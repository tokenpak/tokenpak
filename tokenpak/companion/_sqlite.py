# SPDX-License-Identifier: Apache-2.0
"""Shared SQLite plumbing for the companion stores (journal.db, budget.db).

These databases are written by several concurrent OS processes: the
per-prompt hooks (python and bash variants), the proxy app plane, and the
long-lived MCP server. Historically each writer opened raw connections with
default settings (rollback journal, ``busy_timeout=0``), so a second
concurrent writer hit ``SQLITE_BUSY`` immediately and the best-effort
writers dropped the row silently.

Rules enforced by this module:

- Every python opener goes through :func:`connect`, which enables WAL and a
  >= 5s busy timeout so writers queue instead of failing. (The bash hook
  variants apply the equivalent ``.timeout`` via the sqlite3 CLI.)
- There is exactly ONE canonical DDL for the journal ``sessions`` /
  ``entries`` tables and the budget ``companion_costs`` table. Writers must
  not carry divergent private copies of these statements — first-writer-wins
  schema races were a real defect.
- ``entries`` rows carry a ``content_hash`` dedupe key with a partial
  UNIQUE index so retried/duplicated events collapse under
  ``INSERT OR IGNORE``. Legacy rows (``content_hash IS NULL``) are exempt
  from the index, so the migration is non-destructive and never needs to
  rewrite or dedupe existing data.
- ``companion_costs`` rows carry a ``kind`` column (``'estimate'`` |
  ``'actual'``) so daily-spend readers can count each message exactly once
  (preferring actuals when present). Legacy rows have ``kind IS NULL`` and
  are classified by ``model = ''`` (pre-send estimates never carried a
  model; the recording planes always do).
- Dropped best-effort writes are logged via :func:`note_dropped_write`
  instead of vanishing inside a bare ``except: pass``.

This module must stay stdlib-only: the per-prompt hook imports it on its
hot path.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import sys
import time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, TypedDict

#: Milliseconds a connection waits on a locked database before giving up.
BUSY_TIMEOUT_MS = 5000

#: Best-effort log of dropped writes, relative to the companion dir.
DROPPED_WRITES_LOG = "dropped-writes.log"

#: Retryable materialisation failures.  Unlike a dropped hook write, the
#: underlying intent is retained and will be replayed.
DEFERRED_WRITES_LOG = "deferred-writes.log"

#: Atomic pre-send intents wait here until a non-hook worker materialises them
#: into journal.db and budget.db.  The per-event files are the hook's
#: write-ahead log: a process crash sees either no final file or one complete
#: JSON record, never a half-written record.
PRE_SEND_PENDING_DIR = "pre-send-pending"

#: Singleton-worker claim relative to the companion run directory.
PRE_SEND_FLUSH_CLAIM = "pre-send-flush.claim"

#: Pending-record wire version.  This is private companion state, not a public
#: TokenPak protocol surface.
PRE_SEND_EVENT_VERSION = 1

#: Defensive ceiling for a hook intent.  Normal records are below 1 KiB.
PRE_SEND_EVENT_MAX_BYTES = 64 * 1024

#: A worker claim older than this can be replaced.  Concurrent replacement is
#: harmless because materialisation is idempotent and timestamp-ordered.
PRE_SEND_FLUSH_CLAIM_STALE_S = 120.0


class _PreSendJournal(TypedDict):
    entry_type: str
    content: str
    metadata_json: str


class _PreSendCost(TypedDict):
    input_tokens: int
    estimated_cost: float


class _QueuedPreSendEvent(TypedDict):
    version: int
    event_id: str
    timestamp: float
    date: str
    session_id: str
    journal: _PreSendJournal
    cost: _PreSendCost | None


class _PreSendEvent(TypedDict):
    path: Path
    event_id: str
    timestamp: float
    date: str
    session_id: str
    journal: _PreSendJournal
    cost: _PreSendCost | None


class _SpendSessionState(TypedDict):
    actual_count: int
    actual_spend: float
    legacy_estimate: float | None
    current_estimate: tuple[float, float] | None


# ---------------------------------------------------------------------------
# Canonical DDL — the only copy. Writers import these; do not fork them.
# ---------------------------------------------------------------------------

JOURNAL_SESSIONS_DDL = """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        started_at REAL NOT NULL,
        ended_at REAL,
        project_dir TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        total_requests INTEGER NOT NULL DEFAULT 0,
        total_cost_usd REAL NOT NULL DEFAULT 0.0,
        total_input_tokens INTEGER NOT NULL DEFAULT 0,
        total_output_tokens INTEGER NOT NULL DEFAULT 0,
        capsule_path TEXT
    )
