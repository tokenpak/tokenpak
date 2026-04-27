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

# NCP-3I-v2 stream-integrity events (added for H10).
EVENT_STREAM_START: str = "stream_start"
EVENT_STREAM_COMPLETE: str = "stream_complete"
EVENT_STREAM_ABORT: str = "stream_abort"

# NCP-3I-v3 pre-dispatch lifecycle events. Localize where a request
# dies between handler_entry and upstream_attempt_start (per
# iter-6 §1 finding: 4 of 4 traces had handler_entry without
# upstream_attempt_start).
EVENT_AUTH_GATE_PASS: str = "auth_gate_pass"
EVENT_ROUTE_RESOLVED: str = "route_resolved"
EVENT_BODY_READ_COMPLETE: str = "body_read_complete"
EVENT_ADAPTER_DETECTED: str = "adapter_detected"
EVENT_BEFORE_DISPATCH: str = "before_dispatch"

# NCP-3A-enrichment terminal early-return events. The proxy can
# leave the adapter_detected → before_dispatch span via paths that
# do not reach upstream_attempt_start: subprocess companion dispatch
# (returns the upstream's response from a CLI subprocess) and
# explicit rejections (auth 401, circuit-breaker 503, validator
# 400). Without these terminals, the harness mis-classified those
# requests as "silent deaths at adapter_detected" — they are in
# fact intentional terminations.
EVENT_DISPATCH_SUBPROCESS_COMPLETE: str = "dispatch_subprocess_complete"
EVENT_REQUEST_REJECTED: str = "request_rejected"

ALL_EVENTS: frozenset = frozenset({
    EVENT_HANDLER_ENTRY,
    EVENT_REQUEST_CLASSIFIED,
    EVENT_UPSTREAM_ATTEMPT_START,
    EVENT_UPSTREAM_ATTEMPT_FAILURE,
    EVENT_RETRY_BOUNDARY,
    EVENT_REQUEST_COMPLETION,
    EVENT_STREAM_START,
    EVENT_STREAM_COMPLETE,
    EVENT_STREAM_ABORT,
    EVENT_AUTH_GATE_PASS,
    EVENT_ROUTE_RESOLVED,
    EVENT_BODY_READ_COMPLETE,
    EVENT_ADAPTER_DETECTED,
    EVENT_BEFORE_DISPATCH,
    EVENT_DISPATCH_SUBPROCESS_COMPLETE,
    EVENT_REQUEST_REJECTED,
})

# NCP-3I-v3 — canonical pre-dispatch lifecycle order. Used by
# inspect_session_lanes.py to render per-trace progression and
# identify the LAST observed event (= the stage where the
# request died). NCP-3A-enrichment inserts the two terminal
# early-return events between adapter_detected and before_dispatch
# so that the harness's "latest stage" pick prefers them over
# adapter_detected when both are present in a trace.
LIFECYCLE_ORDER: tuple = (
    EVENT_HANDLER_ENTRY,
    EVENT_AUTH_GATE_PASS,
    EVENT_ROUTE_RESOLVED,
    EVENT_BODY_READ_COMPLETE,
    EVENT_REQUEST_CLASSIFIED,
    EVENT_ADAPTER_DETECTED,
    EVENT_REQUEST_REJECTED,
    EVENT_DISPATCH_SUBPROCESS_COMPLETE,
    EVENT_BEFORE_DISPATCH,
    EVENT_UPSTREAM_ATTEMPT_START,
    EVENT_STREAM_START,
    EVENT_STREAM_COMPLETE,
    EVENT_STREAM_ABORT,
    EVENT_UPSTREAM_ATTEMPT_FAILURE,
    EVENT_RETRY_BOUNDARY,
    EVENT_REQUEST_COMPLETION,
)

