"""
Cache invalidation alerts (extends the base cache-invalidator from log-only to user-visible).

Adds three things on top of the base detector:
  1. Two additional event types beyond the base cache-invalidator:
       - cache_control_position_changed  (cache_control block moved between requests)
       - mcp_server_added                (mcp_servers field gained an entry)
     `claude_md_modified` is intentionally scoped down to in-request detection
     only — the task escalation note explicitly defers file-watching outside
     the proxy. When CLAUDE.md content rides in the system block, a change is
     surfaced as system_changed (base cache-invalidator contract) rather than introducing a
     misleadingly named separate event.

  2. Hashed before/after of the affected region (sha256 of the canonical
     value) and a best-effort USD estimate of the lost cache savings.

  3. Threshold-gated alert dispatch via tokenpak.alerts.channels — fires when
     estimated_lost_savings_usd >= TOKENPAK_CACHE_INVALIDATION_ALERT_THRESHOLD_USD
     (default 1.0).

Writes are additive: the existing cache_invalidator_events table (base cache-invalidator) keeps
its log-only contract; this module writes a separate cache_invalidations row that
includes hashes + dollar estimate.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
from typing import Any, Dict, List, NamedTuple, Optional

from tokenpak.proxy.cache_invalidator import (
    CacheInvalidatorEvent,
    _detect_cache_invalidators,
)

logger = logging.getLogger(__name__)


DEFAULT_THRESHOLD_USD = 1.0


class CacheInvalidationAlert(NamedTuple):
    event_type: str
    before_hash: str
    after_hash: str
    estimated_lost_savings_usd: float


def _hash_value(value: str) -> str:
    """SHA-256 of a canonical string value, hex-encoded, truncated to 16 chars."""
    if value is None:
        value = ""
    return hashlib.sha256(value.encode("utf-8", errors="replace")).hexdigest()[:16]


def _detect_extra_events(
    prev: Dict[str, Any],
    curr: Dict[str, Any],
) -> List[CacheInvalidatorEvent]:
    """Detect additional event types beyond the base cache-invalidator.

    Returns events that are NOT already produced by `_detect_cache_invalidators`.
    """
    events: List[CacheInvalidatorEvent] = []

    # cache_control_position_changed — list of indices where a cache_control
    # block appears in the system array. A change in indices signals a moved
    # marker even when the system text itself is identical.
    def _cache_control_positions(body: Dict[str, Any]) -> List[int]:
        positions: List[int] = []
        sys_block = body.get("system")
        if isinstance(sys_block, list):
            for i, item in enumerate(sys_block):
                if isinstance(item, dict) and item.get("cache_control"):
                    positions.append(i)
        return positions

    prev_positions = _cache_control_positions(prev)
    curr_positions = _cache_control_positions(curr)
    if prev_positions != curr_positions:
        events.append(
            CacheInvalidatorEvent(
                "cache_control_position_changed",
                json.dumps(prev_positions),
                json.dumps(curr_positions),
            )
        )

    # mcp_server_added — list of declared mcp_servers names. Only fire when the
    # set GREW (additions invalidate the prefix). Removals are surfaced as
    # tools_changed elsewhere if they alter the tool array.
    def _mcp_server_names(body: Dict[str, Any]) -> List[str]:
        servers = body.get("mcp_servers")
        if isinstance(servers, list):
            names = []
            for s in servers:
                if isinstance(s, dict):
                    n = s.get("name") or s.get("id") or ""
                    if n:
                        names.append(str(n))
            return sorted(names)
        return []

    prev_mcp = set(_mcp_server_names(prev))
    curr_mcp = set(_mcp_server_names(curr))
    if curr_mcp - prev_mcp:  # new server appeared
        events.append(
            CacheInvalidatorEvent(
                "mcp_server_added",
                json.dumps(sorted(prev_mcp)),
                json.dumps(sorted(curr_mcp)),
            )
        )

    return events


def _estimate_invalidated_tokens(
    prev: Dict[str, Any],
    curr: Dict[str, Any],
    event_type: str,
) -> int:
    """Estimate the number of input tokens that lost their cached state.

    Approximation: bytes / 4 ≈ tokens. We use the larger of (prev, curr) for the
    affected region so the estimate is monotone over the change.
    """

    def _bytes_of(obj: Any) -> int:
        try:
            return len(json.dumps(obj, ensure_ascii=False))
        except Exception:
            return 0

    if event_type == "tools_changed":
        size = max(_bytes_of(prev.get("tools", [])), _bytes_of(curr.get("tools", [])))
    elif event_type == "system_changed":
        size = max(_bytes_of(prev.get("system", "")), _bytes_of(curr.get("system", "")))
    elif event_type == "cache_control_position_changed":
        size = max(_bytes_of(prev.get("system", "")), _bytes_of(curr.get("system", "")))
    elif event_type == "mcp_server_added":
        size = max(_bytes_of(prev.get("mcp_servers", [])), _bytes_of(curr.get("mcp_servers", [])))
    else:
        size = max(_bytes_of(prev), _bytes_of(curr))
    return max(0, size // 4)


def _estimate_lost_savings_usd(
    invalidated_tokens: int,
    model: Optional[str],
) -> float:
    """USD estimate of the savings lost when the prefix invalidates.

    Lost = (cache_creation_premium - cache_read_discount) approximated as:
      tokens × (input_rate × 1.25 - cache_read_rate) / 1_000_000

    Where 1.25 is Anthropic's cache_creation multiplier (already in pricing_rates.py).
    """
    if invalidated_tokens <= 0:
        return 0.0
    try:
        from tokenpak.telemetry.pricing_rates import get_rates

        rates = get_rates(model)
        input_rate = float(rates.get("input", 3.0))
        cached_rate = float(rates.get("cached", input_rate * 0.1))
    except Exception:
        input_rate, cached_rate = 3.0, 0.30

    # Cache creation costs 1.25× input; the user pays that premium AND loses
    # the (input - cached) discount they would have gotten on a cache_read.
    creation_premium = invalidated_tokens / 1_000_000 * input_rate * 0.25
    lost_discount = invalidated_tokens / 1_000_000 * (input_rate - cached_rate)
    return round(creation_premium + lost_discount, 6)


def _alert_threshold_usd() -> float:
    raw = os.environ.get("TOKENPAK_CACHE_INVALIDATION_ALERT_THRESHOLD_USD", "")
    if not raw:
        return DEFAULT_THRESHOLD_USD
    try:
        return float(raw)
    except (ValueError, TypeError):
        return DEFAULT_THRESHOLD_USD


def _dashboard_url() -> str:
    return os.environ.get(
        "TOKENPAK_DASHBOARD_URL",
        "http://localhost:17888/dashboard/cache-invalidations",
    )


def _write_cache_invalidations(
    db_path: str,
    session_id: str,
    alerts: List[CacheInvalidationAlert],
) -> None:
    """Insert one row per alert into cache_invalidations. Fail-open."""
    if not alerts:
        return
    try:
        conn = sqlite3.connect(str(db_path))
        for a in alerts:
            conn.execute(
                "INSERT INTO cache_invalidations "
                "(timestamp, session_id, event_type, before_hash, after_hash, estimated_lost_savings_usd) "
                "VALUES (datetime('now'), ?, ?, ?, ?, ?)",
                (
                    session_id,
                    a.event_type,
                    a.before_hash,
                    a.after_hash,
                    a.estimated_lost_savings_usd,
                ),
            )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.debug("cache_invalidations write failed: %s", exc)


def _fire_alerts(
    session_id: str,
    alerts: List[CacheInvalidationAlert],
    threshold: float,
) -> int:
    """Dispatch alerts above threshold via tokenpak.alerts.channels.

    Returns the number of alerts dispatched.
    """
    fired = 0
    if not alerts:
        return 0
    try:
        from tokenpak.alerts import channels as _channels
    except Exception as exc:
        logger.debug("alert channels import failed: %s", exc)
        return 0

    dashboard_url = _dashboard_url()
    for a in alerts:
        if a.estimated_lost_savings_usd < threshold:
            continue
        # Body fields go through dispatch's **extra — keys must not collide
        # with the positional/keyword args of dispatch(event, severity, message).
        body = {
            "session_id": session_id,
            "event_type": a.event_type,
            "before_hash": a.before_hash,
            "after_hash": a.after_hash,
            "estimated_lost_savings_usd": a.estimated_lost_savings_usd,
            "dashboard_url": dashboard_url,
        }
        message = (
            f"Cache invalidation: {a.event_type} on session {session_id} — "
            f"~${a.estimated_lost_savings_usd:.2f} lost. "
            f"Dashboard: {dashboard_url}"
        )
        try:
            _channels.dispatch(
                event="cache_invalidation",
                severity="warn",
                message=message,
                **body,
            )
            fired += 1
        except Exception as exc:
            logger.debug("alert dispatch failed: %s", exc)
    return fired


def detect_and_alert(
    db_path: str,
    session_id: str,
    prev_body: bytes,
    curr_body: bytes,
    model: Optional[str] = None,
) -> List[CacheInvalidationAlert]:
    """Top-level entrypoint. Detect all event types, persist + alert.

    Returns the list of CacheInvalidationAlert records created. Empty list if
    no invalidation events were detected.
    """
    if not prev_body or not curr_body:
        return []

    try:
        prev = json.loads(prev_body)
        curr = json.loads(curr_body)
    except (json.JSONDecodeError, ValueError, UnicodeDecodeError):
        return []

    base_events = _detect_cache_invalidators(prev_body, curr_body)
    extra_events = _detect_extra_events(prev, curr)
    all_events = base_events + extra_events
    if not all_events:
        return []

    alerts: List[CacheInvalidationAlert] = []
    for ev in all_events:
        invalidated_tokens = _estimate_invalidated_tokens(prev, curr, ev.event_type)
        usd = _estimate_lost_savings_usd(invalidated_tokens, model)
        alerts.append(
            CacheInvalidationAlert(
                event_type=ev.event_type,
                before_hash=_hash_value(ev.before_value),
                after_hash=_hash_value(ev.after_value),
                estimated_lost_savings_usd=usd,
            )
        )

    _write_cache_invalidations(db_path, session_id, alerts)
    threshold = _alert_threshold_usd()
    _fire_alerts(session_id, alerts, threshold)
    return alerts
