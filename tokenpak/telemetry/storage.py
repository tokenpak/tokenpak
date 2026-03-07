"""SQLite storage adapter for TokenPak telemetry.

Provides a lightweight, zero-dependency persistence layer that stores
:class:`~tokenpak.telemetry.models.TelemetryEvent`,
:class:`~tokenpak.telemetry.models.Segment`,
:class:`~tokenpak.telemetry.models.Usage`, and
:class:`~tokenpak.telemetry.models.Cost` records in a local SQLite
database, plus a ``tp_pricing_catalog`` table for caching catalog snapshots.

Usage::

    from tokenpak.telemetry.storage import TelemetryDB

    db = TelemetryDB(":memory:")          # or a file path
    db.insert_trace(event, usage, cost, segments)

    trace = db.get_trace("trace-id-abc")  # returns dict
    rows  = db.list_traces(limit=50)
    segs  = db.get_segments("trace-id-abc")

    db.prune(days=30)                     # delete events older than 30 days
    db.close()

All write helpers accept either single objects or lists for batch inserts.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional, Union

from tokenpak.telemetry.models import Cost, Segment, TelemetryEvent, Usage

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_DDL = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS tp_events (
    trace_id        TEXT NOT NULL,
    request_id      TEXT NOT NULL DEFAULT '',
    event_type      TEXT NOT NULL DEFAULT '',
    ts              REAL NOT NULL DEFAULT 0,
    provider        TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    agent_id        TEXT NOT NULL DEFAULT '',
    api             TEXT NOT NULL DEFAULT '',
    stop_reason     TEXT NOT NULL DEFAULT '',
    session_id      TEXT NOT NULL DEFAULT '',
    duration_ms     REAL NOT NULL DEFAULT 0,
    status          TEXT NOT NULL DEFAULT 'ok',
    error_class     TEXT,
    payload         TEXT NOT NULL DEFAULT '{}',
    span_id         TEXT NOT NULL DEFAULT '',
    node_id         TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (trace_id, request_id, event_type)
);

CREATE TABLE IF NOT EXISTS tp_segments (
    trace_id            TEXT NOT NULL,
    segment_id          TEXT NOT NULL,
    ord                 INTEGER NOT NULL DEFAULT 0,
    segment_type        TEXT NOT NULL DEFAULT '',
    raw_hash            TEXT NOT NULL DEFAULT '',
    final_hash          TEXT NOT NULL DEFAULT '',
    raw_len             INTEGER NOT NULL DEFAULT 0,
    final_len           INTEGER NOT NULL DEFAULT 0,
    tokens_raw          INTEGER NOT NULL DEFAULT 0,
    tokens_after_qmd    INTEGER NOT NULL DEFAULT 0,
    tokens_after_tp     INTEGER NOT NULL DEFAULT 0,
    actions             TEXT NOT NULL DEFAULT '[]',
    relevance_score     REAL NOT NULL DEFAULT 0.5,
    segment_source      TEXT NOT NULL DEFAULT '',
    content_type        TEXT NOT NULL DEFAULT 'text',
    raw_len_chars       INTEGER NOT NULL DEFAULT 0,
    raw_len_bytes       INTEGER NOT NULL DEFAULT 0,
    final_len_chars     INTEGER NOT NULL DEFAULT 0,
    final_len_bytes     INTEGER NOT NULL DEFAULT 0,
    debug_ref           TEXT,
    PRIMARY KEY (trace_id, segment_id)
);

CREATE TABLE IF NOT EXISTS tp_usage (
    trace_id              TEXT NOT NULL PRIMARY KEY,
    usage_source          TEXT NOT NULL DEFAULT 'unknown',
    confidence            TEXT NOT NULL DEFAULT 'low',
    input_billed          INTEGER NOT NULL DEFAULT 0,
    output_billed         INTEGER NOT NULL DEFAULT 0,
    input_est             INTEGER NOT NULL DEFAULT 0,
    output_est            INTEGER NOT NULL DEFAULT 0,
    cache_read            INTEGER NOT NULL DEFAULT 0,
    cache_write           INTEGER NOT NULL DEFAULT 0,
    total_tokens          INTEGER NOT NULL DEFAULT 0,
    total_tokens_billed   INTEGER NOT NULL DEFAULT 0,
    total_tokens_est      INTEGER NOT NULL DEFAULT 0,
    provider_usage_raw    TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS tp_costs (
    trace_id                TEXT NOT NULL PRIMARY KEY,
    cost_input              REAL NOT NULL DEFAULT 0,
    cost_output             REAL NOT NULL DEFAULT 0,
    cost_cache_read         REAL NOT NULL DEFAULT 0,
    cost_cache_write        REAL NOT NULL DEFAULT 0,
    cost_total              REAL NOT NULL DEFAULT 0,
    cost_source             TEXT NOT NULL DEFAULT 'provider',
    pricing_version         TEXT NOT NULL DEFAULT 'v1',
    baseline_input_tokens   INTEGER NOT NULL DEFAULT 0,
    actual_input_tokens     INTEGER NOT NULL DEFAULT 0,
    output_tokens           INTEGER NOT NULL DEFAULT 0,
    baseline_cost           REAL NOT NULL DEFAULT 0,
    actual_cost             REAL NOT NULL DEFAULT 0,
    savings_total           REAL NOT NULL DEFAULT 0,
    savings_qmd             REAL NOT NULL DEFAULT 0,
    savings_tp              REAL NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tp_pricing_catalog (
    version         TEXT NOT NULL PRIMARY KEY,
    captured_at     REAL NOT NULL DEFAULT 0,
    catalog_json    TEXT NOT NULL DEFAULT '{}'
);

-- Rollup tables (Phase 5B)
CREATE TABLE IF NOT EXISTS tp_rollup_daily_model (
    date            TEXT NOT NULL,
    model           TEXT NOT NULL,
    total_requests  INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    total_cost      REAL NOT NULL DEFAULT 0,
    total_savings   REAL NOT NULL DEFAULT 0,
    avg_raw_tokens  REAL NOT NULL DEFAULT 0,
    avg_final_tokens REAL NOT NULL DEFAULT 0,
    avg_cost        REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (date, model)
);

CREATE TABLE IF NOT EXISTS tp_rollup_daily_provider (
    date            TEXT NOT NULL,
    provider        TEXT NOT NULL,
    total_requests  INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    total_cost      REAL NOT NULL DEFAULT 0,
    total_savings   REAL NOT NULL DEFAULT 0,
    avg_raw_tokens  REAL NOT NULL DEFAULT 0,
    avg_final_tokens REAL NOT NULL DEFAULT 0,
    avg_cost        REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (date, provider)
);

CREATE TABLE IF NOT EXISTS tp_rollup_daily_agent (
    date            TEXT NOT NULL,
    agent_id        TEXT NOT NULL,
    total_requests  INTEGER NOT NULL DEFAULT 0,
    total_tokens    INTEGER NOT NULL DEFAULT 0,
    total_cost      REAL NOT NULL DEFAULT 0,
    total_savings   REAL NOT NULL DEFAULT 0,
    avg_raw_tokens  REAL NOT NULL DEFAULT 0,
    avg_final_tokens REAL NOT NULL DEFAULT 0,
    avg_cost        REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (date, agent_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_events_trace
    ON tp_events (trace_id);
CREATE INDEX IF NOT EXISTS idx_events_provider
    ON tp_events (provider);
CREATE INDEX IF NOT EXISTS idx_events_model
    ON tp_events (model);
CREATE INDEX IF NOT EXISTS idx_events_agent
    ON tp_events (agent_id);
CREATE INDEX IF NOT EXISTS idx_events_ts
    ON tp_events (ts);
CREATE INDEX IF NOT EXISTS idx_segments_trace
    ON tp_segments (trace_id);
CREATE INDEX IF NOT EXISTS idx_rollup_model_date
    ON tp_rollup_daily_model (date);
CREATE INDEX IF NOT EXISTS idx_rollup_provider_date
    ON tp_rollup_daily_provider (date);
CREATE INDEX IF NOT EXISTS idx_rollup_agent_date
    ON tp_rollup_daily_agent (date);
"""

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _row_to_dict(cursor: sqlite3.Cursor, row: sqlite3.Row) -> dict[str, Any]:
    """Convert a ``sqlite3.Row`` to a plain dict."""
    return dict(zip([col[0] for col in cursor.description], row))


