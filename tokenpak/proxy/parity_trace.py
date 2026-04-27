# SPDX-License-Identifier: Apache-2.0
"""NCP-3I — in-proxy parity-trace instrumentation.

Captures per-request **lifecycle events** (handler entry, upstream
attempt start / failure, retry boundary, request completion) so
the operator can characterize concurrency-shaped failures that
existing ``tp_events`` telemetry cannot see — specifically the
iter-4 §11 "interp B" condition where TUI retries fire before a
request ever reaches the proxy's completion-time logging path.

**Measurement-only.** Every emit() call is:

  - Gated behind ``TOKENPAK_PARITY_TRACE_ENABLED`` (default ``false``)
  - Off-path: writes happen on the calling thread but every
    failure is swallowed (try/except: pass)
  - Schema-additive: new SQLite table ``tp_parity_trace`` under
    the existing ``$TOKENPAK_HOME/telemetry.db`` (zero new files;
    zero schema mutation of existing tables)
  - Privacy-preserving: structured fields only, no raw prompt /
    secret / credential content reaches any column

When the env var is unset / ``false``, ``emit()`` returns
immediately after one cheap dict lookup. The proxy's hot-path
cost when the trace is disabled is bounded to a single
:func:`os.environ.get` lookup per hook site.

Hook events
-----------

The seven lifecycle phases instrumentation ships with:

  ``handler_entry``           — request entered ``_proxy_to_inner``
  ``request_classified``      — adapter / route / provider resolved
  ``upstream_attempt_start``  — about to call ``pool.stream`` /
                                ``pool.request``
  ``upstream_attempt_failure``— exception propagated past the
                                upstream call
  ``retry_boundary``          — failover engine fired a retry
  ``request_completion``      — handler returned a response cleanly

Every event carries a stable ``trace_id`` (the proxy's per-request
id, available from line 769 of ``server.py``) so multi-event traces
re-assemble at read time.

Privacy contract
----------------

The schema accepts integers, floats, opaque IDs, and a small set
of free-form ``TEXT`` fields with documented allowed values
(``retry_phase``, ``retry_owner``, ``retry_signal``,
``tool_command_first``). The ``notes`` column is the only
free-form sink — callers MUST NOT pass prompt or credential
content there. Privacy tests pin this.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Any, Iterable, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


PARITY_TRACE_ENV: str = "TOKENPAK_PARITY_TRACE_ENABLED"
"""Env-var that gates writes. Read on every emit() call so the
operator can flip it without proxy restart. Truthy values:
``1`` / ``true`` / ``yes`` / ``on`` (case-insensitive)."""


# Lifecycle event types
EVENT_HANDLER_ENTRY: str = "handler_entry"
EVENT_REQUEST_CLASSIFIED: str = "request_classified"
EVENT_UPSTREAM_ATTEMPT_START: str = "upstream_attempt_start"
EVENT_UPSTREAM_ATTEMPT_FAILURE: str = "upstream_attempt_failure"
EVENT_RETRY_BOUNDARY: str = "retry_boundary"
EVENT_REQUEST_COMPLETION: str = "request_completion"

ALL_EVENTS: frozenset = frozenset({
    EVENT_HANDLER_ENTRY,
    EVENT_REQUEST_CLASSIFIED,
    EVENT_UPSTREAM_ATTEMPT_START,
    EVENT_UPSTREAM_ATTEMPT_FAILURE,
    EVENT_RETRY_BOUNDARY,
    EVENT_REQUEST_COMPLETION,
})


# Documented value sets for the "free-form-ish" TEXT columns.
RETRY_PHASES: frozenset = frozenset({
    "initial_user_prompt",
    "post_tool_result",
    "streaming_continuation",
    "message_stop_finalization",
    "unknown",
})

RETRY_OWNERS: frozenset = frozenset({
    "claude_code_client",
    "tokenpak_proxy",
    "upstream_provider",
    "unknown",
})

RETRY_SIGNALS: frozenset = frozenset({
    "429",
    "5xx",
    "timeout",
    "connection_reset",
    "retry_after_header",
    "unknown",
})


_DEFAULT_DB_PATH = (
    Path(os.environ.get("TOKENPAK_HOME", str(Path.home() / ".tokenpak")))
    / "telemetry.db"
)


_DDL = """\
CREATE TABLE IF NOT EXISTS tp_parity_trace (
    trace_id              TEXT NOT NULL,       -- ties events of one request
    event_type            TEXT NOT NULL,       -- one of ALL_EVENTS
    ts                    REAL NOT NULL,       -- epoch seconds (float)
    pid                   INTEGER,             -- os.getpid() at emit time
    ppid                  INTEGER,             -- os.getppid() at emit time
    tokenpak_home         TEXT,                -- $TOKENPAK_HOME or default
    telemetry_db_path     TEXT,                -- the db_path the writer used
    -- Identity
    request_id            TEXT,
    session_id            TEXT,
    provider              TEXT,
    auth_plane            TEXT,
    credential_class      TEXT,
    -- Retry classification (iter-4 dimensions)
    retry_phase           TEXT,                -- one of RETRY_PHASES
    retry_owner           TEXT,                -- one of RETRY_OWNERS
    retry_signal          TEXT,                -- one of RETRY_SIGNALS
    retry_count           INTEGER,
    retry_after_seconds   REAL,
    -- Tool result classification
    tool_command_first    TEXT,
    tool_result_stdout_chars  INTEGER,
    tool_result_stderr_chars  INTEGER,
    tool_result_tokens_est    INTEGER,
    -- Request size
    body_bytes            INTEGER,
    companion_added_chars INTEGER,             -- deferred capture in v1
    intent_guidance_chars INTEGER,             -- deferred capture in v1
    -- Concurrency / lane indicators
    queue_wait_ms         REAL,
    lock_wait_ms          REAL,
    sqlite_write_ms       REAL,
    -- Free-form note (caller-supplied; MUST NOT contain prompt content)
    notes                 TEXT
);
CREATE INDEX IF NOT EXISTS idx_parity_trace_id
    ON tp_parity_trace (trace_id, ts);
