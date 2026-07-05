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
from pathlib import Path
from typing import Optional

_log = logging.getLogger(__name__)

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
            "spend_guard.session_state: empty session key — "
            "skipping session-cumulative check"
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
        conn = sqlite3.connect(str(p), timeout=2.0)
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
        conn = sqlite3.connect(str(p), timeout=2.0)
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


__all__ = ["session_cumulative_cost", "session_cumulative_cost_from_audit"]