def _now() -> float:
    """Return the current Unix timestamp."""
    return time.time()


# ---------------------------------------------------------------------------
# Main DB class
# ---------------------------------------------------------------------------


class TelemetryDB:
    """SQLite-backed telemetry store.

    Parameters
    ----------
    path:
        Path to the SQLite database file.  Pass ``":memory:"`` for an
        in-memory database (useful for testing).
    """

    def __init__(self, path: Union[str, Path] = ":memory:") -> None:
        self._path = str(path)
        self._conn: sqlite3.Connection = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._apply_ddl()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def _apply_ddl(self) -> None:
        """Create tables and indexes if they don't already exist."""
        self._conn.executescript(_DDL)
        self._migrate_schema()
        self._conn.commit()

    def _migrate_schema(self) -> None:
        """Apply schema migrations for existing databases.

        All ALTER TABLE statements are wrapped in try/except so this
        method is fully idempotent — safe to call on both fresh and
        existing databases.
        """
        cur = self._conn.cursor()

        def _add_col(table: str, col: str, typedef: str) -> None:
            """Add *col* to *table* if it doesn't already exist."""
            try:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # Column already exists — safe to ignore

        # ----------------------------------------------------------------
        # tp_costs: handle legacy schema that had actual_cost but no cost_total
        # ----------------------------------------------------------------
        cur.execute("PRAGMA table_info(tp_costs)")
        costs_cols = {row[1]: row for row in cur.fetchall()}

        if "actual_cost" in costs_cols and "cost_total" not in costs_cols:
            cur.execute("DROP TABLE IF EXISTS tp_costs")
            cur.execute("""
                CREATE TABLE tp_costs (
                    trace_id                TEXT NOT NULL PRIMARY KEY,
                    cost_input              REAL NOT NULL DEFAULT 0,
                    cost_output             REAL NOT NULL DEFAULT 0,
                    cost_cache_read         REAL NOT NULL DEFAULT 0,
                    cost_cache_write        REAL NOT NULL DEFAULT 0,
                    cost_total              REAL NOT NULL DEFAULT 0,
                    cost_source             TEXT NOT NULL DEFAULT 'provider',
                    pricing_version         TEXT NOT NULL DEFAULT 'v1',
                    baseline_input_tokens   INTEGER NOT NULL DEFAULT 0,
                    actual_input_tokens     INTEGER NOT NULL DEFAULT 0,
                    output_tokens           INTEGER NOT NULL DEFAULT 0,
                    baseline_cost           REAL NOT NULL DEFAULT 0,
                    actual_cost             REAL NOT NULL DEFAULT 0,
                    savings_total           REAL NOT NULL DEFAULT 0,
                    savings_qmd             REAL NOT NULL DEFAULT 0,
                    savings_tp              REAL NOT NULL DEFAULT 0
                )
            """)

        # ----------------------------------------------------------------
        # tp_events — legacy columns + PRD additions
        # ----------------------------------------------------------------
        _add_col("tp_events", "api", "TEXT NOT NULL DEFAULT ''")
        _add_col("tp_events", "stop_reason", "TEXT NOT NULL DEFAULT ''")
        _add_col("tp_events", "session_id", "TEXT NOT NULL DEFAULT ''")
        _add_col("tp_events", "duration_ms", "REAL NOT NULL DEFAULT 0")
        # PRD additions
        _add_col("tp_events", "span_id", "TEXT NOT NULL DEFAULT ''")
        _add_col("tp_events", "node_id", "TEXT NOT NULL DEFAULT ''")

        # ----------------------------------------------------------------
        # tp_segments — PRD additions
        # ----------------------------------------------------------------
        _add_col("tp_segments", "segment_source", "TEXT NOT NULL DEFAULT ''")
        _add_col("tp_segments", "content_type", "TEXT NOT NULL DEFAULT 'text'")
        _add_col("tp_segments", "raw_len_chars", "INTEGER NOT NULL DEFAULT 0")
        _add_col("tp_segments", "raw_len_bytes", "INTEGER NOT NULL DEFAULT 0")
        _add_col("tp_segments", "final_len_chars", "INTEGER NOT NULL DEFAULT 0")
        _add_col("tp_segments", "final_len_bytes", "INTEGER NOT NULL DEFAULT 0")
        _add_col("tp_segments", "debug_ref", "TEXT")

        # ----------------------------------------------------------------
        # tp_usage — legacy column + PRD additions
        # ----------------------------------------------------------------
        _add_col("tp_usage", "total_tokens", "INTEGER NOT NULL DEFAULT 0")
        _add_col("tp_usage", "total_tokens_billed", "INTEGER NOT NULL DEFAULT 0")
        _add_col("tp_usage", "total_tokens_est", "INTEGER NOT NULL DEFAULT 0")
        _add_col("tp_usage", "provider_usage_raw", "TEXT NOT NULL DEFAULT '{}'")

        # ----------------------------------------------------------------
        # tp_costs — legacy column + PRD additions
        # ----------------------------------------------------------------
        _add_col("tp_costs", "baseline_input_tokens", "INTEGER NOT NULL DEFAULT 0")
        _add_col("tp_costs", "pricing_version", "TEXT NOT NULL DEFAULT 'v1'")
        _add_col("tp_costs", "actual_input_tokens", "INTEGER NOT NULL DEFAULT 0")
        _add_col("tp_costs", "output_tokens", "INTEGER NOT NULL DEFAULT 0")
        _add_col("tp_costs", "actual_cost", "REAL NOT NULL DEFAULT 0")

        # ----------------------------------------------------------------
        # Rollup tables — PRD avg_* additions
        # ----------------------------------------------------------------
        for _tbl in ("tp_rollup_daily_model", "tp_rollup_daily_provider", "tp_rollup_daily_agent"):
            _add_col(_tbl, "avg_raw_tokens", "REAL NOT NULL DEFAULT 0")
            _add_col(_tbl, "avg_final_tokens", "REAL NOT NULL DEFAULT 0")
            _add_col(_tbl, "avg_cost", "REAL NOT NULL DEFAULT 0")

    def close(self) -> None:
        """Close the underlying database connection."""
        self._conn.close()

    def __enter__(self) -> "TelemetryDB":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Insert helpers (single + batch)
    # ------------------------------------------------------------------

    def insert_event(self, event: TelemetryEvent) -> None:
        """Persist a single :class:`TelemetryEvent`."""
        self._insert_events([event])

    def insert_events(self, events: list[TelemetryEvent]) -> None:
        """Batch-insert a list of :class:`TelemetryEvent` records."""
        self._insert_events(events)

    def _insert_events(self, events: list[TelemetryEvent]) -> None:
        sql = """
        INSERT OR REPLACE INTO tp_events
            (trace_id, request_id, event_type, ts, provider, model,
             agent_id, api, stop_reason, session_id, duration_ms,
             status, error_class, payload, span_id, node_id)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        rows = [
            (
                e.trace_id,
                e.request_id,
                e.event_type,
                e.ts if e.ts else _now(),
                e.provider,
                e.model,
                e.agent_id,
                e.api,
                e.stop_reason,
                e.session_id,
                e.duration_ms,
                e.status,
                e.error_class,
                e.payload_json(),
                e.span_id,
                e.node_id,
            )
            for e in events
        ]
        self._conn.executemany(sql, rows)
        self._conn.commit()

    def insert_usage(self, usage: Usage) -> None:
        """Persist a single :class:`Usage` record."""
        self._insert_usages([usage])

    def insert_usages(self, usages: list[Usage]) -> None:
        """Batch-insert a list of :class:`Usage` records."""
        self._insert_usages(usages)

    def _insert_usages(self, usages: list[Usage]) -> None:
        sql = """
        INSERT OR REPLACE INTO tp_usage
            (trace_id, usage_source, confidence, input_billed, output_billed,
             input_est, output_est, cache_read, cache_write, total_tokens,
             total_tokens_billed, total_tokens_est, provider_usage_raw)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        rows = [
            (
                u.trace_id,
                u.usage_source,
                u.confidence,
                u.input_billed,
                u.output_billed,
                u.input_est,
                u.output_est,
                u.cache_read,
                u.cache_write,
                u.total_tokens,
                u.total_tokens_billed,
                u.total_tokens_est,
                u.provider_usage_raw,
            )
            for u in usages
        ]
        self._conn.executemany(sql, rows)
        self._conn.commit()

    def insert_cost(self, cost: Cost) -> None:
        """Persist a single :class:`Cost` record."""
        self._insert_costs([cost])

    def insert_costs(self, costs: list[Cost]) -> None:
        """Batch-insert a list of :class:`Cost` records."""
        self._insert_costs(costs)

    def _insert_costs(self, costs: list[Cost]) -> None:
        sql = """
        INSERT OR REPLACE INTO tp_costs
            (trace_id, cost_input, cost_output, cost_cache_read,
             cost_cache_write, cost_total, cost_source, pricing_version,
             baseline_input_tokens, actual_input_tokens, output_tokens,
             baseline_cost, actual_cost, savings_total, savings_qmd, savings_tp)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        rows = [
            (
                c.trace_id,
                c.cost_input,
                c.cost_output,
                c.cost_cache_read,
                c.cost_cache_write,
                c.cost_total,
                c.cost_source,
                c.pricing_version,
                c.baseline_input_tokens,
                c.actual_input_tokens,
                c.output_tokens,
                c.baseline_cost,
                c.actual_cost,
                c.savings_total,
                c.savings_qmd,
                c.savings_tp,
            )
            for c in costs
        ]
        self._conn.executemany(sql, rows)
        self._conn.commit()

    def insert_segment(self, segment: Segment) -> None:
        """Persist a single :class:`Segment` record."""
        self._insert_segments([segment])

    def insert_segments(self, segments: list[Segment]) -> None:
        """Batch-insert a list of :class:`Segment` records."""
        self._insert_segments(segments)

    def _insert_segments(self, segments: list[Segment]) -> None:
        sql = """
        INSERT OR REPLACE INTO tp_segments
            (trace_id, segment_id, ord, segment_type, raw_hash, final_hash,
             raw_len, final_len, tokens_raw, tokens_after_qmd,
             tokens_after_tp, actions, relevance_score,
             segment_source, content_type, raw_len_chars, raw_len_bytes,
             final_len_chars, final_len_bytes, debug_ref)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        rows = [
            (
                s.trace_id,
                s.segment_id,
                s.order,
                s.segment_type,
                s.raw_hash,
                s.final_hash,
                s.raw_len,
                s.final_len,
                s.tokens_raw,
                s.tokens_after_qmd,
                s.tokens_after_tp,
                s.actions,
                s.relevance_score,
                s.segment_source,
                s.content_type,
                s.raw_len_chars,
                s.raw_len_bytes,
                s.final_len_chars,
                s.final_len_bytes,
                s.debug_ref,
            )
            for s in segments
        ]
        self._conn.executemany(sql, rows)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Compound insert_trace (convenience)
    # ------------------------------------------------------------------

    def insert_trace(
        self,
        event: TelemetryEvent,
        usage: Optional[Usage] = None,
        cost: Optional[Cost] = None,
        segments: Optional[list[Segment]] = None,
    ) -> None:
        """Insert all data for a single trace in one call.

        This is the preferred entry point for recording a completed LLM
        request/response cycle.  All four tables are updated atomically.

        Parameters
        ----------
        event:
            The lifecycle event for this trace.
        usage:
            Optional token-usage record.
        cost:
            Optional cost computation result.
        segments:
            Optional list of classified message segments.
        """
        self.insert_event(event)
        if usage is not None:
            self.insert_usage(usage)
        if cost is not None:
            self.insert_cost(cost)
        if segments:
            self.insert_segments(segments)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_trace(self, trace_id: str) -> dict[str, Any]:
        """Return all stored data for *trace_id* as a plain dict.

        Returns a dict with keys ``"event"``, ``"usage"``, ``"cost"``,
        ``"segments"``.  Missing tables return ``None`` / ``[]``.

        Parameters
        ----------
        trace_id:
            The trace identifier to look up.
        """
        cur = self._conn.cursor()

        cur.execute("SELECT * FROM tp_events WHERE trace_id = ? LIMIT 1", (trace_id,))
        event_row = cur.fetchone()
        event = _row_to_dict(cur, event_row) if event_row else None

        cur.execute("SELECT * FROM tp_usage WHERE trace_id = ?", (trace_id,))
        usage_row = cur.fetchone()
        usage = _row_to_dict(cur, usage_row) if usage_row else None

        cur.execute("SELECT * FROM tp_costs WHERE trace_id = ?", (trace_id,))
        cost_row = cur.fetchone()
        cost = _row_to_dict(cur, cost_row) if cost_row else None

        segments = self.get_segments(trace_id)

        return {
            "event": event,
            "usage": usage,
            "cost": cost,
            "segments": segments,
        }

    def get_segments(self, trace_id: str) -> list[dict[str, Any]]:
        """Return all segment rows for *trace_id*, ordered by ``ord``.

        Parameters
        ----------
        trace_id:
            The trace identifier.
        """
        cur = self._conn.cursor()
        cur.execute(
            "SELECT * FROM tp_segments WHERE trace_id = ? ORDER BY ord",
            (trace_id,),
        )
        rows = cur.fetchall()
        result = []
        for r in rows:
            d = _row_to_dict(cur, r)
            # Remap DB column 'ord' → dataclass field 'order'
            if "ord" in d and "order" not in d:
                d["order"] = d.pop("ord")
            result.append(d)
        return result

    def get_trace_events(self, trace_id: str) -> list[dict[str, Any]]:
        """Return all event rows for *trace_id*, ordered chronologically by timestamp.

        Parameters
        ----------
        trace_id:
            The trace identifier.

        Returns
        -------
        list of event dicts with event_id (request_id), event_type, ts, and payload
        """
        cur = self._conn.cursor()
        cur.execute(
            """SELECT request_id, event_type, ts, provider, model, agent_id,
                      api, stop_reason, session_id, duration_ms, status, error_class, payload
               FROM tp_events WHERE trace_id = ? ORDER BY ts ASC""",
            (trace_id,),
        )
        rows = cur.fetchall()
        events = []
        for r in rows:
            row_dict = _row_to_dict(cur, r)
            # Parse payload JSON
            payload_str = row_dict.pop("payload", "{}")
            try:
                payload = json.loads(payload_str) if payload_str else {}
            except (json.JSONDecodeError, TypeError):
                payload = {}
            events.append(
                {
                    "event_id": row_dict["request_id"],
                    "event_type": row_dict["event_type"],
                    "timestamp": row_dict["ts"],
                    "provider": row_dict.get("provider"),
                    "model": row_dict.get("model"),
                    "agent_id": row_dict.get("agent_id"),
                    "duration_ms": row_dict.get("duration_ms"),
                    "status": row_dict.get("status"),
                    "payload": payload,
                }
            )
        return events

    def list_traces(
        self,
        limit: int = 100,
        offset: int = 0,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
        since_ts: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """Return a paginated list of trace event summaries.

        Parameters
        ----------
        limit:
            Maximum number of rows to return.
        offset:
            Row offset for pagination.
        provider:
            Filter by provider (exact match, case-sensitive).
        model:
            Filter by model (exact match, case-sensitive).
        agent_id:
            Filter by agent identifier.
        since_ts:
            Only return events with ``ts >= since_ts``.
        """
        conditions: list[str] = []
        params: list[Any] = []

        if provider is not None:
            conditions.append("provider = ?")
            params.append(provider)
        if model is not None:
            conditions.append("model = ?")
            params.append(model)
        if agent_id is not None:
            conditions.append("agent_id = ?")
            params.append(agent_id)
        if since_ts is not None:
            conditions.append("ts >= ?")
            params.append(since_ts)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"""
        SELECT
            e.trace_id,
            e.ts,
            datetime(e.ts, 'unixepoch') AS ts_iso,
            e.provider,
            e.model,
            e.agent_id,
            e.status,
            e.duration_ms,
            u.input_billed,
            u.output_billed,
            u.total_tokens_billed,
            c.cost_total    AS actual_cost,
            c.savings_total AS savings_total
        FROM tp_events e
        LEFT JOIN tp_usage u ON u.trace_id = e.trace_id
        LEFT JOIN tp_costs c ON c.trace_id = e.trace_id
        {where}
        ORDER BY e.ts DESC
        LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])

        cur = self._conn.cursor()
        cur.execute(sql, params)
        rows = cur.fetchall()
        return [_row_to_dict(cur, r) for r in rows]

    # ------------------------------------------------------------------
    # Pricing catalog snapshot
    # ------------------------------------------------------------------

    def upsert_pricing_catalog(self, version: str, catalog_json: str) -> None:
        """Store a JSON snapshot of the pricing catalog.

        Parameters
        ----------
        version:
            Catalog version string (e.g. ``"v1"``).
        catalog_json:
            The serialised catalog data.
        """
        sql = """
        INSERT OR REPLACE INTO tp_pricing_catalog (version, captured_at, catalog_json)
        VALUES (?, ?, ?)
        """
        self._conn.execute(sql, (version, _now(), catalog_json))
        self._conn.commit()

    def get_pricing_catalog(self, version: str) -> Optional[dict[str, Any]]:
        """Retrieve a stored pricing catalog snapshot by version.

        Returns ``None`` if no snapshot for *version* exists.
        """
        cur = self._conn.cursor()
        cur.execute(
            "SELECT catalog_json FROM tp_pricing_catalog WHERE version = ?",
            (version,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return json.loads(row[0])

    # ------------------------------------------------------------------
    # Retention / pruning
    # ------------------------------------------------------------------

    def prune(self, days: int = 90) -> int:
        """Delete events (and associated data) older than *days* days.

        Cascades to ``tp_usage``, ``tp_costs``, and ``tp_segments`` for
        any trace_id that no longer has a matching event.

        Parameters
        ----------
        days:
            Events with ``ts < (now - days * 86400)`` are deleted.

        Returns
        -------
        int
            Number of event rows deleted.
        """
        cutoff = _now() - days * 86_400
        cur = self._conn.cursor()

        # Collect trace_ids to prune
        cur.execute("SELECT DISTINCT trace_id FROM tp_events WHERE ts < ?", (cutoff,))
        old_traces = [r[0] for r in cur.fetchall()]

        if not old_traces:
            return 0

        placeholders = ",".join("?" * len(old_traces))

        cur.execute(
            f"DELETE FROM tp_events WHERE trace_id IN ({placeholders})",
            old_traces,
        )
        deleted = cur.rowcount

        cur.execute(
            f"DELETE FROM tp_usage WHERE trace_id IN ({placeholders})",
            old_traces,
        )
        cur.execute(
            f"DELETE FROM tp_costs WHERE trace_id IN ({placeholders})",
            old_traces,
        )
        cur.execute(
            f"DELETE FROM tp_segments WHERE trace_id IN ({placeholders})",
            old_traces,
        )

        self._conn.commit()
        return deleted

    # ------------------------------------------------------------------
    # Three-Stage Cost Ledger: backfill baseline costs
    # ------------------------------------------------------------------

    def backfill_baseline_costs(self, dry_run: bool = False) -> dict[str, int]:
        """Populate ``baseline_input_tokens`` and ``baseline_cost`` for
        existing traces that were inserted without compression data.

        Algorithm
        ---------
        For each trace in ``tp_costs`` where ``baseline_cost = 0``:

        1. Sum ``tokens_raw`` from ``tp_segments`` →
           ``baseline_input_tokens``.
        2. If no segment data, fall back to
           ``input_billed + cache_read`` from ``tp_usage`` as a proxy.
        3. Look up the model from ``tp_events`` and call
           :func:`~tokenpak.telemetry.pricing.compute_baseline_cost`.
        4. Compute ``savings_total = baseline_cost - cost_total``
           (floored at 0).
        5. UPDATE ``tp_costs`` unless *dry_run* is True.

        Parameters
        ----------
        dry_run:
            If True, compute everything but do not write to the DB.

        Returns
        -------
        dict
            ``{"eligible": N, "updated": N, "skipped": N}`` counts.
        """
        from tokenpak.telemetry.pricing import compute_baseline_cost as _cbc

        cur = self._conn.cursor()

        # Find traces that need baseline costs computed
        cur.execute("""
            SELECT c.trace_id, c.cost_total
            FROM tp_costs c
            WHERE c.baseline_cost = 0 AND c.baseline_input_tokens = 0
        """)
        rows = cur.fetchall()

        eligible = len(rows)
        updated = 0
        skipped = 0

        for row in rows:
            trace_id = row[0]
            cost_total = row[1]

            # Step 1: Try to get raw token count from segments
            cur.execute(
                "SELECT COALESCE(SUM(tokens_raw), 0) FROM tp_segments WHERE trace_id = ?",
                (trace_id,),
            )
            seg_row = cur.fetchone()
            baseline_input_tokens: int = seg_row[0] if seg_row else 0

            # Step 2: Fall back to usage proxy if no segment data
            if baseline_input_tokens == 0:
                cur.execute(
                    "SELECT COALESCE(input_billed, 0), COALESCE(cache_read, 0) "
                    "FROM tp_usage WHERE trace_id = ?",
                    (trace_id,),
                )
                usage_row = cur.fetchone()
                if usage_row:
                    baseline_input_tokens = usage_row[0] + usage_row[1]

            if baseline_input_tokens == 0:
                skipped += 1
                continue

            # Step 3: Look up model
            cur.execute(
                "SELECT model FROM tp_events WHERE trace_id = ? LIMIT 1",
                (trace_id,),
            )
            ev_row = cur.fetchone()
            model = ev_row[0] if ev_row else ""
            if not model:
                skipped += 1
                continue

            # Step 4: Compute baseline cost and savings
            baseline_cost = _cbc(model, baseline_input_tokens)
            if baseline_cost == 0.0:
                skipped += 1
                continue

            savings_total = max(0.0, baseline_cost - cost_total)

            # Step 5: Persist
            if not dry_run:
                cur.execute(
                    """UPDATE tp_costs
                       SET baseline_input_tokens = ?,
                           baseline_cost = ?,
                           savings_total = ?
                       WHERE trace_id = ?""",
                    (baseline_input_tokens, baseline_cost, savings_total, trace_id),
                )

            updated += 1

        if not dry_run:
            self._conn.commit()

        return {"eligible": eligible, "updated": updated, "skipped": skipped}

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Return row counts for each telemetry table."""
        cur = self._conn.cursor()
        result: dict[str, int] = {}
        for table in ("tp_events", "tp_segments", "tp_usage", "tp_costs"):
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            result[table] = cur.fetchone()[0]

        rollup_tables = (
            "tp_rollup_daily_model",
            "tp_rollup_daily_provider",
            "tp_rollup_daily_agent",
        )
        rollup_total = 0
        for table in rollup_tables:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            count = cur.fetchone()[0]
            result[table] = count
            rollup_total += count
        result["tp_rollups"] = rollup_total
        return result

    # ------------------------------------------------------------------
    # Phase 5B: Query API methods
    # ------------------------------------------------------------------

    def get_summary(
        self,
        provider: Optional[str] = None,
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> dict[str, Any]:
        """Return aggregate summary statistics.

        Parameters
        ----------
        provider, model, agent_id:
            Optional filters to narrow the summary.

        Returns
        -------
        dict
            Keys: total_requests, total_tokens, total_cost, total_savings,
            by_provider, by_model, by_agent.
        """
        cur = self._conn.cursor()
        conditions: list[str] = []
        params: list[Any] = []

        if provider:
            conditions.append("e.provider = ?")
            params.append(provider)
        if model:
            conditions.append("e.model = ?")
            params.append(model)
        if agent_id:
            conditions.append("e.agent_id = ?")
            params.append(agent_id)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        # Total aggregates
        sql = f"""
            SELECT
                COUNT(DISTINCT e.trace_id) as total_requests,
                COALESCE(SUM(u.input_billed + u.output_billed), 0) as total_tokens,
                COALESCE(SUM(c.cost_total), 0) as total_cost,
                COALESCE(SUM(c.savings_total), 0) as total_savings
            FROM tp_events e
            LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            {where}
        """
        cur.execute(sql, params)
        row = cur.fetchone()
        totals = _row_to_dict(cur, row) if row else {}

        # By provider
        sql_provider = f"""
            SELECT e.provider,
                   COUNT(DISTINCT e.trace_id) as requests,
                   COALESCE(SUM(c.cost_total), 0) as cost,
                   COALESCE(SUM(c.savings_total), 0) as savings
            FROM tp_events e
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            {where}
            GROUP BY e.provider
        """
        cur.execute(sql_provider, params)
        by_provider = [_row_to_dict(cur, r) for r in cur.fetchall()]

        # By model
        sql_model = f"""
            SELECT e.model,
                   COUNT(DISTINCT e.trace_id) as requests,
                   COALESCE(SUM(c.cost_total), 0) as cost,
                   COALESCE(SUM(c.savings_total), 0) as savings
            FROM tp_events e
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            {where}
            GROUP BY e.model
        """
        cur.execute(sql_model, params)
        by_model = [_row_to_dict(cur, r) for r in cur.fetchall()]

        # By agent
        sql_agent = f"""
            SELECT e.agent_id,
                   COUNT(DISTINCT e.trace_id) as requests,
                   COALESCE(SUM(c.cost_total), 0) as cost,
                   COALESCE(SUM(c.savings_total), 0) as savings
            FROM tp_events e
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            {where}
            GROUP BY e.agent_id
        """
        cur.execute(sql_agent, params)
        by_agent = [_row_to_dict(cur, r) for r in cur.fetchall()]

        return {
            **totals,
            "by_provider": by_provider,
            "by_model": by_model,
            "by_agent": by_agent,
        }

    def get_timeseries(
        self,
        metric: str = "cost",
        interval: str = "hour",
        provider: Optional[str] = None,
        model: Optional[str] = None,
        agent_id: Optional[str] = None,
        since_ts: Optional[float] = None,
    ) -> list[dict[str, Any]]:
        """Return time-bucketed metric data for charting.

        Parameters
        ----------
        metric:
            One of ``"cost"``, ``"tokens"``, ``"savings"``, ``"requests"``.
        interval:
            Time bucket size: ``"hour"`` or ``"day"``.
        provider, model, agent_id:
            Optional filters.
        since_ts:
            Only include data from this timestamp onwards.

        Returns
        -------
        list[dict]
            Each dict has ``bucket`` (ISO timestamp) and ``value``.
        """
        cur = self._conn.cursor()
        conditions: list[str] = []
        params: list[Any] = []

        if provider:
            conditions.append("e.provider = ?")
            params.append(provider)
        if model:
            conditions.append("e.model = ?")
            params.append(model)
        if agent_id:
            conditions.append("e.agent_id = ?")
            params.append(agent_id)
        if since_ts:
            conditions.append("e.ts >= ?")
            params.append(since_ts)

        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""

        # Time bucket format
        if interval == "day":
            bucket_expr = "strftime('%Y-%m-%d', datetime(e.ts, 'unixepoch'))"
        else:  # hour
            bucket_expr = "strftime('%Y-%m-%dT%H:00:00', datetime(e.ts, 'unixepoch'))"

        # Metric expression
        metric_map = {
            "cost": "COALESCE(SUM(c.cost_total), 0)",
            "tokens": "COALESCE(SUM(u.input_billed + u.output_billed), 0)",
            "savings": "COALESCE(SUM(c.savings_total), 0)",
            "requests": "COUNT(DISTINCT e.trace_id)",
        }
        metric_expr = metric_map.get(metric, metric_map["cost"])

        sql = f"""
            SELECT {bucket_expr} as bucket, {metric_expr} as value
            FROM tp_events e
            LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            {where}
            GROUP BY bucket
            ORDER BY bucket ASC
        """
        cur.execute(sql, params)
        return [_row_to_dict(cur, r) for r in cur.fetchall()]

    def get_unique_models(self) -> list[str]:
        """Return list of unique model identifiers seen."""
        cur = self._conn.cursor()
        cur.execute("SELECT DISTINCT model FROM tp_events WHERE model != '' ORDER BY model")
        return [r[0] for r in cur.fetchall()]

    def get_unique_providers(self) -> list[str]:
        """Return list of unique provider names seen."""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT DISTINCT provider FROM tp_events WHERE provider != '' ORDER BY provider"
        )
        return [r[0] for r in cur.fetchall()]

    def get_unique_agents(self) -> list[str]:
        """Return list of unique agent identifiers seen."""
        cur = self._conn.cursor()
        cur.execute(
            "SELECT DISTINCT agent_id FROM tp_events WHERE agent_id != '' ORDER BY agent_id"
        )
        return [r[0] for r in cur.fetchall()]

    def export_trace(self, trace_id: str) -> dict[str, Any]:
        """Export a complete trace bundle as JSON-serializable dict.

        Includes event, usage, cost, segments, and metadata.
        """
        trace = self.get_trace(trace_id)
        return {
            "format": "tokenpak_trace_export_v1",
            "trace_id": trace_id,
            "exported_at": _now(),
            **trace,
        }

    # ------------------------------------------------------------------
    # Rollup computation (Phase 5B)
    # ------------------------------------------------------------------

    def compute_rollups(self) -> dict[str, int]:
        """Recompute all daily rollup tables from raw data.

        This is idempotent — can be called repeatedly. Replaces existing
        rollup data with fresh aggregates.

        Returns
        -------
        dict
            Counts of rows written to each rollup table.
        """
        cur = self._conn.cursor()
        counts = {}

        # Rollup by model
        cur.execute("DELETE FROM tp_rollup_daily_model")
        cur.execute("""
            INSERT INTO tp_rollup_daily_model (date, model, total_requests, total_tokens, total_cost, total_savings, avg_raw_tokens, avg_final_tokens, avg_cost)
            WITH seg_agg AS (
                SELECT trace_id,
                    AVG(NULLIF(tokens_raw, 0)) as avg_raw,
                    AVG(NULLIF(tokens_after_tp, 0)) as avg_tp
                FROM tp_segments GROUP BY trace_id
            )
            SELECT
                strftime('%Y-%m-%d', datetime(e.ts, 'unixepoch')) as date,
                e.model,
                COUNT(DISTINCT e.trace_id) as total_requests,
                COALESCE(SUM(u.input_billed + u.output_billed), 0) as total_tokens,
                COALESCE(SUM(c.cost_total), 0) as total_cost,
                COALESCE(SUM(c.savings_total), 0) as total_savings,
                COALESCE(AVG(NULLIF(sa.avg_raw, 0)), AVG(u.input_billed + u.output_billed), 0) as avg_raw_tokens,
                COALESCE(AVG(NULLIF(sa.avg_tp, 0)), AVG(u.input_billed + u.output_billed), 0) as avg_final_tokens,
                COALESCE(AVG(c.cost_total), 0) as avg_cost
            FROM tp_events e
            LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            LEFT JOIN seg_agg sa ON e.trace_id = sa.trace_id
            GROUP BY date, e.model
        """)
        counts["model"] = cur.rowcount

        # Rollup by provider
        cur.execute("DELETE FROM tp_rollup_daily_provider")
        cur.execute("""
            INSERT INTO tp_rollup_daily_provider (date, provider, total_requests, total_tokens, total_cost, total_savings, avg_raw_tokens, avg_final_tokens, avg_cost)
            WITH seg_agg AS (
                SELECT trace_id,
                    AVG(NULLIF(tokens_raw, 0)) as avg_raw,
                    AVG(NULLIF(tokens_after_tp, 0)) as avg_tp
                FROM tp_segments GROUP BY trace_id
            )
            SELECT
                strftime('%Y-%m-%d', datetime(e.ts, 'unixepoch')) as date,
                e.provider,
                COUNT(DISTINCT e.trace_id) as total_requests,
                COALESCE(SUM(u.input_billed + u.output_billed), 0) as total_tokens,
                COALESCE(SUM(c.cost_total), 0) as total_cost,
                COALESCE(SUM(c.savings_total), 0) as total_savings,
                COALESCE(AVG(NULLIF(sa.avg_raw, 0)), AVG(u.input_billed + u.output_billed), 0) as avg_raw_tokens,
                COALESCE(AVG(NULLIF(sa.avg_tp, 0)), AVG(u.input_billed + u.output_billed), 0) as avg_final_tokens,
                COALESCE(AVG(c.cost_total), 0) as avg_cost
            FROM tp_events e
            LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            LEFT JOIN seg_agg sa ON e.trace_id = sa.trace_id
            GROUP BY date, e.provider
        """)
        counts["provider"] = cur.rowcount

        # Rollup by agent
        cur.execute("DELETE FROM tp_rollup_daily_agent")
        cur.execute("""
            INSERT INTO tp_rollup_daily_agent (date, agent_id, total_requests, total_tokens, total_cost, total_savings, avg_raw_tokens, avg_final_tokens, avg_cost)
            WITH seg_agg AS (
                SELECT trace_id,
                    AVG(NULLIF(tokens_raw, 0)) as avg_raw,
                    AVG(NULLIF(tokens_after_tp, 0)) as avg_tp
                FROM tp_segments GROUP BY trace_id
            )
            SELECT
                strftime('%Y-%m-%d', datetime(e.ts, 'unixepoch')) as date,
                e.agent_id,
                COUNT(DISTINCT e.trace_id) as total_requests,
                COALESCE(SUM(u.input_billed + u.output_billed), 0) as total_tokens,
                COALESCE(SUM(c.cost_total), 0) as total_cost,
                COALESCE(SUM(c.savings_total), 0) as total_savings,
                COALESCE(AVG(NULLIF(sa.avg_raw, 0)), AVG(u.input_billed + u.output_billed), 0) as avg_raw_tokens,
                COALESCE(AVG(NULLIF(sa.avg_tp, 0)), AVG(u.input_billed + u.output_billed), 0) as avg_final_tokens,
                COALESCE(AVG(c.cost_total), 0) as avg_cost
            FROM tp_events e
            LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            LEFT JOIN seg_agg sa ON e.trace_id = sa.trace_id
            GROUP BY date, e.agent_id
        """)
        counts["agent"] = cur.rowcount

        self._conn.commit()
        return counts

    def get_rollup_timeseries(
        self,
        entity_type: str = "model",
        metric: str = "cost",
        since_date: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        """Query rollup tables for fast timeseries data.

        Parameters
        ----------
        entity_type:
            ``"model"``, ``"provider"``, or ``"agent"``.
        metric:
            Column to return: ``"cost"``, ``"tokens"``, ``"savings"``, ``"requests"``.
        since_date:
            ISO date string (YYYY-MM-DD) to filter from.
        """
        table_map = {
            "model": "tp_rollup_daily_model",
            "provider": "tp_rollup_daily_provider",
            "agent": "tp_rollup_daily_agent",
        }
        col_map = {
            "cost": "total_cost",
            "tokens": "total_tokens",
            "savings": "total_savings",
            "requests": "total_requests",
        }
        table = table_map.get(entity_type, "tp_rollup_daily_model")
        col = col_map.get(metric, "total_cost")
        entity_col = (
            "model"
            if entity_type == "model"
            else ("provider" if entity_type == "provider" else "agent_id")
        )

        cur = self._conn.cursor()
        params: list[Any] = []
        where = ""
        if since_date:
            where = "WHERE date >= ?"
            params.append(since_date)

        sql = f"""
            SELECT date, {entity_col} as entity, {col} as value
            FROM {table}
            {where}
            ORDER BY date ASC, entity ASC
        """
        cur.execute(sql, params)
        return [_row_to_dict(cur, r) for r in cur.fetchall()]
