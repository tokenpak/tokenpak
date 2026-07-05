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
  for attribution only (per-agent under-count briefly).
- Usage MEASUREMENT fails closed: if the usage DB exists but cannot be
  read (locked/corrupt/unreadable), caps block rather than evaluating
  against a phantom $0. A missing DB on a fresh install still allows.
- A 30-second result cache limits monitor.db query load when many
  requests arrive in burst. In-flight (pending) spend is tracked in a
  process-local counter that BYPASSES this cache, so concurrent
  admissions are visible to each other. Process-local is acceptable:
  a single proxy process per host is the normal deployment shape; a
  multi-process deployment would under-count in-flight spend across
  processes (DB-recorded usage is still shared).
- Existing per-session cap behavior is UNCHANGED.
"""

from __future__ import annotations

import logging
import os
import secrets
import sqlite3
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

# Historical default, kept as a sentinel: when ``_DEFAULT_MONITOR_DB`` still
# equals this literal, the shared resolver (tokenpak._paths.monitor_db) picks
# the real DB path; when a test/config has patched ``_DEFAULT_MONITOR_DB``,
# the override is honored verbatim.
_LEGACY_DEFAULT_MONITOR_DB = "~/.tokenpak/monitor.db"
_DEFAULT_MONITOR_DB = _LEGACY_DEFAULT_MONITOR_DB

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

# ---------------------------------------------------------------------------
# In-flight (pending) spend accounting — closes the check-then-spend window.
#
# monitor.db rows are written only AFTER a response lands, and the usage
# query above is cached for 30s — so N concurrent requests would all pass
# the cap against the same frozen snapshot, overshooting by roughly
# concurrency × per-request projected cost plus a cache-TTL of admissions.
#
# Each admitted request registers its PROJECTED cost/tokens here at
# admission time and is settled (removed) once its actual cost is recorded
# (or the request fails). The totals are computed FRESH on every cap check
# (never cached), so concurrent in-flight spend is visible immediately.
#
# Process-local by design: single-proxy-process deployments are the norm.
# Entries also carry a TTL so a crashed/never-settled request cannot
# permanently inflate the counter.
# ---------------------------------------------------------------------------
_INFLIGHT_LOCK = threading.Lock()
# ticket → (agent_id, cost_usd, tokens_total, cache_read_tokens, admitted_at)
_INFLIGHT: dict[str, tuple[str, float, int, int, float]] = {}
_INFLIGHT_TTL_SEC = 600.0

# Serializes check+admit so two concurrent requests cannot both pass the
# same cap headroom before either registers its pending spend.
_ADMISSION_LOCK = threading.Lock()


def admit_pending_spend(
    agent_id: str,
    projected_cost_usd: float,
    projected_tokens_total: int,
    projected_cache_read_tokens: int,
) -> str:
    """Register a request's projected spend as in-flight. Returns a ticket."""
    ticket = "adm_" + secrets.token_hex(8)
    now = time.time()
    with _INFLIGHT_LOCK:
        _INFLIGHT[ticket] = (
            (agent_id or "").lower(),
            float(projected_cost_usd),
            int(projected_tokens_total),
            int(projected_cache_read_tokens),
            now,
        )
    return ticket


def settle_pending_spend(ticket: Optional[str]) -> bool:
    """Remove an in-flight entry (response landed or request failed).

    Idempotent — settling an unknown/already-settled ticket is a no-op.
    """
    if not ticket:
        return False
    with _INFLIGHT_LOCK:
        return _INFLIGHT.pop(ticket, None) is not None


def _pending_spend_totals(agent_id: str) -> dict:
    """Sum non-expired in-flight spend for ``agent_id`` and the fleet.

    Computed fresh on every call — deliberately NOT behind the usage cache.
    """
    cutoff = time.time() - _INFLIGHT_TTL_SEC
    agent_key = (agent_id or "").lower()
    totals = {
        "agent_cost_usd": 0.0,
        "agent_tokens_total": 0,
        "agent_cache_read_tokens": 0,
        "fleet_cost_usd": 0.0,
        "fleet_tokens_total": 0,
        "fleet_cache_read_tokens": 0,
    }
    with _INFLIGHT_LOCK:
        for ticket, (aid, cost, tokens, cache_read, admitted_at) in list(_INFLIGHT.items()):
            if admitted_at < cutoff:
                # Never settled (crashed request / dropped response path) —
                # expire so it cannot permanently inflate the counter.
                _INFLIGHT.pop(ticket, None)
                continue
            totals["fleet_cost_usd"] += cost
            totals["fleet_tokens_total"] += tokens
            totals["fleet_cache_read_tokens"] += cache_read
            if agent_key and aid == agent_key:
                totals["agent_cost_usd"] += cost
                totals["agent_tokens_total"] += tokens
                totals["agent_cache_read_tokens"] += cache_read
    return totals


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


# Sentinel cap_dimension for "the usage DB exists but could not be read".
# Blocks carry this so operators/agents can distinguish a real cap breach
# from an unmeasurable-usage fail-closed block.
CAP_DIMENSION_UNMEASURABLE = "rolling_cap_unmeasurable"


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


def _warn_unmeasurable(message: str) -> None:
    """Operator-actionable warning — logged AND printed to stderr.

    The stderr line is deliberate: when logging is routed to a file (the
    proxy's normal shape), a silent fail-closed block is the second-worst
    outcome after a silent fail-open one.
    """
    _log.warning(message)
    try:
        print(f"tokenpak: WARN {message}", file=sys.stderr)
    except Exception:
        pass


