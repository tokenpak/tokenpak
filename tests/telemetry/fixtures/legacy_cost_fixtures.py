"""
Synthetic legacy telemetry fixture builder for FORENSIC-70-137-184.

Constructs deterministic in-memory SQLite DBs covering the six scenarios
the forensic cost.py repair must be tested against.  No wall-clock or
random calls; all timestamps are fixed constants derived from _BASE_TS.

Schemas match storage.py verbatim — verify against that file before
extending (PRIMARY KEY (trace_id, request_id, event_type) on tp_events;
PRIMARY KEY trace_id on tp_costs).
"""
from __future__ import annotations

import sqlite3

# ---------------------------------------------------------------------------
# Fixed epoch anchor — no wall-clock calls (deterministic)
# _BASE_TS  = 2023-11-14 22:13:20 UTC
# _DAY_BOUNDARY = 2023-11-15 00:00:00 UTC  (19676 * 86400)
# ---------------------------------------------------------------------------
_BASE_TS: float = 1_700_000_000.0
_DAY_BOUNDARY: float = 1_700_006_400.0  # first midnight strictly above _BASE_TS

# ---------------------------------------------------------------------------
# Schema DDLs
# ---------------------------------------------------------------------------

# Current canonical schema — matches storage.py CREATE TABLE blocks verbatim.
_CANONICAL_DDL = """\
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
    route           TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (trace_id, request_id, event_type)
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
"""

# Legacy schema: tp_costs has actual_cost but NOT cost_total.
# This shape triggers the DROP + recreate path in storage.py:349-370.
_LEGACY_DDL = """\
CREATE TABLE IF NOT EXISTS tp_events (
    trace_id        TEXT NOT NULL,
    request_id      TEXT NOT NULL DEFAULT '',
    event_type      TEXT NOT NULL DEFAULT '',
    ts              REAL NOT NULL DEFAULT 0,
    provider        TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    agent_id        TEXT NOT NULL DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'ok',
    payload         TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (trace_id, request_id, event_type)
);

CREATE TABLE IF NOT EXISTS tp_costs (
    trace_id    TEXT NOT NULL PRIMARY KEY,
    cost_input  REAL NOT NULL DEFAULT 0,
    cost_output REAL NOT NULL DEFAULT 0,
    actual_cost REAL NOT NULL DEFAULT 0
);
"""

# ---------------------------------------------------------------------------
# Internal helpers — not part of the public fixture API
# ---------------------------------------------------------------------------


def _canonical_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_CANONICAL_DDL)
    return conn


def _legacy_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(_LEGACY_DDL)
    return conn


