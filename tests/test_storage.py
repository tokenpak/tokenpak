"""Tests for TokenPak Schema Patch (Phase 7H / PRD fields).

Covers:
- All new PRD columns present in tp_events, tp_segments, tp_usage, tp_costs, rollups
- TelemetryEvent, Segment, Usage, Cost dataclass fields updated
- insert_trace() persists and retrieves all new fields
- Schema migration is idempotent (safe to run multiple times)
- Backfill script columns wired (provider_usage_raw from session JSONL)
- Rollup computation includes avg_raw_tokens, avg_final_tokens, avg_cost
- All existing tests continue to pass
"""

from __future__ import annotations

import json
import sqlite3
import tempfile
import time
from pathlib import Path

from tokenpak.telemetry.models import Cost, Segment, TelemetryEvent, Usage
from tokenpak.telemetry.storage import TelemetryDB

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(**kw) -> TelemetryEvent:
    defaults = dict(
        trace_id="test-trace-1",
        request_id="req-1",
        event_type="request",
        provider="anthropic",
        model="claude-3-sonnet",
        agent_id="cali",
        ts=time.time(),
    )
    defaults.update(kw)
    return TelemetryEvent(**defaults)


def _make_usage(**kw) -> Usage:
    defaults = dict(trace_id="test-trace-1", input_billed=100, output_billed=50)
    defaults.update(kw)
    return Usage(**defaults)


def _make_cost(**kw) -> Cost:
    defaults = dict(trace_id="test-trace-1", cost_total=0.01)
    defaults.update(kw)
    return Cost(**defaults)


def _make_segment(seg_id: str = "seg-1", **kw) -> Segment:
    defaults = dict(trace_id="test-trace-1", segment_id=seg_id, tokens_raw=200, tokens_after_tp=150)
    defaults.update(kw)
    return Segment(**defaults)


def _table_cols(db: TelemetryDB, table: str) -> set[str]:
    cur = db._conn.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


# ---------------------------------------------------------------------------
# 1. DDL — All new PRD columns present in fresh DB
# ---------------------------------------------------------------------------


class TestSchemaColumns:
    def test_tp_events_has_span_id(self):
        db = TelemetryDB(":memory:")
        assert "span_id" in _table_cols(db, "tp_events")
        db.close()

    def test_tp_events_has_node_id(self):
        db = TelemetryDB(":memory:")
        assert "node_id" in _table_cols(db, "tp_events")
        db.close()

    def test_tp_segments_has_segment_source(self):
        db = TelemetryDB(":memory:")
        assert "segment_source" in _table_cols(db, "tp_segments")
        db.close()

    def test_tp_segments_has_content_type(self):
        db = TelemetryDB(":memory:")
        assert "content_type" in _table_cols(db, "tp_segments")
        db.close()

    def test_tp_segments_has_char_byte_lengths(self):
        db = TelemetryDB(":memory:")
        cols = _table_cols(db, "tp_segments")
        for col in ("raw_len_chars", "raw_len_bytes", "final_len_chars", "final_len_bytes"):
            assert col in cols, f"Missing: {col}"
        db.close()

    def test_tp_segments_has_debug_ref(self):
        db = TelemetryDB(":memory:")
        assert "debug_ref" in _table_cols(db, "tp_segments")
        db.close()

    def test_tp_usage_has_total_tokens_billed(self):
        db = TelemetryDB(":memory:")
        assert "total_tokens_billed" in _table_cols(db, "tp_usage")
        db.close()

    def test_tp_usage_has_total_tokens_est(self):
        db = TelemetryDB(":memory:")
        assert "total_tokens_est" in _table_cols(db, "tp_usage")
        db.close()

    def test_tp_usage_has_provider_usage_raw(self):
        db = TelemetryDB(":memory:")
        assert "provider_usage_raw" in _table_cols(db, "tp_usage")
        db.close()

    def test_tp_costs_has_pricing_version(self):
        db = TelemetryDB(":memory:")
        assert "pricing_version" in _table_cols(db, "tp_costs")
        db.close()

    def test_tp_costs_has_actual_input_tokens(self):
        db = TelemetryDB(":memory:")
        assert "actual_input_tokens" in _table_cols(db, "tp_costs")
        db.close()

    def test_tp_costs_has_output_tokens(self):
        db = TelemetryDB(":memory:")
        assert "output_tokens" in _table_cols(db, "tp_costs")
        db.close()

    def test_rollup_tables_have_avg_columns(self):
        db = TelemetryDB(":memory:")
        for table in ("tp_rollup_daily_model", "tp_rollup_daily_provider", "tp_rollup_daily_agent"):
            cols = _table_cols(db, table)
            for col in ("avg_raw_tokens", "avg_final_tokens", "avg_cost"):
                assert col in cols, f"{table} missing {col}"
        db.close()

    def test_stats_includes_rollup_counts(self):
        db = TelemetryDB(":memory:")
        stats = db.stats()
        assert "tp_rollups" in stats
        assert "tp_rollup_daily_model" in stats
        assert "tp_rollup_daily_provider" in stats
        assert "tp_rollup_daily_agent" in stats
        db.close()


