# SPDX-License-Identifier: Apache-2.0
"""Durable write-ahead ledger for in-flight upstream (provider) calls.

Problem this closes
--------------------
``upstream_retry.py`` already tags a request with a ``tip_plan_id`` and, when
the *same process* observes an upstream failure, returns a structured
``TIPError``/``recovery_status`` payload instead of a bare error. That
machinery is entirely in-memory: it only runs code that is still executing.
If the TokenPak proxy process itself dies mid-request — OOM-kill, a deploy,
``systemctl restart``, any ordinary operational event for a long-running
service — none of that in-process handling ever runs. The client just sees
its connection reset, with no ledger anywhere recording that a plan was ever
in flight, so a fresh process has no way to tell "restart interrupted a live
request" apart from "nothing was happening."

This module adds exactly one durable fact: a row, keyed by ``tip_plan_id``,
that is written *before* the upstream call is dispatched (write-ahead) and
updated once the outcome is known. On process startup, before the listener
accepts a single connection, :func:`recover_orphaned_plans` sweeps rows still
marked ``in_flight`` — since this process has not yet served any request,
every such row necessarily belongs to a process that died mid-call — and
marks them ``failed`` with ``failure_reason="proxy_restart_detected"``. If
the client later retries with the same ``tip_plan_id``,
:func:`check_restart_recovered_failure` lets the request handler answer with
an explicit terminal-failure signal (the same
``build_terminal_recovery_payload`` shape ``upstream_retry.py`` already uses)
instead of silently attempting a stale, possibly half-consumed request.

What this deliberately does NOT do
-----------------------------------
Full transparent replay of a provider call that was mid-stream when the
proxy died is out of scope here — resuming a partially-streamed completion
mid-token is not something this module attempts, and it is not safe to fake.
This ledger's job stops at *detecting* the restart-interrupted plan and
*signaling* it cleanly; it never re-issues the original upstream call.

This is also intentionally narrower than a full exactly-once/durable-resume
system: there is no multi-stage intent/sent/acked/terminal/receipt state
machine here, no idempotency-key derivation, and no resume-disposition enum.
Only the minimum shape needed to turn "raw connection reset" into "an
explicit, ledger-backed failure signal" is implemented:

- ``tip_plan_id``            — the same identifier ``upstream_retry.py`` uses.
- ``request_hash``           — sha256 of the outbound request bytes (the same
                                formula an adjacent durable-resume proposal
                                defines for its own, broader ``request_hash``
                                field: sha256 of the exact provider-bound
                                bytes). Used here only for diagnostic/identity
                                purposes, not as part of an idempotency key.
- ``status``                 — ``in_flight`` / ``completed`` / ``failed``.
- ``failure_reason``         — free-form short token; restart-detected rows
                                use ``proxy_restart_detected``.

DB layout follows the ``spend_guard.db`` convention (see
``proxy/spend_guard/pending.py``): one SQLite file under the TokenPak home
directory, lazy ``CREATE TABLE IF NOT EXISTS``, a fresh ``sqlite3.connect``
per call (no pool — this is a low-volume write-ahead log, not a hot
telemetry path), WAL + busy_timeout so a concurrent reader/writer never
raises "database is locked" into the request path.

Fails open, always. Every public function swallows ``sqlite3.Error`` and
``OSError`` and returns a harmless default — a ledger outage must never turn
into a proxy outage. Disable entirely with
``TOKENPAK_EXECUTION_LEDGER_ENABLED=0``.
"""

from __future__ import annotations

import hashlib
import logging
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Restart-recovered rows carry this failure_reason so the request-ingress
# check can tell "this plan died with the previous process" apart from any
# other terminal failure recorded by the normal in-process error path.
RESTART_FAILURE_REASON = "proxy_restart_detected"

# How long a recovered/failed row is kept before it is eligible for cleanup
# on the next startup sweep. Generous on purpose — this is a low-volume
# table and the cost of keeping a stale row an extra day is nothing next to
# the cost of deleting one a legitimate slow retry still needed.
_DEFAULT_RETENTION_SECONDS = 7 * 24 * 3600.0

_BUSY_WAIT_SEC = 20.0


def _enabled() -> bool:
    return os.environ.get("TOKENPAK_EXECUTION_LEDGER_ENABLED", "1") != "0"


def _db_path() -> Path:
    raw = os.environ.get("TOKENPAK_EXECUTION_LEDGER_DB")
    if raw:
        path = Path(os.path.expanduser(raw))
    else:
        from tokenpak._paths import write_under

        path = write_under("execution_ledger.db")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    return path


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=_BUSY_WAIT_SEC)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(_BUSY_WAIT_SEC * 1000)}")
    except sqlite3.Error:
        pass
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return conn