CREATE INDEX IF NOT EXISTS idx_parity_event_type
    ON tp_parity_trace (event_type, ts);
CREATE INDEX IF NOT EXISTS idx_parity_session
    ON tp_parity_trace (session_id, ts);
"""


# ---------------------------------------------------------------------------
# Row dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ParityTraceRow:
    """One lifecycle-event row for ``tp_parity_trace``.

    All fields except ``trace_id`` / ``event_type`` / ``ts`` are
    optional and may be ``None``. The schema treats every column
    other than those three as nullable so the same table holds
    rows from different lifecycle phases without each one having
    to populate every dimension.
    """

    trace_id: str
    event_type: str
    ts: float
    pid: Optional[int] = None
    ppid: Optional[int] = None
    tokenpak_home: Optional[str] = None
    telemetry_db_path: Optional[str] = None

    # Identity
    request_id: Optional[str] = None
    session_id: Optional[str] = None
    provider: Optional[str] = None
    auth_plane: Optional[str] = None
    credential_class: Optional[str] = None

    # Retry classification (iter-4)
    retry_phase: Optional[str] = None
    retry_owner: Optional[str] = None
    retry_signal: Optional[str] = None
    retry_count: Optional[int] = None
    retry_after_seconds: Optional[float] = None

    # Tool result classification
    tool_command_first: Optional[str] = None
    tool_result_stdout_chars: Optional[int] = None
    tool_result_stderr_chars: Optional[int] = None
    tool_result_tokens_est: Optional[int] = None

    # Request size
    body_bytes: Optional[int] = None
    companion_added_chars: Optional[int] = None
    intent_guidance_chars: Optional[int] = None

    # Concurrency / lane indicators
    queue_wait_ms: Optional[float] = None
    lock_wait_ms: Optional[float] = None
    sqlite_write_ms: Optional[float] = None

    # Free-form (caller-supplied; MUST NOT contain prompt / secret content)
    notes: Optional[str] = None


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class ParityTraceStore:
    """SQLite writer for ``tp_parity_trace``. Lazy-init."""

    _LOCK = threading.Lock()

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._conn: Optional[sqlite3.Connection] = None

    @property
    def db_path(self) -> Path:
        return self._db_path

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            conn.executescript(_DDL)
            conn.commit()
            self._conn = conn
        return self._conn

    def write(self, row: ParityTraceRow) -> None:
        """Insert one row. Best-effort — every exception swallowed
        per the directive's measurement-only contract."""
        try:
            with self._LOCK:
                conn = self._connect()
                cols = [f.name for f in fields(row)]
                placeholders = ",".join("?" for _ in cols)
                values = tuple(getattr(row, c) for c in cols)
                conn.execute(
                    f"INSERT INTO tp_parity_trace ({','.join(cols)}) "
                    f"VALUES ({placeholders})",
                    values,
                )
                conn.commit()
        except Exception:  # noqa: BLE001
            return

    def write_many(self, rows: Iterable[ParityTraceRow]) -> None:
        for r in rows:
            self.write(r)

    def fetch_for_trace(self, trace_id: str) -> list:
        """Return every event row tied to ``trace_id``, oldest first."""
        if not self._db_path.is_file():
            return []
        try:
            with self._LOCK:
                conn = self._connect()
                conn.row_factory = sqlite3.Row
                # Defensive — table may not exist yet on a clean install
                # where no emit() has fired.
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' "
                    "AND name='tp_parity_trace'"
                ).fetchone()
                if exists is None:
                    return []
                rows = conn.execute(
                    "SELECT * FROM tp_parity_trace "
                    "WHERE trace_id = ? ORDER BY ts",
                    (trace_id,),
                ).fetchall()
        except sqlite3.DatabaseError:
            return []
        return [dict(r) for r in rows]

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


