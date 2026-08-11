# SPDX-License-Identifier: Apache-2.0
"""Session-cumulative cost lookup for the spend guard.

The single-request estimator (estimator.py) catches *runaway-prompt* spikes
(e.g. a 1.2M-token request landing at once). But the historical spike on
2026-05-07 09:28-10:56 was death-by-1000-cuts: 384 requests, none above
$1.25 individually, totalling $99.67 in 88 minutes.

To catch that pattern we need session-cumulative awareness. This module
reads the proxy's existing ``~/.tokenpak/monitor.db`` (the wire-side cost
log) and returns the running cost for the given session within a sliding
window. The policy engine adds this to the per-request projection.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)


_SESSION_LEDGER_COLUMNS = (
    "id",
    "timestamp",
    "model",
    "input_tokens",
    "output_tokens",
    "estimated_cost",
    "status_code",
    "cache_read_tokens",
    "cache_creation_tokens",
    "ttl_attribution",
    "session_id",
    "agent_id",
    "reasoning_effort",
    "provider_usage_ref",
    "provider_usage_provider",
    "provider_input_tokens",
    "provider_output_tokens",
    "provider_cache_read_tokens",
    "provider_cache_creation_tokens",
    "provider_usage_source",
    "provider_usage_confidence",
    "cost_basis",
    "pricing_source",
)


@dataclass(frozen=True)
class _SessionLedgerRow:
    """One completed monitor-ledger request used by session economics.

    Values intentionally retain SQLite nullability.  The economics engine,
    rather than this storage adapter, decides whether a nullable series is
    observed, unavailable, or erroneous.
    """

    id: int
    timestamp: object
    model: object
    input_tokens: object
    output_tokens: object
    estimated_cost: object
    status_code: object
    cache_read_tokens: object
    cache_creation_tokens: object
    ttl_attribution: object
    session_id: object
    agent_id: object
    reasoning_effort: object
    provider_usage_ref: object
    provider_usage_provider: object
    provider_input_tokens: object
    provider_output_tokens: object
    provider_cache_read_tokens: object
    provider_cache_creation_tokens: object
    provider_usage_source: object
    provider_usage_confidence: object
    cost_basis: object
    pricing_source: object


@dataclass(frozen=True)
class _SessionLedgerRead:
    """Truth-preserving outcome of a completed-session ledger read."""

    state: str
    rows: tuple[_SessionLedgerRow, ...] = ()
    reason: str = ""


def _path() -> Path:
    from tokenpak._paths import home, monitor_db

    result = monitor_db(mode="read")
    if result is not None:
        return result
    return home() / "monitor.db"


def session_cumulative_cost(
    session_id: str,
    *,
    window_seconds: int = 3600,
    monitor_db_path: Optional[str] = None,
) -> float:
    """Sum of recorded ``estimated_cost`` for ``session_id`` in the window.

    Returns 0.0 on any failure (file missing, schema mismatch, empty DB) —
    the guard fails open to per-request mode in that case.

    The proxy writes to ``monitor.db`` AFTER the response lands, so this
    counts only completed requests. In the spike-replay scenario the guard
    sees the running tally accumulating turn by turn and trips the block
    threshold mid-spike.
    """
    if not session_id:
        # Header-less traffic resolves to the '' session key on BOTH the
        # check side and the monitor-row write side. Session-cumulative
        # caps are skipped for it — summing every anonymous request into
        # one pseudo-session would over-block, and a model-name
        # pseudo-session (the old fallback) is worse than none.
        _log.debug(
            "spend_guard.session_state: empty session key — skipping session-cumulative check"
        )
        return 0.0
    p = Path(os.path.expanduser(monitor_db_path)) if monitor_db_path else _path()
    if not p.exists():
        return 0.0
    cutoff_ts = time.time() - window_seconds
    # monitor.db stores timestamp as ISO string. The cutoff_ts above is
    # epoch — convert to ISO for the WHERE clause.
    import datetime as _dt

    cutoff_iso = _dt.datetime.fromtimestamp(cutoff_ts).isoformat()
    try:
        conn = sqlite3.connect(str(p), timeout=5.0)
        try:
            row = conn.execute(
                """SELECT COALESCE(SUM(estimated_cost), 0.0)
                   FROM requests
                   WHERE session_id = ?
                     AND timestamp >= ?""",
                (session_id, cutoff_iso),
            ).fetchone()
        finally:
            conn.close()
        return float(row[0] or 0.0)
    except sqlite3.OperationalError as e:
        _log.debug("spend_guard.session_state: monitor.db query failed: %s", e)
        return 0.0
    except Exception as e:
        _log.debug("spend_guard.session_state: unexpected: %s", e)
        return 0.0


def session_cumulative_cost_from_audit(
    session_id: str,
    *,
    window_seconds: int = 3600,
    audit_db_path: str = "~/.tokenpak/spend_guard.db",
) -> float:
    """Alternative: sum projected cost from the spend_guard audit log.

    Used in tests where monitor.db isn't writable (the audit log captures
    every decision the guard ever made for the session, so summing rows
    where event_type IN ('allow','warn','tip_bypass','replay') gives the
    actual session spend the guard authorized).
    """
    if not session_id:
        return 0.0
    p = Path(os.path.expanduser(audit_db_path))
    if not p.exists():
        return 0.0
    cutoff_ts = time.time() - window_seconds
    try:
        conn = sqlite3.connect(str(p), timeout=5.0)
        try:
            row = conn.execute(
                """SELECT COALESCE(SUM(projected_cost_usd), 0.0)
                   FROM spend_guard_audit
                   WHERE session_id = ?
                     AND ts >= ?
                     AND event_type IN ('allow','warn','tip_bypass','replay')""",
                (session_id, cutoff_ts),
            ).fetchone()
        finally:
            conn.close()
        return float(row[0] or 0.0)
    except Exception as e:
        _log.debug("spend_guard.session_state: audit query failed: %s", e)
        return 0.0


def _read_completed_session_rows(
    session_id: str,
    *,
    monitor_db_path: Optional[str] = None,
) -> _SessionLedgerRead:
    """Read completed request rows for one stable session identity.

    A missing database/table is a legitimate ``no_data`` state on a fresh
    install.  A present but incompatible or unreadable ledger is ``error``;
    it must never be represented as an empty, zero-cost session.  Recorded
    HTTP responses in the 2xx-5xx range are completed events.  Rows without
    a completed response code remain outside the turn ledger.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        return _SessionLedgerRead("no_data", reason="stable session identity is missing")

    p = Path(os.path.expanduser(monitor_db_path)) if monitor_db_path else _path()
    if not p.exists():
        return _SessionLedgerRead("no_data", reason="monitor ledger does not exist")

    conn: sqlite3.Connection | None = None
    try:
        conn = sqlite3.connect(str(p), timeout=5.0)
        conn.row_factory = sqlite3.Row
        table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'requests'"
        ).fetchone()
        if table is None:
            return _SessionLedgerRead("no_data", reason="monitor ledger has no requests table")

        available = {str(row[1]) for row in conn.execute("PRAGMA table_info(requests)").fetchall()}
        missing = sorted(set(_SESSION_LEDGER_COLUMNS) - available)
        if missing:
            return _SessionLedgerRead(
                "error",
                reason="monitor ledger schema is missing columns: " + ", ".join(missing),
            )

        columns = ", ".join(_SESSION_LEDGER_COLUMNS)
        raw_rows = conn.execute(
            f"SELECT {columns} FROM requests "
            "WHERE session_id = ? AND status_code BETWEEN 200 AND 599 "
            "ORDER BY timestamp ASC, id ASC",
            (session_id.strip(),),
        ).fetchall()
        if not raw_rows:
            return _SessionLedgerRead("no_data", reason="session has no completed request rows")

        rows = tuple(
            _SessionLedgerRow(**{column: row[column] for column in _SESSION_LEDGER_COLUMNS})
            for row in raw_rows
        )
        return _SessionLedgerRead("observed", rows=rows)
    except sqlite3.Error as exc:
        _log.warning("spend_guard.session_state: session ledger read failed: %s", exc)
        return _SessionLedgerRead(
            "error",
            reason=f"monitor ledger read failed: {type(exc).__name__}: {exc}",
        )
    except Exception as exc:
        _log.warning("spend_guard.session_state: unexpected session ledger failure: %s", exc)
        return _SessionLedgerRead(
            "error",
            reason=f"unexpected monitor ledger failure: {type(exc).__name__}: {exc}",
        )
    finally:
        if conn is not None:
            conn.close()


__all__ = [
    "session_cumulative_cost",
    "session_cumulative_cost_from_audit",
]
