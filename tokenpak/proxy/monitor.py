"""
TokenPak Monitor — SQLite telemetry, request logging, budget tracking.

Extracted from runtime/proxy.py (Phase 1f of TPK-RESTRUCTURE).
Original location: class Monitor (lines 2320-3204) + SQLite helpers (lines 2248-2319).
"""

from __future__ import annotations

import os
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from queue import Empty, Queue
from typing import TypedDict

DbPath = str | os.PathLike[str]
SqlValue = int | float | str | bytes | None
InsertParams = tuple[SqlValue, ...]
DbWorkItem = tuple[DbPath, InsertParams]


class _DateSavings(TypedDict):
    date: str
    tokens_saved: int
    cost_saved_usd: float


# ---------------------------------------------------------------------------
# Migration system (optional — graceful fallback)
# ---------------------------------------------------------------------------
try:
    from db_migrations import get_current_schema_version
    from db_migrations import migrate as db_migrate

    MIGRATION_AVAILABLE = True
except ImportError:
    MIGRATION_AVAILABLE = False

    def db_migrate(conn: sqlite3.Connection) -> None:
        return None

    def get_current_schema_version(conn: sqlite3.Connection) -> int:
        return 0


# ---------------------------------------------------------------------------
# Budget config — resolved from env at import time (same as proxy.py)
# ---------------------------------------------------------------------------
import os as _os

BUDGET_DAILY_LIMIT_USD: float = float(_os.environ.get("TOKENPAK_BUDGET_DAILY_LIMIT_USD", "0"))
BUDGET_ALERT_THRESHOLD_PCT: float = float(_os.environ.get("TOKENPAK_BUDGET_ALERT_PCT", "80"))

# ---------------------------------------------------------------------------
# SQLite write queue — async background writes, <0.1ms enqueue cost
# ---------------------------------------------------------------------------

_DB_CONNECTION: sqlite3.Connection | None = None
_DB_CONNECTION_PATH: str | None = None
_DB_LOCK = threading.Lock()
_DB_WRITE_QUEUE: Queue[DbWorkItem | None] | None = None
_DB_QUEUE_LOCK = threading.Lock()
_DB_QUEUE_MAX_SIZE = 1000
_DB_BACKGROUND_THREAD: threading.Thread | None = None
_DB_BACKGROUND_STOP = threading.Event()


def _init_db_write_queue() -> None:
    """Initialize the database write queue and background thread."""
    global _DB_WRITE_QUEUE, _DB_BACKGROUND_THREAD
    with _DB_QUEUE_LOCK:
        if _DB_WRITE_QUEUE is None:
            _DB_WRITE_QUEUE = Queue(maxsize=_DB_QUEUE_MAX_SIZE)
            _DB_BACKGROUND_STOP.clear()
            _DB_BACKGROUND_THREAD = threading.Thread(
                target=_db_writer_worker,
                daemon=True,
                name="TokenPak-DB-Writer",
            )
            _DB_BACKGROUND_THREAD.start()


# Write durability: bounded retry on transient lock errors before a row is
# counted as dropped. Total worst-case backoff ~0.5s (0.05+0.10+0.15+0.20).
_DB_WRITE_RETRY_ATTEMPTS = 5
_DB_WRITE_RETRY_BACKOFF_S = 0.05

# Rows lost by the write path after retries were exhausted (or on a fatal
# error). Never reset at runtime — diagnostic surfaces can expose it via
# Monitor.dropped_row_count() to make silent write loss visible.
_DB_DROPPED_ROWS = 0
_DB_DROPPED_ROWS_LOCK = threading.Lock()

# Single source of truth for the requests INSERT column list. Both the async
# writer and the synchronous fallback build their statement from this tuple so
# the two paths can never drift apart.
_REQUEST_INSERT_COLUMNS = (
    "timestamp",
    "model",
    "request_type",
    "input_tokens",
    "output_tokens",
    "estimated_cost",
    "latency_ms",
    "status_code",
    "endpoint",
    "compilation_mode",
    "protected_tokens",
    "compressed_tokens",
    "injected_tokens",
    "injected_sources",
    "cache_read_tokens",
    "cache_creation_tokens",
    "would_have_saved",
    "cache_origin",
    "user_id",
    "cache_creation_ephemeral_1h_tokens",
    "cache_creation_ephemeral_5m_tokens",
    "ttl_attribution",
    "session_id",
    "agent_id",
    "cycle_id",
    "attribution_source",
    "stop_reason",
)


def _request_insert_sql() -> str:
    """Build the shared requests INSERT statement from the column tuple."""
    cols = ",".join(_REQUEST_INSERT_COLUMNS)
    placeholders = ",".join("?" for _ in _REQUEST_INSERT_COLUMNS)
    return f"INSERT INTO requests ({cols}) VALUES ({placeholders})"