def _path(monitor_db_path: Optional[str]) -> Path:
    """Resolve the usage-DB path the caps should measure against.

    Uses the shared resolver (``tokenpak._paths.monitor_db``) so caps read
    the SAME file the proxy writes (``~/.tpk`` on fresh installs, legacy
    ``~/.tokenpak`` where that is active) instead of a hardcoded legacy
    path. An explicit argument or a patched ``_DEFAULT_MONITOR_DB``
    (tests/config) wins over the resolver.
    """
    if monitor_db_path:
        return Path(os.path.expanduser(monitor_db_path))
    if _DEFAULT_MONITOR_DB != _LEGACY_DEFAULT_MONITOR_DB:
        return Path(os.path.expanduser(_DEFAULT_MONITOR_DB))
    try:
        from tokenpak._paths import _monitor_db_candidates, monitor_db
        resolved = monitor_db(mode="read")
        if resolved is not None:
            return resolved
        # No candidate passed validation. If one EXISTS but was rejected
        # (corrupt/unreadable), return it so measurement fails closed on
        # read rather than being misread as a fresh install.
        for cand in _monitor_db_candidates():
            if cand.exists():
                return cand
    except Exception as e:  # resolver itself failing must not crash the guard
        _log.debug("rolling_caps: monitor-db resolver failed: %s", e)
    return Path(os.path.expanduser(_DEFAULT_MONITOR_DB))


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
) -> Optional[dict]:
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

    Failure semantics (fail CLOSED on unmeasurable state):
    - Usage DB missing entirely (fresh install, nothing recorded yet):
      returns all-zero usage — allowing is correct, nothing was spent.
    - Usage DB present but unreadable (locked past timeout, corrupt,
      permission error): returns ``None``. Callers must treat None as
      "usage unmeasurable" and BLOCK, because evaluating caps against a
      phantom $0 during exactly the runaway/lock-contention case defeats
      the caps entirely.
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
        _log.info(
            "rolling_caps: usage DB %s does not exist yet (fresh install) — "
            "rolling caps evaluate against zero recorded usage", p,
        )
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
    except sqlite3.Error as e:
        _warn_unmeasurable(
            f"rolling_caps: usage DB {p} exists but could not be read "
            f"({type(e).__name__}: {e}) — rolling-cap usage is UNMEASURABLE "
            "and requests will be blocked (fail closed). Operator action: "
            "repair or remove the usage DB."
        )
        return None
    except Exception as e:
        _warn_unmeasurable(
            f"rolling_caps: unexpected error reading usage DB {p} "
            f"({type(e).__name__}: {e}) — rolling-cap usage is UNMEASURABLE "
            "and requests will be blocked (fail closed)."
        )
        return None


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

    Returns a ``cap_dimension="rolling_cap_unmeasurable"`` breach (fail
    CLOSED) when the usage DB exists but cannot be read — a cap that
    silently evaluates against $0 during a lock/corruption event is no
    cap at all.

    In-flight (admitted but not yet recorded) spend is ADDED to the
    DB-derived usage, so concurrent requests see each other's projected
    spend even within the usage-cache TTL.
    """
    if not config.enabled:
        return None
    usage = compute_rolling_usage(
        agent_id, config.window_seconds, monitor_db_path=monitor_db_path
    )
    if usage is None:
        # Usage DB present but unreadable — measurement failed, block.
        return CapBreach(
            cap_dimension=CAP_DIMENSION_UNMEASURABLE,
            agent_id=agent_id or "unknown",
            window_seconds=config.window_seconds,
            used=0.0,
            cap=0.0,
            projected_add=float(projected_cost_usd),
            retry_after_seconds=60,
        )
    pending = _pending_spend_totals(agent_id)
    if pending["fleet_cost_usd"] or pending["fleet_tokens_total"] or pending["fleet_cache_read_tokens"]:
        usage = {k: usage[k] + pending[k] for k in usage}
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


def check_rolling_caps_and_admit(
    agent_id: str,
    projected_cost_usd: float,
    projected_input_tokens: int,
    projected_output_tokens: int,
    projected_cache_read_tokens: int,
    config: RollingCapsConfig,
    *,
    monitor_db_path: Optional[str] = None,
) -> tuple[Optional[CapBreach], Optional[str]]:
    """Atomically check the caps and, on pass, register in-flight spend.

    Serialized under a module lock so two concurrent requests cannot both
    pass against the same headroom before either registers. Returns
    ``(breach, None)`` on block, ``(None, ticket)`` on admission. The
    caller MUST eventually :func:`settle_pending_spend` the ticket (a TTL
    backstop reclaims dropped tickets).
    """
    with _ADMISSION_LOCK:
        breach = check_rolling_caps(
            agent_id,
            projected_cost_usd,
            projected_input_tokens,
            projected_output_tokens,
            projected_cache_read_tokens,
            config,
            monitor_db_path=monitor_db_path,
        )
        if breach is not None:
            return breach, None
        ticket = admit_pending_spend(
            agent_id,
            projected_cost_usd,
            int(projected_input_tokens) + int(projected_output_tokens),
            projected_cache_read_tokens,
        )
        return None, ticket


def reset_caches_for_testing() -> None:
    """Test-only — clear in-memory caches between test runs."""
    with _SESSION_AGENT_LOCK:
        _SESSION_AGENT.clear()
    with _USAGE_CACHE_LOCK:
        _USAGE_CACHE.clear()
    with _INFLIGHT_LOCK:
        _INFLIGHT.clear()