"""

JOURNAL_ENTRIES_DDL = """
    CREATE TABLE IF NOT EXISTS entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        timestamp REAL NOT NULL,
        entry_type TEXT NOT NULL,
        content TEXT NOT NULL DEFAULT '',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        content_hash TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions(session_id)
    )
"""

#: Partial UNIQUE index: dedupes only rows that carry a hash, so it can be
#: created on legacy databases that already contain duplicate rows.
ENTRIES_DEDUPE_INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_entries_dedupe "
    "ON entries(session_id, entry_type, content_hash) "
    "WHERE content_hash IS NOT NULL"
)

JOURNAL_INDEX_DDLS = (
    "CREATE INDEX IF NOT EXISTS idx_entries_session ON entries(session_id)",
    "CREATE INDEX IF NOT EXISTS idx_entries_ts ON entries(timestamp)",
    ENTRIES_DEDUPE_INDEX_DDL,
)

COSTS_DDL = """
    CREATE TABLE IF NOT EXISTS companion_costs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp REAL NOT NULL,
        date TEXT NOT NULL,
        session_id TEXT NOT NULL DEFAULT '',
        model TEXT NOT NULL DEFAULT '',
        input_tokens INTEGER NOT NULL DEFAULT 0,
        cached_tokens INTEGER NOT NULL DEFAULT 0,
        output_tokens INTEGER NOT NULL DEFAULT 0,
        estimated_cost REAL NOT NULL DEFAULT 0.0,
        kind TEXT
    )
"""

#: Partial UNIQUE index backing the one-estimate-row-per-(session, day)
#: upsert. Legacy rows (kind IS NULL) are exempt, so creation succeeds on
#: databases that already contain the historical one-row-per-prompt series.
COSTS_ESTIMATE_INDEX_DDL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_companion_costs_estimate "
    "ON companion_costs(session_id, date) WHERE kind = 'estimate'"
)

#: Upsert for the pre-send estimate plane: a session contributes ONE
#: estimate row per day, refreshed in place to the latest full-transcript
#: estimate. Equivalent to recording per-turn deltas against a per-session
#: high-water mark — the daily gate reads true marginal spend instead of
#: summing a monotonically growing series (the historical over-count).
#: Parameters: (timestamp, date, session_id, input_tokens, estimated_cost).
COSTS_ESTIMATE_UPSERT_SQL = """
    INSERT INTO companion_costs
        (timestamp, date, session_id, model, input_tokens, cached_tokens,
         output_tokens, estimated_cost, kind)
    VALUES (?, ?, ?, '', ?, 0, 0, ?, 'estimate')
    ON CONFLICT(session_id, date) WHERE kind = 'estimate'
    DO UPDATE SET
        timestamp = excluded.timestamp,
        input_tokens = excluded.input_tokens,
        estimated_cost = excluded.estimated_cost
    WHERE excluded.timestamp >= companion_costs.timestamp
"""

#: Truthful daily-spend aggregation. Per (session, day): sum the ACTUAL
#: rows when any exist (the recording planes report real usage), otherwise
#: take the latest/largest ESTIMATE. This counts each message exactly once
#: — never estimate + actual for the same traffic, and never a summed
#: series of cumulative transcript estimates. ``kind`` classifies new rows;
#: legacy rows (kind IS NULL) are classified by ``model = ''``.
#: Parameter: (date,).
DAILY_SPEND_SQL = """
    SELECT COALESCE(SUM(session_spend), 0.0) FROM (
        SELECT CASE
            WHEN SUM(CASE WHEN COALESCE(kind,
                          CASE WHEN model = '' THEN 'estimate' ELSE 'actual' END
                      ) = 'actual' THEN 1 ELSE 0 END) > 0
            THEN SUM(CASE WHEN COALESCE(kind,
                          CASE WHEN model = '' THEN 'estimate' ELSE 'actual' END
                      ) = 'actual' THEN estimated_cost ELSE 0.0 END)
            ELSE MAX(estimated_cost)
        END AS session_spend
        FROM companion_costs
        WHERE date = ?
        GROUP BY session_id
    )
