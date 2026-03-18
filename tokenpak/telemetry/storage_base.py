"""Base storage module: DDL, schema migration, connection management.

This module is internal.  Import :class:`TelemetryDB` from
``tokenpak.telemetry.storage`` instead.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Union

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


class TelemetryDBBase:
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

    def __enter__(self) -> "TelemetryDBBase":
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Insert helpers (single + batch)
    # ------------------------------------------------------------------