def _insert_event(
    conn: sqlite3.Connection,
    trace_id: str,
    request_id: str,
    event_type: str,
    ts: float,
    model: str = "claude-3-5-sonnet-20241022",
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO tp_events
               (trace_id, request_id, event_type, ts, provider, model, agent_id, status)
           VALUES (?, ?, ?, ?, 'anthropic', ?, 'fixture-agent', 'ok')""",
        (trace_id, request_id, event_type, ts, model),
    )


def _insert_cost(
    conn: sqlite3.Connection,
    trace_id: str,
    pricing_version: str = "v1",
    cost_total: float = 0.001,
) -> None:
    conn.execute(
        """INSERT OR REPLACE INTO tp_costs
               (trace_id, cost_input, cost_output, cost_total, cost_source,
                pricing_version, baseline_input_tokens, actual_input_tokens,
                output_tokens, baseline_cost, actual_cost)
           VALUES (?, 0.0005, 0.0005, ?, 'provider', ?, 100, 80, 50, 0.0012, 0.001)""",
        (trace_id, cost_total, pricing_version),
    )


# ---------------------------------------------------------------------------
# Scenario 1 — Existing tp_costs rows (repair must UPDATE, once per trace)
# ---------------------------------------------------------------------------


def build_existing_costs_db() -> sqlite3.Connection:
    """Three traces each with one tp_events row AND a populated tp_costs row.

    The forensic repair must UPDATE each tp_costs row exactly once.
    """
    conn = _canonical_conn()
    for i in range(1, 4):
        tid = f"trace-existing-{i:03d}"
        _insert_event(conn, tid, f"req-existing-{i:03d}", "request_end", _BASE_TS + i * 10)
        _insert_cost(conn, tid)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Scenario 2 — Missing tp_costs rows (repair must INSERT)
# ---------------------------------------------------------------------------


def build_missing_costs_db() -> sqlite3.Connection:
    """Three traces in tp_events with NO corresponding tp_costs rows.

    These are the traces miscounted as processed today; repair must INSERT.
    """
    conn = _canonical_conn()
    for i in range(1, 4):
        tid = f"trace-missing-{i:03d}"
        _insert_event(conn, tid, f"req-missing-{i:03d}", "request_end", _BASE_TS + i * 10)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Scenario 3 — Multi-event-per-trace (row order must not decide final cost)
# ---------------------------------------------------------------------------


def build_multi_event_per_trace_db() -> sqlite3.Connection:
    """One trace_id with three tp_events rows (different request_id); no tp_costs.

    The composite PRIMARY KEY (trace_id, request_id, event_type) makes each
    row independently addressable.  Row insertion ORDER must not determine the
    final cost reconstruction — whichever event the repair processes last must
    not silently overwrite the others.
    """
    conn = _canonical_conn()
    tid = "trace-multi-event-001"
    for i in range(1, 4):
        _insert_event(conn, tid, f"req-multi-{i:03d}", "request_end", _BASE_TS + i * 1.0)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Scenario 4 — Multiple historical pricing versions
# ---------------------------------------------------------------------------


def build_multi_pricing_version_db() -> sqlite3.Connection:
    """Two traces with tp_events + tp_costs using different pricing_version values.

    Trace "alpha" uses v1; trace "beta" uses v2.  The version-selection path
    in the repair must pick the correct pricing catalog for each trace.
    """
    conn = _canonical_conn()
    for version, label in (("v1", "alpha"), ("v2", "beta")):
        tid = f"trace-pver-{label}"
        _insert_event(conn, tid, f"req-{label}", "request_end", _BASE_TS + 100)
        _insert_cost(conn, tid, pricing_version=version)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Scenario 5 — Mixed epoch boundaries (DATE(e.ts) inclusive-bounds regression)
# ---------------------------------------------------------------------------


def build_mixed_epoch_boundary_db() -> sqlite3.Connection:
    """Four tp_events rows straddling a UTC day boundary; no tp_costs rows.

    Two events land before _DAY_BOUNDARY (2023-11-14) and two after
    (2023-11-15).  Inclusive-bounds / DATE(e.ts) logic must account for both
    calendar days without double-counting or missing either side.
    """
    conn = _canonical_conn()
    events = [
        ("trace-boundary-001", "req-am-1", "request_end", _DAY_BOUNDARY - 120.0),
        ("trace-boundary-002", "req-am-2", "request_end", _DAY_BOUNDARY - 30.0),
        ("trace-boundary-003", "req-pm-1", "request_end", _DAY_BOUNDARY + 30.0),
        ("trace-boundary-004", "req-pm-2", "request_end", _DAY_BOUNDARY + 120.0),
    ]
    for tid, req, etype, ts in events:
        _insert_event(conn, tid, req, etype, ts)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Scenario 6 — Pre-populated legacy rows (migration preservation)
# ---------------------------------------------------------------------------


def build_legacy_schema_db() -> sqlite3.Connection:
    """Legacy-schema DB: tp_costs has actual_cost but NO cost_total column.

    Two traces have both tp_events and tp_costs rows in the pre-migration
    schema.  The storage.py migration path (lines 349-370) drops and
    recreates tp_costs when it detects this shape; copy/verify/rollback tests
    must prove byte/value/count equivalence before and after that migration.
    """
    conn = _legacy_conn()
    for i in range(1, 3):
        tid = f"trace-legacy-{i:03d}"
        conn.execute(
            """INSERT OR REPLACE INTO tp_events
                   (trace_id, request_id, event_type, ts, provider, model, agent_id, status)
               VALUES (?, ?, 'request_end', ?, 'anthropic', 'claude-3-opus-20240229',
                       'fixture-agent', 'ok')""",
            (tid, f"req-legacy-{i:03d}", _BASE_TS + i * 5),
        )
        conn.execute(
            """INSERT OR REPLACE INTO tp_costs
                   (trace_id, cost_input, cost_output, actual_cost)
               VALUES (?, 0.0004, 0.0006, 0.001)""",
            (tid,),
        )
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Registry — all six builders in scenario order
# ---------------------------------------------------------------------------

ALL_BUILDERS: dict[str, object] = {
    "existing_costs": build_existing_costs_db,
    "missing_costs": build_missing_costs_db,
    "multi_event_per_trace": build_multi_event_per_trace_db,
    "multi_pricing_version": build_multi_pricing_version_db,
    "mixed_epoch_boundary": build_mixed_epoch_boundary_db,
    "legacy_schema": build_legacy_schema_db,
}
