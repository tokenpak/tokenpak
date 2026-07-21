"""
Smoke tests for the synthetic legacy cost fixture builder (FORENSIC-70-137-184).

Asserts that each builder produces a well-formed SQLite DB with the expected
tp_events / tp_costs row counts and key shapes.  Does NOT exercise cost.py
repair logic — that belongs to the parent task's regression suite.
"""
from __future__ import annotations

import sqlite3

import pytest

from tests.telemetry.fixtures.legacy_cost_fixtures import (
    _DAY_BOUNDARY,
    ALL_BUILDERS,
    build_existing_costs_db,
    build_legacy_schema_db,
    build_missing_costs_db,
    build_mixed_epoch_boundary_db,
    build_multi_event_per_trace_db,
    build_multi_pricing_version_db,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _count(conn: sqlite3.Connection, table: str) -> int:
    (n,) = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()  # noqa: S608
    return n


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


# ---------------------------------------------------------------------------
# Scenario 1 — existing tp_costs rows
# ---------------------------------------------------------------------------


def test_existing_costs_row_counts():
    conn = build_existing_costs_db()
    assert _count(conn, "tp_events") == 3
    assert _count(conn, "tp_costs") == 3


def test_existing_costs_traces_matched():
    conn = build_existing_costs_db()
    event_ids = {r[0] for r in conn.execute("SELECT trace_id FROM tp_events")}
    cost_ids = {r[0] for r in conn.execute("SELECT trace_id FROM tp_costs")}
    assert event_ids == cost_ids, "every event trace must have a matching cost row"


# ---------------------------------------------------------------------------
# Scenario 2 — missing tp_costs rows
# ---------------------------------------------------------------------------


def test_missing_costs_events_present():
    conn = build_missing_costs_db()
    assert _count(conn, "tp_events") == 3


def test_missing_costs_no_cost_rows():
    conn = build_missing_costs_db()
    assert _count(conn, "tp_costs") == 0, "no cost rows — repair must INSERT all"


# ---------------------------------------------------------------------------
# Scenario 3 — multi-event-per-trace
# ---------------------------------------------------------------------------


def test_multi_event_single_trace():
    conn = build_multi_event_per_trace_db()
    trace_ids = {r[0] for r in conn.execute("SELECT DISTINCT trace_id FROM tp_events")}
    assert len(trace_ids) == 1, "all events belong to one trace_id"


def test_multi_event_row_count():
    conn = build_multi_event_per_trace_db()
    assert _count(conn, "tp_events") == 3
    assert _count(conn, "tp_costs") == 0


def test_multi_event_composite_pk_uniqueness():
    """All three rows are independently addressable via composite PK."""
    conn = build_multi_event_per_trace_db()
    rows = conn.execute(
        "SELECT trace_id, request_id, event_type FROM tp_events"
    ).fetchall()
    pks = {(r[0], r[1], r[2]) for r in rows}
    assert len(pks) == 3, "composite PK (trace_id, request_id, event_type) must be unique per row"


# ---------------------------------------------------------------------------
# Scenario 4 — multiple historical pricing versions
# ---------------------------------------------------------------------------


def test_multi_pricing_version_distinct_versions():
    conn = build_multi_pricing_version_db()
    versions = {r[0] for r in conn.execute("SELECT pricing_version FROM tp_costs")}
    assert versions == {"v1", "v2"}


def test_multi_pricing_version_row_counts():
    conn = build_multi_pricing_version_db()
    assert _count(conn, "tp_events") == 2
    assert _count(conn, "tp_costs") == 2


# ---------------------------------------------------------------------------
# Scenario 5 — mixed epoch boundaries
# ---------------------------------------------------------------------------


def test_mixed_epoch_boundary_row_count():
    conn = build_mixed_epoch_boundary_db()
    assert _count(conn, "tp_events") == 4
    assert _count(conn, "tp_costs") == 0


def test_mixed_epoch_boundary_straddles_day():
    conn = build_mixed_epoch_boundary_db()
    tss = [r[0] for r in conn.execute("SELECT ts FROM tp_events ORDER BY ts")]
    assert min(tss) < _DAY_BOUNDARY, "at least one event must precede the day boundary"
    assert max(tss) > _DAY_BOUNDARY, "at least one event must follow the day boundary"


def test_mixed_epoch_boundary_two_events_per_side():
    conn = build_mixed_epoch_boundary_db()
    before = conn.execute(
        "SELECT COUNT(*) FROM tp_events WHERE ts < ?", (_DAY_BOUNDARY,)
    ).fetchone()[0]
    after = conn.execute(
        "SELECT COUNT(*) FROM tp_events WHERE ts > ?", (_DAY_BOUNDARY,)
    ).fetchone()[0]
    assert before == 2
    assert after == 2


# ---------------------------------------------------------------------------
# Scenario 6 — legacy schema (actual_cost present, cost_total absent)
# ---------------------------------------------------------------------------


def test_legacy_schema_row_counts():
    conn = build_legacy_schema_db()
    assert _count(conn, "tp_events") == 2
    assert _count(conn, "tp_costs") == 2


def test_legacy_schema_has_actual_cost():
    conn = build_legacy_schema_db()
    cols = _column_names(conn, "tp_costs")
    assert "actual_cost" in cols


def test_legacy_schema_no_cost_total():
    """Absence of cost_total triggers the DROP+recreate migration path in storage.py."""
    conn = build_legacy_schema_db()
    cols = _column_names(conn, "tp_costs")
    assert "cost_total" not in cols, (
        "legacy schema must NOT have cost_total — its presence disarms the migration"
    )


def test_legacy_schema_traces_matched():
    conn = build_legacy_schema_db()
    event_ids = {r[0] for r in conn.execute("SELECT trace_id FROM tp_events")}
    cost_ids = {r[0] for r in conn.execute("SELECT trace_id FROM tp_costs")}
    assert event_ids == cost_ids


# ---------------------------------------------------------------------------
# Registry — all six builders produce valid sqlite3.Connection objects
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name,builder", list(ALL_BUILDERS.items()))
def test_all_builders_return_connection(name: str, builder) -> None:
    conn = builder()
    assert isinstance(conn, sqlite3.Connection), f"{name}: expected sqlite3.Connection"
    tables = {
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    assert "tp_events" in tables, f"{name}: missing tp_events table"
    assert "tp_costs" in tables, f"{name}: missing tp_costs table"