def _record_dropped_row(reason: str, exc: BaseException) -> None:
    """Count a lost telemetry row and surface the failure on stderr."""
    global _DB_DROPPED_ROWS
    with _DB_DROPPED_ROWS_LOCK:
        _DB_DROPPED_ROWS += 1
    print(f"[TokenPak] DB write dropped ({reason}): {exc}", file=sys.stderr)


def get_dropped_row_count() -> int:
    """Total telemetry rows dropped by the write path since process start."""
    with _DB_DROPPED_ROWS_LOCK:
        return _DB_DROPPED_ROWS


def _is_transient_lock_error(exc: BaseException) -> bool:
    """True for SQLite errors worth retrying (lock/busy contention)."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _write_row(db_path: DbPath, insert_params: InsertParams) -> None:
    """Write one telemetry row through the guarded shared connection.

    All writes (async worker and synchronous fallback) come through here so
    they share one code path: the persistent WAL/busy_timeout connection,
    the _DB_LOCK guard, and the single INSERT statement builder. Transient
    'database is locked/busy' errors are retried with a short linear backoff;
    the final failure (or any non-transient error) is raised so the caller
    can count the drop.
    """
    last_exc: sqlite3.OperationalError | None = None
    for attempt in range(_DB_WRITE_RETRY_ATTEMPTS):
        try:
            with _DB_LOCK:
                conn = _get_db_connection(db_path)
                conn.execute(_request_insert_sql(), insert_params)
                conn.commit()
            return
        except sqlite3.OperationalError as exc:
            if not _is_transient_lock_error(exc):
                raise
            last_exc = exc
            if attempt < _DB_WRITE_RETRY_ATTEMPTS - 1:
                time.sleep(_DB_WRITE_RETRY_BACKOFF_S * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("database write retries exhausted without an exception")


def _db_writer_worker() -> None:
    """Background worker thread that drains the DB write queue."""
    while not _DB_BACKGROUND_STOP.is_set():
        try:
            # Block for up to 1 second waiting for items
            queue = _DB_WRITE_QUEUE
            if queue is None:
                return
            work_item = queue.get(timeout=1.0)
            if work_item is None:  # Poison pill to stop
                queue.task_done()
                break

            db_path, insert_params = work_item
            try:
                _write_row(db_path, insert_params)
            except Exception as e:
                _record_dropped_row("async-writer", e)
            finally:
                queue.task_done()
        except Empty:
            continue
        except Exception as e:
            print(f"[TokenPak] DB worker error: {e}", file=sys.stderr)


def _wait_for_queue_drain(q: Queue[DbWorkItem | None], deadline: float) -> bool:
    """Poll until the queue's unfinished tasks reach zero or deadline passes."""
    while True:
        if getattr(q, "unfinished_tasks", 0) == 0:
            return True
        if time.monotonic() >= deadline:
            return getattr(q, "unfinished_tasks", 0) == 0
        time.sleep(0.01)


def _stop_db_write_queue(timeout: float = 5.0) -> bool:
    """Drain the write queue and stop the background writer thread.

    Sends a poison pill, waits (bounded by ``timeout``) for queued rows to be
    committed, joins the writer thread, and resets module writer state so a
    later Monitor construction can restart it. Returns True when the queue
    fully drained within the timeout.
    """
    global _DB_WRITE_QUEUE, _DB_BACKGROUND_THREAD
    with _DB_QUEUE_LOCK:
        q = _DB_WRITE_QUEUE
        thread = _DB_BACKGROUND_THREAD
        if q is None:
            return True
        deadline = time.monotonic() + timeout
        try:
            q.put(None, timeout=max(0.0, timeout / 2.0))  # poison pill
        except Exception:
            # Queue full and stayed full: stop the worker after its current
            # item instead of blocking shutdown forever.
            _DB_BACKGROUND_STOP.set()
        drained = _wait_for_queue_drain(q, deadline)
        _DB_BACKGROUND_STOP.set()
        if thread is not None and thread.is_alive():
            thread.join(timeout=max(0.1, deadline - time.monotonic()))
        _DB_WRITE_QUEUE = None
        _DB_BACKGROUND_THREAD = None
        return drained