# NCP-3A-enrichment — events that signal an INTENTIONAL terminal
# decision before upstream_attempt_start. The harness excludes
# traces ending here from the pre-upstream "death" cohort: they
# are completed (subprocess) or explicitly rejected (auth /
# circuit / validation), not silent failures.
TERMINAL_EARLY_RETURN_EVENTS: frozenset = frozenset({
    EVENT_DISPATCH_SUBPROCESS_COMPLETE,
    EVENT_REQUEST_REJECTED,
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


# DDL split into two parts so v1 hosts upgrading get their schema
# migrated BEFORE indexes that reference v2-only columns are created.
_DDL_TABLE = """\
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
    lane_id               TEXT,                -- NCP-3I-v2 — "<pid>:<thread_id>"
    concurrent_stream_count INTEGER,           -- NCP-3I-v2
    -- NCP-3I-v2 stream-integrity dimensions (H10)
    stream_started        INTEGER,
    stream_completed      INTEGER,
    stream_aborted        INTEGER,
    upstream_status       INTEGER,
    downstream_status     INTEGER,
    response_content_type TEXT,
    sse_event_count       INTEGER,
    sse_last_event_type   TEXT,
    bytes_from_upstream   INTEGER,
    bytes_to_client       INTEGER,
    json_parse_error_seen INTEGER,
    stream_exception_class    TEXT,
    stream_exception_message_hash TEXT,
    connection_closed_early INTEGER,
    -- Free-form note (caller-supplied; MUST NOT contain prompt content)
    notes                 TEXT
);
"""

# Indexes run AFTER the ALTER TABLE migrations so v1 hosts have the
# referenced columns by the time the index is created.
_DDL_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_parity_trace_id "
    "ON tp_parity_trace (trace_id, ts)",
    "CREATE INDEX IF NOT EXISTS idx_parity_event_type "
    "ON tp_parity_trace (event_type, ts)",
    "CREATE INDEX IF NOT EXISTS idx_parity_session "
    "ON tp_parity_trace (session_id, ts)",
    "CREATE INDEX IF NOT EXISTS idx_parity_lane "
    "ON tp_parity_trace (lane_id, ts)",
)

# NCP-3I-v2 — additive columns for hosts upgrading from v1.
_V2_ADDITIVE_COLUMNS: tuple = (
    ("lane_id", "TEXT"),
    ("concurrent_stream_count", "INTEGER"),
    ("stream_started", "INTEGER"),
    ("stream_completed", "INTEGER"),
    ("stream_aborted", "INTEGER"),
    ("upstream_status", "INTEGER"),
    ("downstream_status", "INTEGER"),
    ("response_content_type", "TEXT"),
    ("sse_event_count", "INTEGER"),
    ("sse_last_event_type", "TEXT"),
    ("bytes_from_upstream", "INTEGER"),
    ("bytes_to_client", "INTEGER"),
    ("json_parse_error_seen", "INTEGER"),
    ("stream_exception_class", "TEXT"),
    ("stream_exception_message_hash", "TEXT"),
    ("connection_closed_early", "INTEGER"),
)


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
    lane_id: Optional[str] = None  # NCP-3I-v2: e.g. "<pid>:<thread_id>"
    concurrent_stream_count: Optional[int] = None  # NCP-3I-v2

    # NCP-3I-v2 — stream-integrity dimensions (H10)
    stream_started: Optional[int] = None        # 0/1
    stream_completed: Optional[int] = None      # 0/1
    stream_aborted: Optional[int] = None        # 0/1
    upstream_status: Optional[int] = None       # HTTP status from upstream
    downstream_status: Optional[int] = None     # HTTP status sent to client
    response_content_type: Optional[str] = None
    sse_event_count: Optional[int] = None
    sse_last_event_type: Optional[str] = None
    bytes_from_upstream: Optional[int] = None
    bytes_to_client: Optional[int] = None
    json_parse_error_seen: Optional[int] = None  # 0/1
    stream_exception_class: Optional[str] = None
    stream_exception_message_hash: Optional[str] = None  # sha256-hex
    connection_closed_early: Optional[int] = None  # 0/1

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
            # 1. Create the table (idempotent — IF NOT EXISTS).
            conn.executescript(_DDL_TABLE)
            # 2. Apply v2 additive migration BEFORE creating indexes
            #    that reference v2-only columns. SQLite has no
            #    ADD COLUMN IF NOT EXISTS; swallow duplicate-column.
            for col_name, col_type in _V2_ADDITIVE_COLUMNS:
                try:
                    conn.execute(
                        f"ALTER TABLE tp_parity_trace "
                        f"ADD COLUMN {col_name} {col_type}"
                    )
                except sqlite3.OperationalError:
                    pass
            # 3. Now create indexes — all referenced columns exist.
            for idx_sql in _DDL_INDEXES:
                try:
                    conn.execute(idx_sql)
                except sqlite3.OperationalError:
                    pass
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


# ---------------------------------------------------------------------------
# NCP-3I-v2 concurrent-stream counter + helpers
# ---------------------------------------------------------------------------


_CONCURRENT_STREAMS: int = 0
_CONCURRENT_STREAMS_LOCK = threading.Lock()


def begin_stream() -> int:
    """Increment the concurrent-stream gauge; return the new value.
    Off-path; never raises. Callers SHOULD pair with :func:`end_stream`."""
    global _CONCURRENT_STREAMS
    try:
        with _CONCURRENT_STREAMS_LOCK:
            _CONCURRENT_STREAMS += 1
            return _CONCURRENT_STREAMS
    except Exception:  # noqa: BLE001
        return 0


def end_stream() -> int:
    """Decrement the concurrent-stream gauge; return the new value."""
    global _CONCURRENT_STREAMS
    try:
        with _CONCURRENT_STREAMS_LOCK:
            _CONCURRENT_STREAMS = max(0, _CONCURRENT_STREAMS - 1)
            return _CONCURRENT_STREAMS
    except Exception:  # noqa: BLE001
        return 0


def current_lane_id() -> str:
    """Return ``"<pid>:<thread_id>"`` for the calling thread.
    Useful as a stable lane identifier across multi-event traces."""
    try:
        return f"{os.getpid()}:{threading.get_ident()}"
    except Exception:  # noqa: BLE001
        return "unknown"


def hash_exception_message(exc: BaseException) -> str:
    """Return a sha256-hex of the exception's str(message). Allows
    the operator to cluster identical errors without storing the
    message text (which may include URL fragments / user data)."""
    try:
        import hashlib
        msg = str(exc)
        return hashlib.sha256(msg.encode("utf-8", "replace")).hexdigest()
    except Exception:  # noqa: BLE001
        return ""


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
    "EVENT_ADAPTER_DETECTED",
    "EVENT_AUTH_GATE_PASS",
    "EVENT_BEFORE_DISPATCH",
    "EVENT_BODY_READ_COMPLETE",
    "EVENT_DISPATCH_SUBPROCESS_COMPLETE",
    "EVENT_HANDLER_ENTRY",
    "EVENT_REQUEST_CLASSIFIED",
    "EVENT_REQUEST_COMPLETION",
    "EVENT_REQUEST_REJECTED",
    "EVENT_RETRY_BOUNDARY",
    "EVENT_ROUTE_RESOLVED",
    "EVENT_STREAM_ABORT",
    "EVENT_STREAM_COMPLETE",
    "EVENT_STREAM_START",
    "EVENT_UPSTREAM_ATTEMPT_FAILURE",
    "EVENT_UPSTREAM_ATTEMPT_START",
    "LIFECYCLE_ORDER",
    "PARITY_TRACE_ENV",
    "ParityTraceRow",
    "ParityTraceStore",
    "RETRY_OWNERS",
    "RETRY_PHASES",
    "RETRY_SIGNALS",
    "TERMINAL_EARLY_RETURN_EVENTS",
    "begin_stream",
    "current_lane_id",
    "emit",
    "end_stream",
    "get_default_store",
    "hash_exception_message",
    "is_enabled",
    "set_default_store",
]