"""


# ---------------------------------------------------------------------------
# Connection factory
# ---------------------------------------------------------------------------


def connect(
    db_path: Path | str,
    *,
    timeout: float = 5.0,
    check_same_thread: bool = True,
    foreign_keys: bool = False,
) -> sqlite3.Connection:
    """Open a companion database with concurrency-safe pragmas.

    ``busy_timeout`` is applied first so even the WAL switch itself waits
    for a lock instead of failing. Pragma application is best-effort: on
    surfaces where WAL is impossible (read-only mounts) the connection
    still works with the rollback journal — and a WAL switch that loses a
    cross-process race is harmless because whichever opener succeeds
    converts the file persistently for everyone.
    """
    conn = sqlite3.connect(str(db_path), timeout=timeout, check_same_thread=check_same_thread)
    try:
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        if foreign_keys:
            conn.execute("PRAGMA foreign_keys=ON")
    except sqlite3.Error:
        pass
    return conn


@contextmanager
def _write_transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Batch one companion schema/write unit into a single durable commit.

    Python's legacy sqlite transaction mode does not automatically open a
    transaction for DDL. Without an explicit boundary, the hook's first-run
    CREATE/ALTER/INDEX statements autocommit one by one and can exceed its
    subprocess timeout on high-latency filesystems. Callers retain connection
    ownership; this helper guarantees commit-or-rollback only.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.commit()
    except BaseException:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise


# ---------------------------------------------------------------------------
# Schema (create + additive migration)
# ---------------------------------------------------------------------------


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    """Additive column migration; never touches existing rows."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def ensure_journal_schema(conn: sqlite3.Connection) -> None:
    """Create/upgrade the journal schema (sessions + entries + indexes).

    Idempotent and non-destructive: legacy databases gain the
    ``content_hash`` column (NULL for existing rows) and the partial
    dedupe index; existing rows are never rewritten or deduplicated.
    """
    conn.execute(JOURNAL_SESSIONS_DDL)
    conn.execute(JOURNAL_ENTRIES_DDL)
    _ensure_column(conn, "entries", "content_hash", "TEXT")
    for ddl in JOURNAL_INDEX_DDLS:
        conn.execute(ddl)


def ensure_costs_schema(conn: sqlite3.Connection) -> None:
    """Create/upgrade the budget schema (companion_costs + estimate index)."""
    conn.execute(COSTS_DDL)
    _ensure_column(conn, "companion_costs", "kind", "TEXT")
    conn.execute(COSTS_ESTIMATE_INDEX_DDL)


# ---------------------------------------------------------------------------
# Dedupe key
# ---------------------------------------------------------------------------


def entry_content_hash(entry_type: str, content: str, metadata_json: str = "{}") -> str:
    """Canonical dedupe key for a journal entry.

    sha256 over ``entry_type <US> content <US> metadata_json`` (US = 0x1f).
    The bash hook variants compute the same preimage via
    ``printf '%s\\037%s\\037%s' | sha256sum`` — keep the two in lockstep.
    ``timestamp`` is deliberately excluded: two deliveries of the same
    event differ only in arrival time and must collapse to one row.
    """
    preimage = "\x1f".join((entry_type, content, metadata_json or "{}"))
    return hashlib.sha256(preimage.encode("utf-8", "replace")).hexdigest()


# ---------------------------------------------------------------------------
# Pre-send write-ahead intents
# ---------------------------------------------------------------------------


def _pre_send_pending_dir(base_dir: Path | str) -> Path:
    return Path(base_dir) / "run" / PRE_SEND_PENDING_DIR


