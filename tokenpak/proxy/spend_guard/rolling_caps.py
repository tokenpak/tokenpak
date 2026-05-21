"""Rolling/cumulative spend caps — supplements the per-session cap.

The per-session cap (`session_block_cost_usd`) catches a single session
that runs away. The 2026-05-15 incident proved that 64 well-bounded
sub-cap sessions can still cumulate to $566+ over 8 hours because the
session cap doesn't see cross-session totals. This module adds rolling
cumulative caps:

    per-agent  : max cost / tokens / cache_read per hour
    per-fleet  : max cost / tokens / cache_read per hour

If any cap would be exceeded by the projected cost of THIS request, the
guard returns a block with error.type=tokenpak_spend_guard_rolling_cap_blocked.

Design notes:

- Reads existing monitor.db columns only (timestamp, input_tokens,
  output_tokens, cache_read_tokens, estimated_cost, session_id). No
  schema change.
- Agent attribution comes from the X-Tokenpak-Agent request header set
  by agent-claude-worker.sh. Sessions-without-header are bucketed to
  "unknown" and only the fleet-wide cap restrains them.
- Session→agent mapping is maintained in-memory as requests flow.
  After proxy restart, the mapping resets — that's degraded but safe
  (fail-open: under-count briefly, never over-block).
- A 30-second result cache limits monitor.db query load when many
  requests arrive in burst.
- Existing per-session cap behavior is UNCHANGED.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

_DEFAULT_MONITOR_DB = "~/.tokenpak/monitor.db"

# In-memory session→agent mapping. Populated as the proxy sees requests
# (orchestrator calls record_session_agent at evaluate-time). Cleared
# only on proxy restart. Bounded growth — sessions are TTL'd at the
# window boundary on each lookup.
_SESSION_AGENT_LOCK = threading.Lock()
_SESSION_AGENT: dict[str, tuple[str, float]] = {}  # session_id → (agent_id, last_seen_epoch)

# Result cache for rolling-usage queries — 30s TTL.
_USAGE_CACHE_LOCK = threading.Lock()
_USAGE_CACHE: dict[str, tuple[float, dict]] = {}  # key → (expires_at, usage_dict)
_USAGE_CACHE_TTL_SEC = 30.0


@dataclass
class RollingCapsConfig:
    """Rolling-cap settings. All fields editable via SpendGuardConfig."""

    enabled: bool = True
    window_seconds: int = 3600

    # Per-agent caps
    per_agent_max_cost_usd: float = 20.0
    per_agent_max_tokens_total: int = 5_000_000
    per_agent_max_cache_read_tokens: int = 4_000_000

    # Per-fleet (all agents combined) caps
    per_fleet_max_cost_usd: float = 60.0
    per_fleet_max_tokens_total: int = 15_000_000
    per_fleet_max_cache_read_tokens: int = 12_000_000


@dataclass
class CapBreach:
    """A rolling-cap evaluation result indicating the request must block."""

    cap_dimension: str          # e.g. "per_agent_cost_usd", "per_fleet_cache_read_tokens"
    agent_id: str
    window_seconds: int
    used: float                 # current usage (cost in USD or tokens as int)
    cap: float                  # configured cap
    projected_add: float        # what THIS request would add
    retry_after_seconds: int    # seconds until enough usage ages out


def record_session_agent(session_id: str, agent_id: str) -> None:
    """Record the (session_id → agent_id) mapping for future per-agent lookup.

    Called once per request at proxy entry (after the existing
    session_id resolution + header parse). No-op for empty inputs.
    """
    if not session_id or not agent_id:
        return
    with _SESSION_AGENT_LOCK:
        _SESSION_AGENT[session_id] = (agent_id.lower(), time.time())


def _path(monitor_db_path: Optional[str]) -> Path:
    return Path(os.path.expanduser(monitor_db_path or _DEFAULT_MONITOR_DB))


def _get_agents_for_window(window_seconds: int) -> dict[str, list[str]]:
    """Return {agent_id: [session_id, ...]} for sessions seen in the window.

    Sessions without a recorded mapping (e.g. pre-restart) are excluded.
    """
    cutoff = time.time() - float(window_seconds)
    out: dict[str, list[str]] = {}
    with _SESSION_AGENT_LOCK:
        for sid, (agent, last_seen) in list(_SESSION_AGENT.items()):
            if last_seen < cutoff:
                # Prune stale entries
                _SESSION_AGENT.pop(sid, None)
                continue
            out.setdefault(agent, []).append(sid)
    return out


def compute_rolling_usage(
    agent_id: str,
    window_seconds: int,
    *,
    monitor_db_path: Optional[str] = None,
) -> dict:
    """Compute rolling-window usage for ONE agent + the whole fleet.

    Returns:
        {
          "agent_cost_usd": float,
          "agent_tokens_total": int,
          "agent_cache_read_tokens": int,
          "fleet_cost_usd": float,
          "fleet_tokens_total": int,
          "fleet_cache_read_tokens": int,
        }

    Cached for 30 seconds keyed by (agent_id, window_seconds, db_path).
    Returns all-zero on any failure (fail open).
    """
    db_path = str(_path(monitor_db_path))
    cache_key = f"{agent_id}|{window_seconds}|{db_path}"

    now = time.time()
    with _USAGE_CACHE_LOCK:
        cached = _USAGE_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return cached[1]

    blank = {
        "agent_cost_usd": 0.0,
        "agent_tokens_total": 0,
        "agent_cache_read_tokens": 0,
        "fleet_cost_usd": 0.0,
        "fleet_tokens_total": 0,
        "fleet_cache_read_tokens": 0,
    }
    p = _path(monitor_db_path)
    if not p.exists():
        return blank

    cutoff_iso = time.strftime(
        "%Y-%m-%dT%H:%M:%S", time.localtime(time.time() - float(window_seconds))
    )
    try:
        conn = sqlite3.connect(str(p), timeout=2.0)
        # Fleet-wide totals.
        # tokens_total = input + output (cache_read EXCLUDED
        # 2026-05-15: Anthropic bills cache_read ~90% cheaper, so cache_read
        # inflation should not trip the rolling tokens cap. cache_read is
        # still recorded for observability + its own dedicated cap.
        row = conn.execute(
            """SELECT COALESCE(SUM(estimated_cost), 0.0),
                      COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0),
                      COALESCE(SUM(cache_read_tokens), 0)
               FROM requests
               WHERE timestamp >= ?""",
            (cutoff_iso,),
        ).fetchone()
        fleet_cost, fleet_tokens, fleet_cache_read = float(row[0]), int(row[1]), int(row[2])

        # Per-agent totals — restrict to sessions the proxy has mapped
        # to this agent. Sessions with no mapping count toward fleet
        # only (handled by the fleet query above).
        agent_cost = 0.0
        agent_tokens = 0
        agent_cache_read = 0
        if agent_id:
            mapping = _get_agents_for_window(window_seconds)
            sessions = mapping.get(agent_id.lower(), [])
            if sessions:
                placeholders = ",".join("?" for _ in sessions)
                row2 = conn.execute(
                    f"""SELECT COALESCE(SUM(estimated_cost), 0.0),
                              COALESCE(SUM(input_tokens), 0) + COALESCE(SUM(output_tokens), 0),
                              COALESCE(SUM(cache_read_tokens), 0)
                       FROM requests
                       WHERE timestamp >= ?
                         AND session_id IN ({placeholders})""",
                    (cutoff_iso, *sessions),
                ).fetchone()
                agent_cost, agent_tokens, agent_cache_read = float(row2[0]), int(row2[1]), int(row2[2])
        conn.close()
        usage = {
            "agent_cost_usd": agent_cost,
            "agent_tokens_total": agent_tokens,
            "agent_cache_read_tokens": agent_cache_read,
            "fleet_cost_usd": fleet_cost,
            "fleet_tokens_total": fleet_tokens,
            "fleet_cache_read_tokens": fleet_cache_read,
        }
        with _USAGE_CACHE_LOCK:
            _USAGE_CACHE[cache_key] = (now + _USAGE_CACHE_TTL_SEC, usage)
        return usage
    except sqlite3.OperationalError as e:
        _log.debug("rolling_caps: monitor.db query failed: %s", e)
        return blank
    except Exception as e:
        _log.debug("rolling_caps: unexpected error: %s", e)
        return blank


def check_rolling_caps(
    agent_id: str,
    projected_cost_usd: float,
    projected_input_tokens: int,
    projected_output_tokens: int,
    projected_cache_read_tokens: int,
    config: RollingCapsConfig,
    *,
    monitor_db_path: Optional[str] = None,
) -> Optional[CapBreach]:
    """Evaluate all configured rolling caps; return the FIRST breach or None.

    The check order (matches packet doc): per-agent cost → per-agent
    tokens → per-agent cache_read → per-fleet cost → per-fleet tokens
    → per-fleet cache_read. First breach wins so the error message
    pinpoints the tightest constraint.

    Returns None when:
        - Rolling caps are disabled
        - Usage is below all configured caps (with projected_add included)
        - Any computation error (fail-open per Standard 29 §9.8)
    """
    if not config.enabled:
        return None
    usage = compute_rolling_usage(
        agent_id, config.window_seconds, monitor_db_path=monitor_db_path
    )
    # tokens_total = input + output only (cache_read EXCLUDED
    # 2026-05-15: cache_read is ~90% cheaper and inflates the count without
    # reflecting real cost. cache_read keeps its own dedicated cap dimension.
    projected_tokens_total = (
        int(projected_input_tokens) + int(projected_output_tokens)
    )

    def retry_after(cost_used: float, tokens_used: float, cap: float) -> int:
        # Coarse heuristic: time until the oldest in-window request ages
        # out. We don't have per-row aging info here; return a flat 30
        # min for now, the operator can re-try after.
        return 1800

    # Per-agent — only when agent_id is known
    if agent_id:
        a_cost = usage["agent_cost_usd"]
        if config.per_agent_max_cost_usd > 0 and a_cost + projected_cost_usd > config.per_agent_max_cost_usd:
            return CapBreach(
                cap_dimension="per_agent_cost_usd",
                agent_id=agent_id, window_seconds=config.window_seconds,
                used=a_cost, cap=config.per_agent_max_cost_usd,
                projected_add=projected_cost_usd,
                retry_after_seconds=retry_after(a_cost, 0, config.per_agent_max_cost_usd),
            )
        a_tok = usage["agent_tokens_total"]
        if config.per_agent_max_tokens_total > 0 and a_tok + projected_tokens_total > config.per_agent_max_tokens_total:
            return CapBreach(
                cap_dimension="per_agent_tokens_total",
                agent_id=agent_id, window_seconds=config.window_seconds,
                used=float(a_tok), cap=float(config.per_agent_max_tokens_total),
                projected_add=float(projected_tokens_total),
                retry_after_seconds=retry_after(0, a_tok, config.per_agent_max_tokens_total),
            )
        a_cr = usage["agent_cache_read_tokens"]
        if config.per_agent_max_cache_read_tokens > 0 and a_cr + projected_cache_read_tokens > config.per_agent_max_cache_read_tokens:
            return CapBreach(
                cap_dimension="per_agent_cache_read_tokens",
                agent_id=agent_id, window_seconds=config.window_seconds,
                used=float(a_cr), cap=float(config.per_agent_max_cache_read_tokens),
                projected_add=float(projected_cache_read_tokens),
                retry_after_seconds=retry_after(0, a_cr, config.per_agent_max_cache_read_tokens),
            )

    # Per-fleet — applies whether or not agent is known
    f_cost = usage["fleet_cost_usd"]
    if config.per_fleet_max_cost_usd > 0 and f_cost + projected_cost_usd > config.per_fleet_max_cost_usd:
        return CapBreach(
            cap_dimension="per_fleet_cost_usd",
            agent_id=agent_id or "unknown", window_seconds=config.window_seconds,
            used=f_cost, cap=config.per_fleet_max_cost_usd,
            projected_add=projected_cost_usd,
            retry_after_seconds=retry_after(f_cost, 0, config.per_fleet_max_cost_usd),
        )
    f_tok = usage["fleet_tokens_total"]
    if config.per_fleet_max_tokens_total > 0 and f_tok + projected_tokens_total > config.per_fleet_max_tokens_total:
        return CapBreach(
            cap_dimension="per_fleet_tokens_total",
            agent_id=agent_id or "unknown", window_seconds=config.window_seconds,
            used=float(f_tok), cap=float(config.per_fleet_max_tokens_total),
            projected_add=float(projected_tokens_total),
            retry_after_seconds=retry_after(0, f_tok, config.per_fleet_max_tokens_total),
        )
    f_cr = usage["fleet_cache_read_tokens"]
    if config.per_fleet_max_cache_read_tokens > 0 and f_cr + projected_cache_read_tokens > config.per_fleet_max_cache_read_tokens:
        return CapBreach(
            cap_dimension="per_fleet_cache_read_tokens",
            agent_id=agent_id or "unknown", window_seconds=config.window_seconds,
            used=float(f_cr), cap=float(config.per_fleet_max_cache_read_tokens),
            projected_add=float(projected_cache_read_tokens),
            retry_after_seconds=retry_after(0, f_cr, config.per_fleet_max_cache_read_tokens),
        )
    return None


def reset_caches_for_testing() -> None:
    """Test-only — clear in-memory caches between test runs."""
    with _SESSION_AGENT_LOCK:
        _SESSION_AGENT.clear()
    with _USAGE_CACHE_LOCK:
        _USAGE_CACHE.clear()