# ---------------------------------------------------------------------------
# 2. Dataclass fields
# ---------------------------------------------------------------------------


class TestDataclassFields:
    def test_telemetry_event_has_span_id(self):
        e = TelemetryEvent()
        assert hasattr(e, "span_id")
        assert e.span_id == ""

    def test_telemetry_event_has_node_id(self):
        e = TelemetryEvent()
        assert hasattr(e, "node_id")
        assert e.node_id == ""

    def test_segment_has_segment_source(self):
        s = Segment()
        assert hasattr(s, "segment_source")
        assert s.segment_source == ""

    def test_segment_has_content_type(self):
        s = Segment()
        assert hasattr(s, "content_type")
        assert s.content_type == "text"

    def test_segment_has_char_byte_lengths(self):
        s = Segment()
        for attr in ("raw_len_chars", "raw_len_bytes", "final_len_chars", "final_len_bytes"):
            assert hasattr(s, attr), f"Segment missing field: {attr}"
            assert getattr(s, attr) == 0

    def test_segment_has_debug_ref_nullable(self):
        s = Segment()
        assert hasattr(s, "debug_ref")
        assert s.debug_ref is None

    def test_usage_has_total_tokens_billed(self):
        u = Usage()
        assert hasattr(u, "total_tokens_billed")
        assert u.total_tokens_billed == 0

    def test_usage_has_total_tokens_est(self):
        u = Usage()
        assert hasattr(u, "total_tokens_est")
        assert u.total_tokens_est == 0

    def test_usage_has_provider_usage_raw(self):
        u = Usage()
        assert hasattr(u, "provider_usage_raw")
        assert u.provider_usage_raw == "{}"

    def test_cost_has_pricing_version(self):
        c = Cost()
        assert hasattr(c, "pricing_version")

    def test_cost_has_actual_input_tokens(self):
        c = Cost()
        assert hasattr(c, "actual_input_tokens")
        assert c.actual_input_tokens == 0


# ---------------------------------------------------------------------------
# 3. insert_trace() persists and retrieves new fields
# ---------------------------------------------------------------------------