_SCHEMA_READY_LOCK = threading.Lock()
_SCHEMA_READY: set[str] = set()


def _ensure_schema_once(conn: sqlite3.Connection, path: Path) -> None:
    key = str(path)
    if key in _SCHEMA_READY:
        return
    with _SCHEMA_READY_LOCK:
        if key in _SCHEMA_READY:
            return
        try:
            conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.Error:
            pass
        _ensure_schema(conn)
        _SCHEMA_READY.add(key)


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS execution_plans (
            tip_plan_id TEXT PRIMARY KEY,
            request_id TEXT NOT NULL DEFAULT '',
            request_hash TEXT NOT NULL DEFAULT '',
            target_url TEXT NOT NULL DEFAULT '',
            stream_started INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'in_flight',
            failure_reason TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            recovered_at REAL,
            acknowledged INTEGER NOT NULL DEFAULT 0,
            pid INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_execution_plans_status ON execution_plans(status)")
    conn.commit()


def hash_request(body: Optional[bytes]) -> str:
    """sha256 of the outbound request bytes — identity only, not a key."""
    return hashlib.sha256(body or b"").hexdigest()


# ---------------------------------------------------------------------------
# Write-ahead API
# ---------------------------------------------------------------------------


def begin_plan(
    tip_plan_id: str,
    *,
    request_id: str = "",
    request_hash: str = "",
    target_url: str = "",
    stream_started: bool = False,
) -> None:
    """Durably record that ``tip_plan_id`` is about to be sent upstream.

    Must be called BEFORE the upstream call is dispatched — that ordering is
    what makes this a write-ahead log: if the process dies between this call
    and the matching :func:`complete_plan`/:func:`fail_plan`, the row is left
    ``in_flight`` and the next startup's :func:`recover_orphaned_plans` finds
    it.
    """
    if not _enabled() or not tip_plan_id:
        return
    now = time.time()
    try:
        conn = _connect(_db_path())
        try:
            _ensure_schema_once(conn, _db_path())
            conn.execute(
                """INSERT INTO execution_plans
                       (tip_plan_id, request_id, request_hash, target_url,
                        stream_started, status, failure_reason,
                        created_at, updated_at, recovered_at, acknowledged, pid)
                   VALUES (?, ?, ?, ?, ?, 'in_flight', '', ?, ?, NULL, 0, ?)
                   ON CONFLICT(tip_plan_id) DO UPDATE SET
                       request_id=excluded.request_id,
                       request_hash=excluded.request_hash,
                       target_url=excluded.target_url,
                       stream_started=excluded.stream_started,
                       status='in_flight',
                       failure_reason='',
                       created_at=excluded.created_at,
                       updated_at=excluded.updated_at,
                       recovered_at=NULL,
                       acknowledged=0,
                       pid=excluded.pid""",
                (
                    tip_plan_id,
                    request_id,
                    request_hash,
                    target_url,
                    1 if stream_started else 0,
                    now,
                    now,
                    os.getpid(),
                ),
            )
            conn.commit()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        logger.warning("execution_ledger: begin_plan failed (fail-open)", exc_info=True)


def complete_plan(tip_plan_id: str) -> None:
    """Mark ``tip_plan_id`` as cleanly completed. Idempotent, fail-open."""
    if not _enabled() or not tip_plan_id:
        return
    try:
        conn = _connect(_db_path())
        try:
            _ensure_schema_once(conn, _db_path())
            conn.execute(
                "UPDATE execution_plans SET status='completed', updated_at=? "
                "WHERE tip_plan_id=? AND status='in_flight'",
                (time.time(), tip_plan_id),
            )
            conn.commit()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        logger.warning("execution_ledger: complete_plan failed (fail-open)", exc_info=True)


def fail_plan(tip_plan_id: str, *, reason: str) -> None:
    """Mark ``tip_plan_id`` as failed within the same process that began it.

    A no-op if the row is already ``completed``/``failed`` (the ``WHERE
    status='in_flight'`` guard), so this is safe to call defensively from a
    catch-all exit path without clobbering an outcome recorded earlier.
    """
    if not _enabled() or not tip_plan_id:
        return
    try:
        conn = _connect(_db_path())
        try:
            _ensure_schema_once(conn, _db_path())
            conn.execute(
                "UPDATE execution_plans SET status='failed', failure_reason=?, "
                "updated_at=? WHERE tip_plan_id=? AND status='in_flight'",
                (reason[:200], time.time(), tip_plan_id),
            )
            conn.commit()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        logger.warning("execution_ledger: fail_plan failed (fail-open)", exc_info=True)


# ---------------------------------------------------------------------------
# Startup recovery pass
# ---------------------------------------------------------------------------


def recover_orphaned_plans(
    *, retention_seconds: float = _DEFAULT_RETENTION_SECONDS
) -> list[dict[str, Any]]:
    """Startup recovery pass — call once, before the listener accepts requests.

    Any row still ``in_flight`` at this point belongs to a process other than
    this one (this process has not yet dispatched a single upstream call),
    which can only mean that process died mid-request. Each such row is
    marked ``failed`` / ``proxy_restart_detected`` so a subsequent client
    retry carrying the same ``tip_plan_id`` gets an explicit signal instead
    of TokenPak silently attempting a stale, possibly half-consumed request.

    Also opportunistically purges old completed/failed rows past
    ``retention_seconds`` so the table does not grow without bound.

    Returns the list of recovered plan records (for startup logging). Bounded
    by a single table scan plus a bulk UPDATE — well within the ≤5s recovery
    decision window even on a modest developer machine.
    """
    if not _enabled():
        return []
    recovered: list[dict[str, Any]] = []
    try:
        conn = _connect(_db_path())
        try:
            _ensure_schema_once(conn, _db_path())
            now = time.time()
            rows = conn.execute("SELECT * FROM execution_plans WHERE status='in_flight'").fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE execution_plans SET status='failed', "
                    "failure_reason=?, updated_at=?, recovered_at=? "
                    "WHERE tip_plan_id=? AND status='in_flight'",
                    (RESTART_FAILURE_REASON, now, now, row["tip_plan_id"]),
                )
                recovered.append(
                    {
                        "tip_plan_id": row["tip_plan_id"],
                        "request_id": row["request_id"],
                        "request_hash": row["request_hash"],
                        "target_url": row["target_url"],
                        "stream_started": bool(row["stream_started"]),
                        "started_at": row["created_at"],
                        "recovered_at": now,
                        "orphaned_pid": row["pid"],
                    }
                )
            if retention_seconds > 0:
                cutoff = now - retention_seconds
                conn.execute(
                    "DELETE FROM execution_plans WHERE status IN ('completed','failed') "
                    "AND updated_at < ?",
                    (cutoff,),
                )
            conn.commit()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        logger.warning("execution_ledger: recover_orphaned_plans failed (fail-open)", exc_info=True)
        return []
    return recovered