_DEFAULT_STORE: Optional[ParityTraceStore] = None


def get_default_store() -> ParityTraceStore:
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = ParityTraceStore()
    return _DEFAULT_STORE


def set_default_store(store: Optional[ParityTraceStore]) -> None:
    """Test hook — swap the default writer."""
    global _DEFAULT_STORE
    _DEFAULT_STORE = store


# ---------------------------------------------------------------------------
# Public emit API
# ---------------------------------------------------------------------------


def is_enabled() -> bool:
    """Return True iff ``TOKENPAK_PARITY_TRACE_ENABLED`` is set
    to a truthy value at this exact moment.

    Re-read every call so the operator can flip it without
    restarting the proxy.
    """
    val = os.environ.get(PARITY_TRACE_ENV, "")
    return val.strip().lower() in ("1", "true", "yes", "on")


def emit(
    event_type: str,
    *,
    trace_id: str,
    store: Optional[ParityTraceStore] = None,
    **fields_kwargs: Any,
) -> None:
    """Emit one lifecycle-event row if enabled. No-op when disabled.

    ``trace_id`` and ``event_type`` are required. ``store`` defaults
    to the process-wide default. Any ``ParityTraceRow`` field can be
    passed as a keyword argument.

    All exceptions — including unknown ``event_type`` or schema
    drift — are swallowed. The hot-path guarantee is: this function
    returns in O(1) cheap-dict-lookup time when the env var is
    unset / false.
    """
    if not is_enabled():
        return
    try:
        s = store if store is not None else get_default_store()
        row = ParityTraceRow(
            trace_id=trace_id,
            event_type=event_type,
            ts=time.time(),
            pid=os.getpid(),
            ppid=os.getppid(),
            tokenpak_home=os.environ.get(
                "TOKENPAK_HOME", str(Path.home() / ".tokenpak")
            ),
            telemetry_db_path=str(s.db_path),
            **fields_kwargs,
        )
        s.write(row)
    except Exception:  # noqa: BLE001
        return


__all__ = [
    "ALL_EVENTS",
    "EVENT_HANDLER_ENTRY",
    "EVENT_REQUEST_CLASSIFIED",
    "EVENT_REQUEST_COMPLETION",
    "EVENT_RETRY_BOUNDARY",
    "EVENT_UPSTREAM_ATTEMPT_FAILURE",
    "EVENT_UPSTREAM_ATTEMPT_START",
    "PARITY_TRACE_ENV",
    "ParityTraceRow",
    "ParityTraceStore",
    "RETRY_OWNERS",
    "RETRY_PHASES",
    "RETRY_SIGNALS",
    "emit",
    "get_default_store",
    "is_enabled",
    "set_default_store",
]