class TestInsertTraceNewFields:
    def test_span_id_persisted(self):
        db = TelemetryDB(":memory:")
        e = _make_event(span_id="span-abc", node_id="node-xyz")
        db.insert_trace(e, _make_usage(), _make_cost())
        trace = db.get_trace("test-trace-1")
        assert trace["event"]["span_id"] == "span-abc"
        assert trace["event"]["node_id"] == "node-xyz"
        db.close()

    def test_usage_new_fields_persisted(self):
        db = TelemetryDB(":memory:")
        raw_usage_json = json.dumps({"input": 100, "output": 50, "cache_read": 10})
        u = _make_usage(
            total_tokens_billed=150,
            total_tokens_est=140,
            provider_usage_raw=raw_usage_json,
        )
        db.insert_trace(_make_event(), u, _make_cost())
        trace = db.get_trace("test-trace-1")
        assert trace["usage"]["total_tokens_billed"] == 150
        assert trace["usage"]["total_tokens_est"] == 140
        assert trace["usage"]["provider_usage_raw"] == raw_usage_json
        db.close()

    def test_cost_new_fields_persisted(self):
        db = TelemetryDB(":memory:")
        c = _make_cost(pricing_version="v2", actual_input_tokens=80, output_tokens=40)
        db.insert_trace(_make_event(), _make_usage(), c)
        trace = db.get_trace("test-trace-1")
        assert trace["cost"]["pricing_version"] == "v2"
        assert trace["cost"]["actual_input_tokens"] == 80
        assert trace["cost"]["output_tokens"] == 40
        db.close()

    def test_segment_new_fields_persisted(self):
        db = TelemetryDB(":memory:")
        s = _make_segment(
            segment_source="user_message",
            content_type="code",
            raw_len_chars=500,
            raw_len_bytes=510,
            final_len_chars=300,
            final_len_bytes=308,
            debug_ref="debug-blob-001",
        )
        db.insert_trace(_make_event(), _make_usage(), _make_cost(), [s])
        trace = db.get_trace("test-trace-1")
        seg = trace["segments"][0]
        assert seg["segment_source"] == "user_message"
        assert seg["content_type"] == "code"
        assert seg["raw_len_chars"] == 500
        assert seg["raw_len_bytes"] == 510
        assert seg["final_len_chars"] == 300
        assert seg["debug_ref"] == "debug-blob-001"
        db.close()

    def test_segment_debug_ref_nullable(self):
        db = TelemetryDB(":memory:")
        s = _make_segment()  # debug_ref=None by default
        db.insert_trace(_make_event(), _make_usage(), _make_cost(), [s])
        trace = db.get_trace("test-trace-1")
        # debug_ref should be None or absent
        seg = trace["segments"][0]
        assert seg.get("debug_ref") is None or seg.get("debug_ref") == ""
        db.close()


# ---------------------------------------------------------------------------
# 4. Schema migration is idempotent
# ---------------------------------------------------------------------------