def check_restart_recovered_failure(tip_plan_id: str) -> Optional[dict[str, Any]]:
    """Return (and consume) a restart-detected terminal failure for this plan.

    Called at request ingress, before dispatch. If a prior process died
    mid-request for this exact ``tip_plan_id`` and the recovery pass already
    marked it terminally failed, the FIRST subsequent request carrying that
    plan id gets the record back (and the row is marked acknowledged so a
    legitimate later reuse of the same header value is not blocked forever).
    Returns ``None`` on no match, on an already-acknowledged row, or on any
    ledger failure (fail-open — never block a request over ledger trouble).
    """
    if not _enabled() or not tip_plan_id:
        return None
    try:
        conn = _connect(_db_path())
        try:
            _ensure_schema_once(conn, _db_path())
            row = conn.execute(
                "SELECT * FROM execution_plans WHERE tip_plan_id=? AND status='failed' "
                "AND failure_reason=? AND acknowledged=0",
                (tip_plan_id, RESTART_FAILURE_REASON),
            ).fetchone()
            if row is None:
                return None
            conn.execute(
                "UPDATE execution_plans SET acknowledged=1 WHERE tip_plan_id=?",
                (tip_plan_id,),
            )
            conn.commit()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        logger.warning(
            "execution_ledger: check_restart_recovered_failure failed (fail-open)",
            exc_info=True,
        )
        return None
    return {
        "tip_plan_id": row["tip_plan_id"],
        "request_id": row["request_id"],
        "request_hash": row["request_hash"],
        "target_url": row["target_url"],
        "stream_started": bool(row["stream_started"]),
        "started_at": row["created_at"],
        "recovered_at": row["recovered_at"],
    }


def lookup_plan(tip_plan_id: str) -> Optional[dict[str, Any]]:
    """Read-only lookup, for diagnostics/tests. Does not mutate state."""
    if not tip_plan_id:
        return None
    try:
        conn = _connect(_db_path())
        try:
            _ensure_schema_once(conn, _db_path())
            row = conn.execute(
                "SELECT * FROM execution_plans WHERE tip_plan_id=?",
                (tip_plan_id,),
            ).fetchone()
        finally:
            conn.close()
    except (sqlite3.Error, OSError):
        return None
    if row is None:
        return None
    return dict(row)


__all__ = [
    "RESTART_FAILURE_REASON",
    "begin_plan",
    "complete_plan",
    "fail_plan",
    "recover_orphaned_plans",
    "check_restart_recovered_failure",
    "lookup_plan",
    "hash_request",
]