def queue_pre_send_event(
    base_dir: Path | str,
    *,
    session_id: str,
    timestamp: float,
    date: str,
    entry_type: str,
    content: str,
    metadata_json: str,
    tokens_est: int | None,
    cost_est: float | None,
) -> Path:
    """Atomically enqueue one pre-send journal/cost intent.

    The prompt hook must not wait for SQLite lock acquisition, WAL checkpoint,
    or filesystem syncs on two databases.  It therefore writes one small,
    unique same-directory tempfile and atomically renames it into the pending
    directory.  A detached worker (or the next store reader) materialises the
    intent into both databases.  The intent is removed only after both commits
    succeed, so a crash between commits replays safely through journal dedupe
    and the timestamp-ordered cost upsert.

    The tempfile is deliberately not ``fsync``-ed.  Companion hook writes were
    already best-effort under SQLite ``synchronous=NORMAL`` (the most recent
    transaction may be lost on power failure); avoiding an fsync here preserves
    that durability class while eliminating the unbounded prompt-path stall.
    Atomic rename still makes ordinary process crashes torn-record safe.
    """
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("session_id must be a non-empty string")
    timestamp = float(timestamp)
    if not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("timestamp must be finite and non-negative")
    if not isinstance(date, str) or not date:
        raise ValueError("date must be a non-empty string")
    if not isinstance(entry_type, str) or not entry_type:
        raise ValueError("entry_type must be a non-empty string")
    if not isinstance(content, str) or not isinstance(metadata_json, str):
        raise ValueError("journal content and metadata_json must be strings")
    metadata = json.loads(metadata_json)
    if not isinstance(metadata, dict):
        raise ValueError("metadata_json must encode an object")

    cost: _PreSendCost | None = None
    if tokens_est is not None or cost_est is not None:
        if tokens_est is None or cost_est is None:
            raise ValueError("tokens_est and cost_est must be supplied together")
        tokens_est = int(tokens_est)
        cost_est = float(cost_est)
        if tokens_est < 0 or not math.isfinite(cost_est) or cost_est < 0:
            raise ValueError("cost estimate values must be finite and non-negative")
        cost = {"input_tokens": tokens_est, "estimated_cost": cost_est}

    pending_dir = _pre_send_pending_dir(base_dir)
    pending_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    for sequence in range(100):
        event_id = f"{time.time_ns():020d}-{os.getpid()}-{sequence}"
        final_path = pending_dir / f"{event_id}.json"
        tmp_path = pending_dir / f".{event_id}.tmp"
        event: _QueuedPreSendEvent = {
            "version": PRE_SEND_EVENT_VERSION,
            "event_id": event_id,
            "timestamp": timestamp,
            "date": date,
            "session_id": session_id,
            "journal": {
                "entry_type": entry_type,
                "content": content,
                "metadata_json": metadata_json,
            },
            "cost": cost,
        }
        payload = (json.dumps(event, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
        if len(payload) > PRE_SEND_EVENT_MAX_BYTES:
            raise ValueError("pre-send event exceeds the private spool limit")
        try:
            fd = os.open(tmp_path, flags, 0o600)
        except FileExistsError:
            continue
        try:
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("short write while queueing pre-send event")
                view = view[written:]
        except BaseException:
            try:
                os.close(fd)
            finally:
                tmp_path.unlink(missing_ok=True)
            raise
        else:
            os.close(fd)
        try:
            os.replace(tmp_path, final_path)
        except BaseException:
            tmp_path.unlink(missing_ok=True)
            raise
        return final_path
    raise FileExistsError("could not allocate a unique pre-send event path")


def _parse_pre_send_event(path: Path) -> _PreSendEvent:
    payload = path.read_bytes()
    if len(payload) > PRE_SEND_EVENT_MAX_BYTES:
        raise ValueError("pending pre-send event exceeds size limit")
    event = json.loads(payload)
    if not isinstance(event, dict) or event.get("version") != PRE_SEND_EVENT_VERSION:
        raise ValueError("unsupported pending pre-send event version")

    event_id = event.get("event_id")
    session_id = event.get("session_id")
    date = event.get("date")
    journal = event.get("journal")
    if not isinstance(event_id, str) or not event_id or path.name != f"{event_id}.json":
        raise ValueError("pending pre-send event id/path mismatch")
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("pending pre-send event has no session id")
    if not isinstance(date, str) or not date:
        raise ValueError("pending pre-send event has no date")
    if not isinstance(journal, dict):
        raise ValueError("pending pre-send event has no journal payload")
    entry_type = journal.get("entry_type")
    content = journal.get("content")
    metadata_json = journal.get("metadata_json")
    if not isinstance(entry_type, str) or not entry_type:
        raise ValueError("pending pre-send event has invalid entry type")
    if not isinstance(content, str) or not isinstance(metadata_json, str):
        raise ValueError("pending pre-send event has invalid journal strings")
    metadata = json.loads(metadata_json)
    if not isinstance(metadata, dict):
        raise ValueError("pending pre-send metadata must encode an object")

    timestamp = float(event.get("timestamp", -1))
    if not math.isfinite(timestamp) or timestamp < 0:
        raise ValueError("pending pre-send event has invalid timestamp")

    cost = event.get("cost")
    clean_cost: _PreSendCost | None = None
    if cost is not None:
        if not isinstance(cost, dict):
            raise ValueError("pending pre-send event has invalid cost payload")
        tokens_est = int(cost.get("input_tokens", -1))
        cost_est = float(cost.get("estimated_cost", -1))
        if tokens_est < 0 or not math.isfinite(cost_est) or cost_est < 0:
            raise ValueError("pending pre-send event has invalid cost values")
        clean_cost = {"input_tokens": tokens_est, "estimated_cost": cost_est}

    return {
        "path": path,
        "event_id": event_id,
        "timestamp": timestamp,
        "date": date,
        "session_id": session_id,
        "journal": {
            "entry_type": entry_type,
            "content": content,
            "metadata_json": metadata_json,
        },
        "cost": clean_cost,
    }


def _read_pre_send_events(
    base_dir: Path | str,
) -> tuple[list[_PreSendEvent], list[tuple[Path, Exception]]]:
    pending_dir = _pre_send_pending_dir(base_dir)
    try:
        paths = sorted(pending_dir.glob("*.json"))
    except OSError:
        return [], []
    events: list[_PreSendEvent] = []
    invalid: list[tuple[Path, Exception]] = []
    for path in paths:
        try:
            events.append(_parse_pre_send_event(path))
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            invalid.append((path, exc))
    return events, invalid


def _quarantine_invalid_pre_send_events(
    base_dir: Path | str, invalid: list[tuple[Path, Exception]]
) -> None:
    if not invalid:
        return
    invalid_dir = _pre_send_pending_dir(base_dir) / "invalid"
    try:
        invalid_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError:
        invalid_dir = _pre_send_pending_dir(base_dir)
    for path, exc in invalid:
        note_dropped_write(Path(base_dir) / "journal.db", "pending_event_parse", exc)
        target = invalid_dir / f"{path.name}.invalid"
        try:
            os.replace(path, target)
        except OSError:
            pass


def flush_pre_send_events(base_dir: Path | str) -> int:
    """Materialise queued hook intents into both SQLite stores.

    Events remain pending unless the journal transaction *and* the cost
    transaction both commit.  Replays are safe: journal entries use their
    canonical content hash and estimate upserts reject timestamp regression.
    Multiple flush workers may overlap without losing or double-counting data.
    Returns the number of event files removed after successful materialisation.
    """
    base_dir = Path(base_dir)
    events, invalid = _read_pre_send_events(base_dir)
    _quarantine_invalid_pre_send_events(base_dir, invalid)
    if not events:
        return 0
    events.sort(key=lambda item: (item["timestamp"], item["event_id"]))

    journal_path = base_dir / "journal.db"
    try:
        journal_path.parent.mkdir(parents=True, exist_ok=True)
        conn = connect(journal_path)
        try:
            with _write_transaction(conn):
                ensure_journal_schema(conn)
                for event in events:
                    journal = event["journal"]
                    entry_type = journal["entry_type"]
                    content = journal["content"]
                    metadata_json = journal["metadata_json"]
                    conn.execute(
                        "INSERT OR IGNORE INTO entries "
                        "(session_id, timestamp, entry_type, content, metadata_json, "
                        "content_hash) VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            event["session_id"],
                            event["timestamp"],
                            entry_type,
                            content,
                            metadata_json,
                            entry_content_hash(entry_type, content, metadata_json),
                        ),
                    )
        finally:
            conn.close()
    except Exception as exc:
        note_deferred_write(journal_path, "pending_journal_flush", exc)
        return 0

    cost_events = [event for event in events if event["cost"] is not None]
    if cost_events:
        budget_path = base_dir / "budget.db"
        try:
            conn = connect(budget_path)
            try:
                with _write_transaction(conn):
                    ensure_costs_schema(conn)
                    for event in cost_events:
                        cost = event["cost"]
                        assert cost is not None
                        conn.execute(
                            COSTS_ESTIMATE_UPSERT_SQL,
                            (
                                event["timestamp"],
                                event["date"],
                                event["session_id"],
                                cost["input_tokens"],
                                round(cost["estimated_cost"], 6),
                            ),
                        )
            finally:
                conn.close()
        except Exception as exc:
            note_deferred_write(budget_path, "pending_cost_flush", exc)
            return 0

    removed = 0
    for event in events:
        path = event["path"]
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError as exc:
            note_deferred_write(journal_path, "pending_event_cleanup", exc)
    return removed


def daily_spend_with_pending(
    db_path: Path | str,
    pending_base_dir: Path | str,
    date: str,
) -> float:
    """Return truthful daily spend without mutating SQLite on the hook path.

    Pending intents are read *before* SQLite.  A concurrent flusher therefore
    cannot create a missing window: the estimate is observed in the intent, in
    the committed database, or in both.  Per-session reconciliation remains
    identical to :data:`DAILY_SPEND_SQL`: actual rows win; legacy estimate
    series use their maximum; current estimate rows use the newest timestamp.
    """
    pending, _invalid = _read_pre_send_events(pending_base_dir)
    pending_latest: dict[str, tuple[float, float]] = {}
    for event in pending:
        if event["date"] != date or event["cost"] is None:
            continue
        cost = event["cost"]
        assert cost is not None
        session_id = event["session_id"]
        candidate = (event["timestamp"], cost["estimated_cost"])
        previous_pending = pending_latest.get(session_id)
        if previous_pending is None or candidate[0] >= previous_pending[0]:
            pending_latest[session_id] = candidate

    state: dict[str, _SpendSessionState] = {}

    def session_state(session_id: str) -> _SpendSessionState:
        return state.setdefault(
            session_id,
            {
                "actual_count": 0,
                "actual_spend": 0.0,
                "legacy_estimate": None,
                "current_estimate": None,
            },
        )

    db_path = Path(db_path)
    if db_path.exists():
        try:
            uri = db_path.resolve().as_uri() + "?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=0.1)
            try:
                conn.execute("PRAGMA query_only=ON")
                conn.execute("PRAGMA busy_timeout=100")
                columns = {
                    str(row[1]) for row in conn.execute("PRAGMA table_info(companion_costs)")
                }
                if {"session_id", "timestamp", "model", "estimated_cost", "date"} <= columns:
                    rows: Iterable[tuple[Any, Any, Any, Any, Any]]
                    if "kind" in columns:
                        rows = conn.execute(
                            "SELECT session_id, timestamp, model, estimated_cost, kind "
                            "FROM companion_costs WHERE date = ?",
                            (date,),
                        )
                    else:
                        rows = (
                            (*row, None)
                            for row in conn.execute(
                                "SELECT session_id, timestamp, model, estimated_cost "
                                "FROM companion_costs WHERE date = ?",
                                (date,),
                            )
                        )
                    for row_session_id, timestamp, model, estimated_cost, kind in rows:
                        item = session_state(str(row_session_id))
                        effective_kind = kind
                        if effective_kind is None:
                            effective_kind = "estimate_legacy" if str(model) == "" else "actual"
                        if effective_kind == "actual":
                            item["actual_count"] += 1
                            item["actual_spend"] += float(estimated_cost)
                        elif effective_kind == "estimate":
                            candidate = (float(timestamp), float(estimated_cost))
                            previous_current = item["current_estimate"]
                            if previous_current is None or candidate[0] >= previous_current[0]:
                                item["current_estimate"] = candidate
                        else:
                            previous_legacy = item["legacy_estimate"]
                            value = float(estimated_cost)
                            item["legacy_estimate"] = (
                                value if previous_legacy is None else max(previous_legacy, value)
                            )
            finally:
                conn.close()
        except (OSError, sqlite3.Error, TypeError, ValueError):
            pass

    for session_id, candidate in pending_latest.items():
        item = session_state(session_id)
        previous_current = item["current_estimate"]
        if previous_current is None or candidate[0] >= previous_current[0]:
            item["current_estimate"] = candidate

    total = 0.0
    for item in state.values():
        if item["actual_count"] > 0:
            total += item["actual_spend"]
            continue
        legacy = item["legacy_estimate"]
        current = item["current_estimate"]
        if legacy is not None:
            total += max(legacy, current[1] if current is not None else 0.0)
        elif current is not None:
            total += current[1]
    return total


def request_async_pre_send_flush(base_dir: Path | str) -> bool:
    """Start one detached materialiser without waiting on its SQLite work.

    A small atomic claim suppresses worker storms.  Stale-claim replacement is
    safe because flushes are replayable and timestamp ordered.  Returns whether
    this call launched a worker; queued data remains available even if launch
    fails.
    """
    if os.environ.get("TOKENPAK_COMPANION_ASYNC_FLUSH", "1").lower() in {
        "0",
        "false",
        "no",
    }:
        return False
    base_dir = Path(base_dir)
    run_dir = base_dir / "run"
    run_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    claim_path = run_dir / PRE_SEND_FLUSH_CLAIM
    token = f"{os.getpid()}-{time.time_ns()}"
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW

    for _attempt in range(2):
        try:
            fd = os.open(claim_path, flags, 0o600)
        except FileExistsError:
            try:
                if time.time() - claim_path.stat().st_mtime <= PRE_SEND_FLUSH_CLAIM_STALE_S:
                    return False
                claim_path.unlink()
            except (FileNotFoundError, OSError):
                return False
            continue
        try:
            os.write(fd, token.encode("ascii"))
        finally:
            os.close(fd)
        break
    else:
        return False

    try:
        import subprocess

        env = os.environ.copy()
        env["TOKENPAK_COMPANION_ASYNC_FLUSH"] = "0"
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "tokenpak.companion._sqlite",
                "--flush-pre-send",
                str(base_dir),
                token,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env=env,
            start_new_session=os.name != "nt",
        )
    except Exception:
        try:
            if claim_path.read_text(encoding="ascii") == token:
                claim_path.unlink()
        except OSError:
            pass
        return False
    return True