class TestSchemaMigration:
    def test_migration_idempotent_multiple_runs(self):
        """Running _migrate_schema() multiple times must not raise."""
        db = TelemetryDB(":memory:")
        for _ in range(3):
            db._migrate_schema()
        # All columns still present
        assert "span_id" in _table_cols(db, "tp_events")
        assert "provider_usage_raw" in _table_cols(db, "tp_usage")
        db.close()

    def test_migration_adds_columns_to_legacy_db(self):
        """Opening a pre-patch DB should add all new columns automatically."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name

        try:
            # Create DB with OLD schema (no new columns)
            conn = sqlite3.connect(path)
            conn.execute("""CREATE TABLE tp_events (
                trace_id TEXT NOT NULL, request_id TEXT NOT NULL DEFAULT '',
                event_type TEXT NOT NULL DEFAULT '', ts REAL NOT NULL DEFAULT 0,
                provider TEXT NOT NULL DEFAULT '', model TEXT NOT NULL DEFAULT '',
                agent_id TEXT NOT NULL DEFAULT '', api TEXT NOT NULL DEFAULT '',
                stop_reason TEXT NOT NULL DEFAULT '', session_id TEXT NOT NULL DEFAULT '',
                duration_ms REAL NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'ok',
                error_class TEXT, payload TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (trace_id, request_id, event_type))""")
            conn.execute("""CREATE TABLE tp_usage (
                trace_id TEXT NOT NULL PRIMARY KEY, usage_source TEXT NOT NULL DEFAULT 'unknown',
                confidence TEXT NOT NULL DEFAULT 'low', input_billed INTEGER NOT NULL DEFAULT 0,
                output_billed INTEGER NOT NULL DEFAULT 0, input_est INTEGER NOT NULL DEFAULT 0,
                output_est INTEGER NOT NULL DEFAULT 0, cache_read INTEGER NOT NULL DEFAULT 0,
                cache_write INTEGER NOT NULL DEFAULT 0)""")
            conn.execute("""CREATE TABLE tp_costs (
                trace_id TEXT NOT NULL PRIMARY KEY, cost_total REAL NOT NULL DEFAULT 0,
                cost_source TEXT NOT NULL DEFAULT 'provider',
                baseline_cost REAL NOT NULL DEFAULT 0, savings_total REAL NOT NULL DEFAULT 0,
                savings_qmd REAL NOT NULL DEFAULT 0, savings_tp REAL NOT NULL DEFAULT 0)""")
            conn.commit()
            conn.close()

            # Open with TelemetryDB — should auto-migrate
            db = TelemetryDB(path)

            assert "span_id" in _table_cols(db, "tp_events")
            assert "node_id" in _table_cols(db, "tp_events")
            assert "provider_usage_raw" in _table_cols(db, "tp_usage")
            assert "total_tokens_billed" in _table_cols(db, "tp_usage")
            assert "pricing_version" in _table_cols(db, "tp_costs")
            db.close()

        finally:
            Path(path).unlink(missing_ok=True)

    def test_migration_second_run_no_error(self):
        """Opening a post-patch DB again must not fail."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db = TelemetryDB(path)
            db.close()
            # Second open
            db2 = TelemetryDB(path)
            assert "span_id" in _table_cols(db2, "tp_events")
            db2.close()
        finally:
            Path(path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 5. Rollup computation includes avg columns
# ---------------------------------------------------------------------------


class TestRollupAvgColumns:
    def test_compute_rollups_includes_avg_raw_tokens(self):
        """After inserting events+segments, rollup should have avg_raw_tokens."""
        db = TelemetryDB(":memory:")

        for i in range(3):
            trace_id = f"trace-{i}"
            e = _make_event(trace_id=trace_id)
            u = Usage(trace_id=trace_id, input_billed=100, output_billed=50)
            c = Cost(trace_id=trace_id, cost_total=0.005)
            s = Segment(
                trace_id=trace_id,
                segment_id=f"seg-{i}",
                tokens_raw=200 + i * 100,
                tokens_after_tp=150 + i * 80,
            )
            db.insert_trace(e, u, c, [s])

        db.compute_rollups()

        cur = db._conn.cursor()
        cur.execute(
            "SELECT avg_raw_tokens, avg_final_tokens, avg_cost FROM tp_rollup_daily_model LIMIT 1"
        )
        row = cur.fetchone()
        assert row is not None, "No rollup rows computed"
        avg_raw, avg_final, avg_cost = row[0], row[1], row[2]
        assert avg_raw > 0, f"avg_raw_tokens should be > 0, got {avg_raw}"
        assert avg_final > 0, f"avg_final_tokens should be > 0, got {avg_final}"
        assert avg_cost >= 0
        db.close()

    def test_compute_rollups_fallbacks_to_usage_without_segments(self):
        """Avg columns should fall back to usage tokens when segments are missing."""
        db = TelemetryDB(":memory:")

        for i in range(3):
            trace_id = f"trace-nosg-{i}"
            e = _make_event(trace_id=trace_id)
            u = Usage(trace_id=trace_id, input_billed=120 + i * 10, output_billed=30)
            c = Cost(trace_id=trace_id, cost_total=0.005)
            db.insert_trace(e, u, c, [])

        db.compute_rollups()

        cur = db._conn.cursor()
        cur.execute("SELECT avg_raw_tokens, avg_final_tokens FROM tp_rollup_daily_model LIMIT 1")
        row = cur.fetchone()
        assert row is not None, "No rollup rows computed"
        avg_raw, avg_final = row[0], row[1]
        assert avg_raw > 0, f"avg_raw_tokens should be > 0, got {avg_raw}"
        assert avg_final > 0, f"avg_final_tokens should be > 0, got {avg_final}"
        db.close()


# ---------------------------------------------------------------------------
# 6. provider_usage_raw JSON from session
# ---------------------------------------------------------------------------


class TestProviderUsageRaw:
    def test_provider_usage_raw_stores_valid_json(self):
        db = TelemetryDB(":memory:")
        raw = {"input": 1000, "output": 250, "cacheRead": 800, "cost": {"total": 0.05}}
        u = _make_usage(provider_usage_raw=json.dumps(raw))
        db.insert_trace(_make_event(), u, _make_cost())
        trace = db.get_trace("test-trace-1")
        stored = trace["usage"]["provider_usage_raw"]
        parsed = json.loads(stored)
        assert parsed["input"] == 1000
        assert parsed["cost"]["total"] == 0.05
        db.close()

    def test_provider_usage_raw_defaults_to_empty_json(self):
        db = TelemetryDB(":memory:")
        db.insert_trace(_make_event(), _make_usage(), _make_cost())
        trace = db.get_trace("test-trace-1")
        raw = trace["usage"]["provider_usage_raw"]
        assert raw in ("{}", None, ""), f"Expected empty JSON obj, got: {raw!r}"
        db.close()