def _get_db_connection(db_path: DbPath) -> sqlite3.Connection:
    """Get the guarded SQLite writer connection for ``db_path``.

    The write queue is process-global and each work item carries its own
    database path.  A cached connection therefore cannot be reused after the
    queue switches paths: doing so silently commits the row to the previous
    monitor database.  Production normally has one monitor path, while tests,
    embedders, and recovery tooling may legitimately interleave several.
    """
    global _DB_CONNECTION, _DB_CONNECTION_PATH
    requested_path = os.path.abspath(os.path.expanduser(os.fspath(db_path)))
    if _DB_CONNECTION is not None and _DB_CONNECTION_PATH != requested_path:
        _DB_CONNECTION.close()
        _DB_CONNECTION = None
        _DB_CONNECTION_PATH = None
    if _DB_CONNECTION is None:
        _DB_CONNECTION = sqlite3.connect(
            requested_path,
            check_same_thread=False,  # Required for ThreadedHTTPServer
        )
        _DB_CONNECTION.execute("PRAGMA journal_mode=WAL")
        _DB_CONNECTION.execute("PRAGMA synchronous=NORMAL")
        _DB_CONNECTION.execute("PRAGMA busy_timeout=5000")
        _DB_CONNECTION_PATH = requested_path
    return _DB_CONNECTION


# ---------------------------------------------------------------------------
# Monitor class
# ---------------------------------------------------------------------------


def _apply_schema_migration(conn: sqlite3.Connection, ddl: str) -> None:
    """Run an idempotent ALTER TABLE, tolerating only 'duplicate column name'.

    Any other OperationalError (most importantly 'database is locked') is
    re-raised: silently swallowing it would skip the migration, leave the
    schema behind, and make every subsequent INSERT fail with
    'no such column' until restart.
    """
    try:
        conn.execute(ddl)
    except sqlite3.OperationalError as exc:
        if "duplicate column name" in str(exc).lower():
            return
        raise


def _estimate_bucket_savings_usd(
    model: str | None, compressed_tokens: int, cache_read_tokens: int
) -> float:
    """Registry-rate savings estimate for one (model) aggregation bucket.

    Compression savings value tokens eliminated entirely at the model's
    registry input rate. Cache-read savings reuse the shared proxy estimator
    (registry rate x per-provider read multiplier) so every savings surface
    agrees on a single formula instead of hardcoded flat rates. Falls back to
    conservative defaults if the registry is unavailable.
    """
    total = 0.0
    if compressed_tokens and compressed_tokens > 0:
        try:
            from tokenpak.models import get_rates

            input_rate = get_rates(model or None).get("input", 3.0)
        except Exception:
            input_rate = 3.0
        total += compressed_tokens * input_rate / 1_000_000
    if cache_read_tokens and cache_read_tokens > 0:
        try:
            from tokenpak.models import detect_provider
            from tokenpak.proxy.cache import estimate_cache_savings

            provider = detect_provider(model or "")
            total += estimate_cache_savings(provider, cache_read_tokens, model or "")
        except Exception:
            # Conservative fallback: default input rate at a 90% read discount.
            total += cache_read_tokens * 3.0 / 1_000_000 * 0.90
    return total