def _run_pre_send_flush_worker(base_dir: Path, token: str) -> int:
    claim_path = base_dir / "run" / PRE_SEND_FLUSH_CLAIM
    try:
        # Rescan a short burst so prompts arriving during the first commit are
        # folded into the same worker rather than spawning a process per event.
        for _ in range(4):
            processed = flush_pre_send_events(base_dir)
            if processed == 0:
                break
            time.sleep(0.025)
        return 0
    finally:
        try:
            if claim_path.read_text(encoding="ascii") == token:
                claim_path.unlink()
        except OSError:
            pass
        # Close the queue/claim race: an event is queued before its hook checks
        # the claim.  Therefore any hook suppressed by the old claim has made
        # its event visible before this post-release scan.  Hooks arriving
        # after release launch their own worker; overlapping flush is safe.
        flush_pre_send_events(base_dir)


def _main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) == 3 and argv[0] == "--flush-pre-send":
        return _run_pre_send_flush_worker(Path(argv[1]), argv[2])
    return 2


# ---------------------------------------------------------------------------
# Dropped-write accounting
# ---------------------------------------------------------------------------


def note_dropped_write(db_path: Path | str, op: str, exc: BaseException) -> None:
    """Record a dropped best-effort write instead of losing it silently.

    Appends one line to ``run/dropped-writes.log`` next to the database and
    emits a single stderr note (visible in the TUI). Never raises — callers
    sit on fail-open hook paths.
    """
    db_path = Path(db_path)
    try:
        run_dir = db_path.parent / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        line = f"{time.time():.3f}\t{db_path.name}\t{op}\t{type(exc).__name__}: {exc}\n"
        with open(run_dir / DROPPED_WRITES_LOG, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass
    try:
        print(
            f"tokenpak: dropped {op} write to {db_path.name} ({type(exc).__name__})",
            file=sys.stderr,
        )
    except Exception:
        pass


def note_deferred_write(db_path: Path | str, op: str, exc: BaseException) -> None:
    """Record a retryable materialisation failure without claiming data loss."""
    db_path = Path(db_path)
    try:
        run_dir = db_path.parent / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        line = f"{time.time():.3f}\t{db_path.name}\t{op}\t{type(exc).__name__}: {exc}\n"
        with open(run_dir / DEFERRED_WRITES_LOG, "a", encoding="utf-8") as fh:
            fh.write(line)
    except Exception:
        pass
    try:
        print(
            f"tokenpak: deferred {op} for {db_path.name}; intent retained ({type(exc).__name__})",
            file=sys.stderr,
        )
    except Exception:
        pass


if __name__ == "__main__":
    sys.exit(_main())