class Monitor:
    """SQLite-backed request telemetry (requests + budget_alerts tables).

    Writes are enqueued to a background writer thread (async, sub-millisecond
    enqueue). The writer is a daemon thread, so queued rows are NOT
    automatically durable across interpreter exit: embedders that own process
    shutdown should call :meth:`stop` (poison pill + bounded drain + join) or
    :meth:`flush` (bounded drain without stopping the writer) to guarantee
    queued telemetry reaches the database on a clean exit. Rows that still
    fail after bounded write retries are counted; diagnostic surfaces can
    read the counter via :meth:`dropped_row_count`.
    """

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = db_path
        self._lock = threading.Lock()
        self._init_db()
        # Start background worker on first Monitor creation
        try:
            _init_db_write_queue()
        except NameError:
            pass

    def _init_db(self) -> None:
        conn = sqlite3.connect(str(self.db_path))
        # Wait out short lock contention instead of failing migrations at
        # startup (a raced ALTER would otherwise surface as 'database is
        # locked' and abort schema setup).
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                model TEXT NOT NULL,
                request_type TEXT,
                input_tokens INTEGER,
                output_tokens INTEGER,
                estimated_cost REAL,
                latency_ms INTEGER,
                status_code INTEGER,
                endpoint TEXT,
                compilation_mode TEXT,
                protected_tokens INTEGER,
                compressed_tokens INTEGER,
                injected_tokens INTEGER DEFAULT 0,
                injected_sources TEXT DEFAULT '',
                cache_read_tokens INTEGER DEFAULT 0,
                cache_creation_tokens INTEGER DEFAULT 0,
                would_have_saved INTEGER DEFAULT 0,
                user_id TEXT DEFAULT '',
                session_id TEXT DEFAULT '',
                agent_id TEXT DEFAULT '',
                cycle_id TEXT DEFAULT '',
                attribution_source TEXT DEFAULT ''
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON requests(timestamp)")
        # Add columns if upgrading from v3. NOTE: these migrations tolerate
        # ONLY 'duplicate column name' (idempotent re-run). Any other
        # OperationalError — most importantly 'database is locked' — must
        # propagate; swallowing it would silently skip the ALTER, leave the
        # schema behind, and make every INSERT fail with 'no such column'
        # until restart.
        _apply_schema_migration(
            conn, "ALTER TABLE requests ADD COLUMN injected_tokens INTEGER DEFAULT 0"
        )
        _apply_schema_migration(
            conn, "ALTER TABLE requests ADD COLUMN injected_sources TEXT DEFAULT ''"
        )
        _apply_schema_migration(
            conn, "ALTER TABLE requests ADD COLUMN cache_read_tokens INTEGER DEFAULT 0"
        )
        _apply_schema_migration(
            conn, "ALTER TABLE requests ADD COLUMN cache_creation_tokens INTEGER DEFAULT 0"
        )
        _apply_schema_migration(
            conn, "ALTER TABLE requests ADD COLUMN would_have_saved INTEGER DEFAULT 0"
        )
        _apply_schema_migration(
            conn, "ALTER TABLE requests ADD COLUMN cache_origin TEXT DEFAULT 'unknown'"
        )
        # Anthropic prompt-cache TTL attribution (additive, backward-compatible).
        # Older rows have NULL/0 here; readers must COALESCE for aggregation.
        _apply_schema_migration(
            conn,
            "ALTER TABLE requests ADD COLUMN cache_creation_ephemeral_1h_tokens INTEGER DEFAULT 0",
        )
        _apply_schema_migration(
            conn,
            "ALTER TABLE requests ADD COLUMN cache_creation_ephemeral_5m_tokens INTEGER DEFAULT 0",
        )
        _apply_schema_migration(
            conn, "ALTER TABLE requests ADD COLUMN ttl_attribution TEXT DEFAULT NULL"
        )
        # P0-06 (A6): user_id holds the SHA-256 hex of the proxy auth bearer
        # token when the proxy auth gate accepted the request via the bearer
        # path. Empty string for localhost / pre-A6 rows. Hash only — never the
        # raw token.
        _apply_schema_migration(conn, "ALTER TABLE requests ADD COLUMN user_id TEXT DEFAULT ''")
        # Reasoning-usage columns (Provider-Native Compatibility Foundation,
        # Packet A 2026-05-16). Populated by the dynamic per-provider parser
        # registry under tokenpak.services.providers. Null/0 for pre-feature
        # rows and for providers without reasoning usage surfaces.
        for _alter in (
            "ALTER TABLE requests ADD COLUMN reasoning_tokens INTEGER DEFAULT NULL",
            "ALTER TABLE requests ADD COLUMN visible_output_tokens INTEGER DEFAULT NULL",
            "ALTER TABLE requests ADD COLUMN total_billable_tokens INTEGER DEFAULT NULL",
            "ALTER TABLE requests ADD COLUMN reasoning_effort TEXT DEFAULT ''",
            "ALTER TABLE requests ADD COLUMN reasoning_usage_source TEXT DEFAULT ''",
            "ALTER TABLE requests ADD COLUMN provider_usage_ref TEXT DEFAULT ''",
        ):
            _apply_schema_migration(conn, _alter)
        # Stream-mode telemetry columns (Provider-Native Compatibility
        # Foundation, Packet D 2026-05-16). Populated when the stream
        # translator or byte-passthrough decision path resolves; empty
        # string for non-streaming or pre-feature rows.
        for _alter in (
            "ALTER TABLE requests ADD COLUMN stream_mode TEXT DEFAULT ''",
            "ALTER TABLE requests ADD COLUMN event_transform_applied INTEGER DEFAULT 0",
        ):
            _apply_schema_migration(conn, _alter)
        # D5 (finishes Fix A): agent/cycle attribution columns on requests.
        # agent_id <- X-Tokenpak-Agent header; cycle_id <- X-Tokenpak-Cycle
        # (no caller sets X-Tokenpak-Cycle yet -> '' sentinel, classified
        # 'unknown', never fabricated). Idempotent — columns may pre-exist
        # from a peer migration. Telemetry contract: '' sentinel, not NULL.
        for _alter in (
            "ALTER TABLE requests ADD COLUMN agent_id TEXT DEFAULT ''",
            "ALTER TABLE requests ADD COLUMN cycle_id TEXT DEFAULT ''",
            # attribution_source <- platform-origin extractor (Path C). Non-empty
            # only when origin is genuinely known; '' sentinel otherwise (never
            # fabricated). Idempotent — may pre-exist from a peer migration.
            "ALTER TABLE requests ADD COLUMN attribution_source TEXT DEFAULT ''",
        ):
            _apply_schema_migration(conn, _alter)
        # Provider execution truth: stop_reason observed on the response path
        # (non-streaming JSON `stop_reason`; SSE `message_delta.delta.stop_reason`).
        # Makes a refusal returned as HTTP 200 distinguishable from a successful
        # completion on receipt rows. '' sentinel = not observed (legacy rows,
        # errored/truncated streams) - never fabricated. Idempotent.
        _apply_schema_migration(conn, "ALTER TABLE requests ADD COLUMN stop_reason TEXT DEFAULT ''")
        conn.commit()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS budget_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                agent_id TEXT DEFAULT "",
                period TEXT DEFAULT "daily",
                budget_usd REAL,
                spent_usd REAL,
                pct_used REAL,
                triggered INTEGER DEFAULT 1
            )
        """)
        conn.commit()
        # Duplicate-alert guard: at most one budget alert per (local day,
        # period). Pre-existing duplicates from the old check-then-insert
        # race are collapsed to the earliest row so the unique index can be
        # created on legacy databases.
        try:
            conn.execute(
                "DELETE FROM budget_alerts WHERE id NOT IN "
                "(SELECT MIN(id) FROM budget_alerts GROUP BY date(timestamp), period)"
            )
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_budget_alerts_day_period "
                "ON budget_alerts(date(timestamp), period)"
            )
            conn.commit()
        except sqlite3.OperationalError as exc:
            # Expression indexes need a modern SQLite; without the index the
            # INSERT ... WHERE NOT EXISTS guard still bounds duplicates.
            print(
                f"[TokenPak] budget_alerts dedupe index unavailable: {exc}",
                file=sys.stderr,
            )

        # session_id on requests + mutation_audit table
        try:
            from tokenpak.proxy.db import ensure_schema as _ccg02_ensure_schema

            _ccg02_ensure_schema(conn)
            conn.commit()
        except Exception as e:
            print(f"⚠️  schema migration error (non-fatal): {e}")

        # Run migrations to bring DB schema up to current version
        try:
            if MIGRATION_AVAILABLE:
                try:
                    db_migrate(conn)
                    version = get_current_schema_version(conn)
                    print(f"✅ DB schema version: {version}")
                except Exception as e:
                    print(f"⚠️  Migration error (non-fatal): {e}")
        except NameError:
            pass

        conn.close()
        # Reset the shared writer connection under the writer lock so a
        # concurrent write can't race the swap, and close the old handle
        # instead of leaking it.
        global _DB_CONNECTION, _DB_CONNECTION_PATH
        with _DB_LOCK:
            if _DB_CONNECTION is not None:
                try:
                    _DB_CONNECTION.close()
                except Exception:
                    pass
            _DB_CONNECTION = None  # reset so next call reopens fresh
            _DB_CONNECTION_PATH = None

    def log(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost: float,
        latency_ms: int,
        status_code: int,
        endpoint: str,
        compilation_mode: str = "",
        protected_tokens: int = 0,
        compressed_tokens: int = 0,
        injected_tokens: int = 0,
        injected_sources: str = "",
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        would_have_saved: int = 0,
        cache_origin: str = "unknown",
        user_id: str = "",
        cache_creation_ephemeral_1h_tokens: int = 0,
        cache_creation_ephemeral_5m_tokens: int = 0,
        ttl_attribution: str | None = None,
        session_id: str = "",
        agent_id: str = "",
        cycle_id: str = "",
        attribution_source: str = "",
        stop_reason: str = "",
    ) -> None:
        # ``session_id`` is the resolved Claude Code / TokenPak session id
        # (``_resolve_session_id``). Empty string when no session header was
        # present. NOTE: Claude Code spawned subagents reuse the parent
        # session id verbatim, so this attributes to a session but does not
        # separate subagent traffic from main — see findings 2026-05-30.
        # P0-06 (A6): ``user_id`` is the SHA-256 hex of the proxy auth bearer
        # token populated by ``_ProxyHandler._enforce_proxy_auth``. Defaults to
        # "" for localhost / pre-A6 callers. The raw token MUST never be passed
        # in — callers always use ``proxy_auth.hash_token(...)`` first.
        # Enqueue write instead of writing directly (async, <0.1ms return)
        insert_params = (
            datetime.now().isoformat(),
            model,
            "chat",
            input_tokens,
            output_tokens,
            cost,
            latency_ms,
            status_code,
            endpoint,
            compilation_mode,
            protected_tokens,
            compressed_tokens,
            injected_tokens,
            injected_sources,
            cache_read_tokens,
            cache_creation_tokens,
            would_have_saved,
            cache_origin,
            user_id or "",
            int(cache_creation_ephemeral_1h_tokens or 0),
            int(cache_creation_ephemeral_5m_tokens or 0),
            ttl_attribution,
            session_id or "",
            agent_id or "",
            cycle_id or "",
            attribution_source or "",
            stop_reason or "",
        )
        _queued = False
        try:
            queue = _DB_WRITE_QUEUE
            if queue is None:
                raise RuntimeError("database write queue is not initialized")
            queue.put_nowait((self.db_path, insert_params))
            _queued = True
        except (NameError, Exception):
            # Queue full / uninitialized / stopped: write synchronously through
            # the SAME guarded path the async writer uses (persistent
            # WAL + busy_timeout connection, _DB_LOCK, shared INSERT builder,
            # bounded retry) instead of a fresh unguarded connection.
            try:
                _write_row(self.db_path, insert_params)
            except Exception as exc:
                _record_dropped_row("sync-fallback", exc)
        try:
            # When queued async, cost not yet in DB — pass it as current_cost.
            # When written synchronously (fallback), cost already in DB — pass 0.
            self._check_budget_alert(current_cost=cost if (_queued and cost) else 0)
        except Exception:
            pass

    def _read_connection(self) -> sqlite3.Connection:
        """Short-lived per-call read connection.

        Readers must not share the writer's persistent connection: doing so
        would require holding the writer lock for every read and mutating
        shared state such as ``row_factory``. WAL mode lets these read
        connections coexist with the background writer.
        """
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def flush(self, timeout: float = 5.0) -> bool:
        """Block until queued telemetry writes are committed (bounded).

        Returns True when the write queue fully drained within ``timeout``
        seconds. Does not stop the background writer; safe to call any time.
        """
        q = _DB_WRITE_QUEUE
        if q is None:
            return True
        return _wait_for_queue_drain(q, time.monotonic() + timeout)

    def stop(self, timeout: float = 5.0) -> bool:
        """Drain queued writes and stop the background writer thread.

        Sends a poison pill, waits (bounded by ``timeout``) for queued rows
        to be committed, joins the writer thread, and resets writer state so
        a later Monitor construction can restart it. Returns True on a clean
        drain. Nothing invokes this automatically — embedders that own
        process shutdown should call it to avoid losing queued rows on a
        clean exit (the writer is a daemon thread).
        """
        return _stop_db_write_queue(timeout=timeout)

    def dropped_row_count(self) -> int:
        """Telemetry rows dropped after write retries were exhausted.

        Exposed so health/diagnostic surfaces can report write-path loss
        instead of it staying invisible on stderr.
        """
        return get_dropped_row_count()

    def get_stats(self, hours: int = 24) -> dict[str, object]:
        conn = self._read_connection()
        try:
            # Rows are stamped with LOCAL time (datetime.now().isoformat()),
            # so the window cutoff must be local too: bare datetime('now') is
            # UTC and would mis-window the report by the UTC offset. The stored
            # timestamp is 'T'-separated ISO (isoformat) while datetime(...)
            # yields a space-separated string — wrap the column in datetime() so
            # both sides normalize to the same form. A raw string compare would
            # count every same-date row as in-window because 'T' > ' ' lexically
            # (get_stats(hours=1) would behave like a whole-day window).
            row = conn.execute(
                """
                SELECT COUNT(*), COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),
                       COALESCE(SUM(estimated_cost),0), COALESCE(AVG(latency_ms),0),
                       COALESCE(SUM(protected_tokens),0), COALESCE(SUM(compressed_tokens),0),
                       COALESCE(SUM(injected_tokens),0),
                       COALESCE(SUM(cache_read_tokens),0),
                       COALESCE(SUM(cache_creation_tokens),0)
                FROM requests WHERE datetime(timestamp) >= datetime('now', 'localtime', ?)
            """,
                (f"-{hours} hours",),
            ).fetchone()
        finally:
            conn.close()
        return {
            "requests": row[0],
            "input_tokens": row[1],
            "output_tokens": row[2],
            "total_cost": round(row[3], 4),
            "avg_latency_ms": round(row[4], 0),
            "protected_tokens": row[5],
            "compressed_tokens": row[6],
            "injected_tokens": row[7],
            "cache_read_tokens": row[8],
            "cache_creation_tokens": row[9],
        }

    def get_by_model(self) -> dict[str, dict[str, object]]:
        conn = self._read_connection()
        try:
            rows = conn.execute("""
                SELECT model, COUNT(*), SUM(input_tokens), SUM(output_tokens), SUM(estimated_cost),
                       SUM(cache_read_tokens), SUM(cache_creation_tokens), COALESCE(SUM(compressed_tokens),0)
                FROM requests GROUP BY model ORDER BY SUM(estimated_cost) DESC
            """).fetchall()
        finally:
            conn.close()
        result: dict[str, dict[str, object]] = {}
        for r in rows:
            input_tokens = r[2] or 0
            compressed_tokens = r[7] or 0
            compression_ratio = (
                round(compressed_tokens / input_tokens, 4) if input_tokens > 0 else 0.0
            )
            result[r[0]] = {
                "requests": r[1],
                "input_tokens": input_tokens,
                "output_tokens": r[3],
                "cost": round(r[4], 4),
                "cache_read_tokens": r[5] or 0,
                "cache_creation_tokens": r[6] or 0,
                "compressed_tokens": compressed_tokens,
                "compression_ratio": compression_ratio,
            }
        return result

    def _check_budget_alert(
        self,
        current_cost: float = 0.0,
        _daily_limit: float | None = None,
        _threshold_pct: float | None = None,
    ) -> None:
        try:
            daily_limit = _daily_limit if _daily_limit is not None else BUDGET_DAILY_LIMIT_USD
        except NameError:
            daily_limit = 0.0
        try:
            threshold_pct = (
                _threshold_pct if _threshold_pct is not None else BUDGET_ALERT_THRESHOLD_PCT
            )
        except NameError:
            threshold_pct = 80.0
        if daily_limit <= 0:
            return
        conn = self._read_connection()
        try:
            # Rows are stamped with LOCAL time (datetime.now().isoformat());
            # the day window must be local too. Bare date('now') is UTC and
            # would read today's spend as $0 for part of every local day.
            spent = (
                conn.execute(
                    "SELECT COALESCE(SUM(estimated_cost), 0) FROM requests "
                    "WHERE date(timestamp) = date('now', 'localtime')"
                ).fetchone()[0]
                or 0.0
            )
            total_spent = float(spent) + float(current_cost)
            if total_spent >= daily_limit * threshold_pct / 100:
                import datetime as _dt

                # Dedupe: the UNIQUE(date(timestamp), period) index plus
                # INSERT OR IGNORE collapse concurrent triggers into one row
                # per local day; the NOT EXISTS guard keeps behavior bounded
                # even on SQLite builds without expression-index support.
                conn.execute(
                    "INSERT OR IGNORE INTO budget_alerts "
                    "(timestamp, period, budget_usd, spent_usd, pct_used, triggered) "
                    "SELECT ?, ?, ?, ?, ?, ? "
                    "WHERE NOT EXISTS (SELECT 1 FROM budget_alerts "
                    "WHERE date(timestamp) = date('now', 'localtime') AND period = 'daily')",
                    (
                        _dt.datetime.now().isoformat(),
                        "daily",
                        daily_limit,
                        total_spent,
                        round(total_spent / daily_limit * 100, 2),
                        1,
                    ),
                )
                conn.commit()
        finally:
            conn.close()

    def get_budget_alert_status(
        self, _daily_limit: float | None = None, _threshold_pct: float | None = None
    ) -> dict[str, object]:
        try:
            daily_limit = _daily_limit if _daily_limit is not None else BUDGET_DAILY_LIMIT_USD
        except NameError:
            daily_limit = 0.0
        try:
            threshold_pct = (
                _threshold_pct if _threshold_pct is not None else BUDGET_ALERT_THRESHOLD_PCT
            )
        except NameError:
            threshold_pct = 80.0
        conn = self._read_connection()
        try:
            # Local-day window to match the locally-stamped timestamps (see
            # _check_budget_alert).
            spent = (
                conn.execute(
                    "SELECT COALESCE(SUM(estimated_cost), 0) FROM requests "
                    "WHERE date(timestamp) = date('now', 'localtime')"
                ).fetchone()[0]
                or 0.0
            )
            spent = float(spent)
            pct_used = round(spent / daily_limit * 100, 2) if daily_limit > 0 else 0.0
            remaining = max(0.0, daily_limit - spent)
            alert_triggered = (pct_used >= threshold_pct) if daily_limit > 0 else False
            last_row = conn.execute(
                "SELECT timestamp FROM budget_alerts ORDER BY id DESC LIMIT 1"
            ).fetchone()
            last_alert_at = last_row[0] if last_row else None
        finally:
            conn.close()
        return {
            "spent_usd": round(spent, 4),
            "budget_usd": daily_limit,
            "pct_used": pct_used,
            "remaining_usd": round(remaining, 4),
            "alert_triggered": alert_triggered,
            "last_alert_at": last_alert_at,
        }

    def get_savings_report(self, since: str | None = None) -> dict[str, object]:
        """Savings summary computed from registry pricing rates.

        Cost figures use the shared estimator path (model registry rates and
        per-provider cache-read multipliers) rather than hardcoded flat
        rates, so this report and the proxy runtime agree on one formula.
        Cache-read savings are credited ONLY for rows whose ``cache_origin``
        is ``'proxy'`` (cache markers placed by this product). Client-placed
        and unknown-origin cache reads are reported separately and never
        claimed as product savings.
        """
        conn = self._read_connection()
        try:
            where = ""
            params: list[str] = []
            if since:
                where = "WHERE date(timestamp) >= ?"
                params = [since]
            _origin = "COALESCE(cache_origin, 'unknown')"
            select_sums = (
                "COUNT(*), "
                "COALESCE(SUM(compressed_tokens),0), "
                f"COALESCE(SUM(CASE WHEN {_origin} = 'proxy' THEN cache_read_tokens ELSE 0 END),0), "
                f"COALESCE(SUM(CASE WHEN {_origin} = 'client' THEN cache_read_tokens ELSE 0 END),0), "
                f"COALESCE(SUM(CASE WHEN {_origin} NOT IN ('proxy', 'client') THEN cache_read_tokens ELSE 0 END),0)"
            )

            # by model — also the basis for the totals, because pricing
            # rates are per-model.
            model_rows = conn.execute(
                f"SELECT model, {select_sums} FROM requests {where} GROUP BY model",
                params,
            ).fetchall()

            savings_by_model: dict[str, dict[str, object]] = {}
            total_requests = 0
            total_tokens_saved = 0
            total_cost_saved = 0.0
            client_cache_read_tokens = 0
            client_cache_read_est_usd = 0.0
            unknown_cache_read_tokens = 0
            for model, reqs, comp, product_cr, client_cr, unknown_cr in model_rows:
                comp = comp or 0
                product_cr = product_cr or 0
                total_requests += reqs or 0
                cost_saved = _estimate_bucket_savings_usd(model, comp, product_cr)
                savings_by_model[model] = {
                    "requests": reqs,
                    "tokens_saved": int(comp + product_cr),
                    "cost_saved_usd": round(cost_saved, 4),
                }
                total_tokens_saved += int(comp + product_cr)
                total_cost_saved += cost_saved
                client_cache_read_tokens += client_cr or 0
                client_cache_read_est_usd += _estimate_bucket_savings_usd(model, 0, client_cr or 0)
                unknown_cache_read_tokens += unknown_cr or 0

            # by date (last 7 days) — same per-model rates, folded per day.
            # Local-day window: rows are stamped with local time.
            date_where = "WHERE date(timestamp) >= date('now', 'localtime', '-7 days')"
            date_params: list[str] = []
            if since:
                date_where += " AND date(timestamp) >= ?"
                date_params = [since]
            date_rows = conn.execute(
                f"SELECT date(timestamp), model, {select_sums} FROM requests {date_where} "
                "GROUP BY date(timestamp), model ORDER BY date(timestamp)",
                date_params,
            ).fetchall()
            _by_date: dict[str, _DateSavings] = {}
            for day, model, _reqs, comp, product_cr, _client_cr, _unknown_cr in date_rows:
                comp = comp or 0
                product_cr = product_cr or 0
                bucket = _by_date.setdefault(
                    day, {"date": day, "tokens_saved": 0, "cost_saved_usd": 0.0}
                )
                bucket["tokens_saved"] += int(comp + product_cr)
                bucket["cost_saved_usd"] += _estimate_bucket_savings_usd(model, comp, product_cr)
            savings_by_date_7d = [
                {
                    "date": b["date"],
                    "tokens_saved": b["tokens_saved"],
                    "cost_saved_usd": round(b["cost_saved_usd"], 4),
                }
                for b in _by_date.values()
            ]
        finally:
            conn.close()
        return {
            "total_requests": total_requests,
            "total_tokens_saved": total_tokens_saved,
            "total_cost_saved_usd": round(total_cost_saved, 4),
            "savings_by_model": savings_by_model,
            "savings_by_date_7d": savings_by_date_7d,
            # Cache reads observed but NOT credited as product savings:
            "client_cache_read_tokens": int(client_cache_read_tokens),
            "client_cache_read_est_usd_not_counted": round(client_cache_read_est_usd, 4),
            "unknown_origin_cache_read_tokens": int(unknown_cache_read_tokens),
        }

    def recent(self, limit: int = 20) -> list[dict[str, object]]:
        conn = self._read_connection()
        # Local, per-call connection — setting row_factory here cannot leak
        # into other readers (the old shared-connection version mutated
        # row_factory for everyone).
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT * FROM requests ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        finally:
            conn.close()
        return [dict(r) for r in rows]
