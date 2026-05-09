"""Tests for telemetry/storage_rollups.py — RollupsMixin.

Covers: get_summary, get_timeseries, compute_rollups, get_rollup_timeseries.
"""

from __future__ import annotations

import time

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_db():
    """Create a combined TelemetryDBBase + RollupsMixin instance using :memory:."""
    from tokenpak.telemetry.storage_base import TelemetryDBBase
    from tokenpak.telemetry.storage_rollups import RollupsMixin

    class TestDB(TelemetryDBBase, RollupsMixin):
        pass

    return TestDB(":memory:")


def insert_row(db, trace_id: str, provider: str, model: str, agent_id: str,
               ts: float, tokens: int = 100, cost: float = 0.01, savings: float = 0.001):
    """Insert synthetic rows into tp_events, tp_usage, tp_costs."""
    cur = db._conn.cursor()
    cur.execute(
        """INSERT OR IGNORE INTO tp_events
           (trace_id, request_id, event_type, ts, provider, model, agent_id)
           VALUES (?,?,?,?,?,?,?)""",
        (trace_id, trace_id + "_req", "completion", ts, provider, model, agent_id),
    )
    cur.execute(
        """INSERT OR REPLACE INTO tp_usage
           (trace_id, input_billed, output_billed) VALUES (?,?,?)""",
        (trace_id, tokens // 2, tokens // 2),
    )
    cur.execute(
        """INSERT OR REPLACE INTO tp_costs
           (trace_id, cost_input, cost_output, cost_total, savings_total)
           VALUES (?,?,?,?,?)""",
        (trace_id, cost / 2, cost / 2, cost, savings),
    )
    db._conn.commit()


# ---------------------------------------------------------------------------
# Test: module import
# ---------------------------------------------------------------------------

class TestModuleImport:
    def test_import_storage_rollups(self):
        """Module imports without error."""
        from tokenpak.telemetry import storage_rollups
        assert storage_rollups is not None

    def test_rollups_mixin_exists(self):
        """RollupsMixin class is accessible."""
        from tokenpak.telemetry.storage_rollups import RollupsMixin
        assert RollupsMixin is not None


# ---------------------------------------------------------------------------
# Test: get_summary
# ---------------------------------------------------------------------------

class TestGetSummary:
    def test_summary_empty_db(self):
        """get_summary on empty DB returns zeros."""
        db = make_db()
        result = db.get_summary()
        assert isinstance(result, dict)
        assert result.get("total_requests", 0) == 0
        assert result.get("total_tokens", 0) == 0
        assert "by_provider" in result
        assert "by_model" in result
        assert "by_agent" in result

    def test_summary_with_data(self):
        """get_summary aggregates inserted rows correctly."""
        db = make_db()
        now = time.time()
        insert_row(db, "t1", "anthropic", "claude-3-haiku", "agent1", now, tokens=200, cost=0.02)
        insert_row(db, "t2", "anthropic", "claude-3-haiku", "agent1", now, tokens=100, cost=0.01)

        result = db.get_summary()
        assert result["total_requests"] == 2
        assert result["total_tokens"] == 300
        assert abs(result["total_cost"] - 0.03) < 1e-9

    def test_summary_filter_by_provider(self):
        """get_summary filters by provider."""
        db = make_db()
        now = time.time()
        insert_row(db, "t1", "anthropic", "model-a", "agent1", now, tokens=100)
        insert_row(db, "t2", "openai", "model-b", "agent1", now, tokens=200)

        result = db.get_summary(provider="openai")
        assert result["total_requests"] == 1
        assert result["total_tokens"] == 200

    def test_summary_filter_by_model(self):
        """get_summary filters by model."""
        db = make_db()
        now = time.time()
        insert_row(db, "t1", "anthropic", "haiku", "agent1", now, tokens=50)
        insert_row(db, "t2", "anthropic", "sonnet", "agent2", now, tokens=150)

        result = db.get_summary(model="haiku")
        assert result["total_requests"] == 1
        assert result["total_tokens"] == 50

    def test_summary_by_provider_breakdown(self):
        """get_summary returns per-provider breakdown list."""
        db = make_db()
        now = time.time()
        insert_row(db, "t1", "anthropic", "m", "a", now)
        insert_row(db, "t2", "openai", "m", "a", now)

        result = db.get_summary()
        providers = {row["provider"] for row in result["by_provider"]}
        assert "anthropic" in providers
        assert "openai" in providers


# ---------------------------------------------------------------------------
# Test: get_timeseries
# ---------------------------------------------------------------------------

class TestGetTimeseries:
    def test_timeseries_empty(self):
        """get_timeseries on empty DB returns empty list."""
        db = make_db()
        result = db.get_timeseries()
        assert isinstance(result, list)
        assert len(result) == 0

    def test_timeseries_cost_metric(self):
        """get_timeseries returns cost values."""
        db = make_db()
        now = time.time()
        insert_row(db, "t1", "anthropic", "m", "a", now, cost=0.05)

        result = db.get_timeseries(metric="cost")
        assert len(result) >= 1
        assert any(row["value"] > 0 for row in result)

    def test_timeseries_tokens_metric(self):
        """get_timeseries returns token counts."""
        db = make_db()
        now = time.time()
        insert_row(db, "t1", "anthropic", "m", "a", now, tokens=300)

        result = db.get_timeseries(metric="tokens")
        assert len(result) >= 1
        assert any(row["value"] > 0 for row in result)

    def test_timeseries_requests_metric(self):
        """get_timeseries returns request counts."""
        db = make_db()
        now = time.time()
        insert_row(db, "t1", "anthropic", "m", "a", now)
        insert_row(db, "t2", "openai", "m", "a", now)

        result = db.get_timeseries(metric="requests")
        total = sum(row["value"] for row in result)
        assert total == 2

    def test_timeseries_since_ts_filter(self):
        """get_timeseries respects since_ts filter."""
        db = make_db()
        old_ts = time.time() - 86400  # 1 day ago
        new_ts = time.time()
        insert_row(db, "t1", "anthropic", "m", "a", old_ts, cost=0.10)
        insert_row(db, "t2", "anthropic", "m", "a", new_ts, cost=0.20)

        result = db.get_timeseries(metric="cost", since_ts=new_ts - 1)
        total = sum(row["value"] for row in result)
        assert abs(total - 0.20) < 1e-9

    def test_timeseries_day_interval(self):
        """get_timeseries returns bucket keys when interval='day'."""
        db = make_db()
        now = time.time()
        insert_row(db, "t1", "anthropic", "m", "a", now)

        result = db.get_timeseries(interval="day")
        assert len(result) >= 1
        # bucket should be a date string YYYY-MM-DD
        assert len(result[0]["bucket"]) == 10


# ---------------------------------------------------------------------------
# Test: compute_rollups
# ---------------------------------------------------------------------------

class TestComputeRollups:
    def test_compute_rollups_empty_db(self):
        """compute_rollups on empty DB returns zero counts."""
        db = make_db()
        counts = db.compute_rollups()
        assert isinstance(counts, dict)
        assert "model" in counts
        assert "provider" in counts
        assert "agent" in counts

    def test_compute_rollups_populates_tables(self):
        """compute_rollups writes rows to rollup tables."""
        db = make_db()
        now = time.time()
        insert_row(db, "t1", "anthropic", "haiku", "agent1", now, cost=0.01)
        insert_row(db, "t2", "openai", "gpt-4o", "agent2", now, cost=0.02)

        counts = db.compute_rollups()
        assert counts["model"] >= 2
        assert counts["provider"] >= 2
        assert counts["agent"] >= 2

    def test_compute_rollups_idempotent(self):
        """compute_rollups can be called twice without error."""
        db = make_db()
        now = time.time()
        insert_row(db, "t1", "anthropic", "m", "a", now)

        counts1 = db.compute_rollups()
        counts2 = db.compute_rollups()
        assert counts1 == counts2


# ---------------------------------------------------------------------------
# Test: get_rollup_timeseries
# ---------------------------------------------------------------------------

class TestGetRollupTimeseries:
    def test_rollup_timeseries_empty(self):
        """get_rollup_timeseries on empty tables returns empty list."""
        db = make_db()
        result = db.get_rollup_timeseries()
        assert isinstance(result, list)
        assert len(result) == 0

    def test_rollup_timeseries_after_compute(self):
        """get_rollup_timeseries returns data after compute_rollups."""
        db = make_db()
        now = time.time()
        insert_row(db, "t1", "anthropic", "haiku", "agent1", now, cost=0.05)
        db.compute_rollups()

        result = db.get_rollup_timeseries(entity_type="model", metric="cost")
        assert len(result) >= 1
        assert any(row["value"] > 0 for row in result)

    def test_rollup_timeseries_provider_entity(self):
        """get_rollup_timeseries works with entity_type='provider'."""
        db = make_db()
        now = time.time()
        insert_row(db, "t1", "anthropic", "m", "a", now, cost=0.10)
        db.compute_rollups()

        result = db.get_rollup_timeseries(entity_type="provider", metric="cost")
        assert len(result) >= 1

    def test_rollup_timeseries_since_date_filter(self):
        """get_rollup_timeseries respects since_date filter."""
        db = make_db()
        import datetime
        old_ts = time.time() - 86400 * 5
        new_ts = time.time()
        insert_row(db, "t1", "anthropic", "m", "a", old_ts, cost=0.50)
        insert_row(db, "t2", "anthropic", "m", "a", new_ts, cost=0.20)
        db.compute_rollups()

        today = datetime.date.today().isoformat()
        result = db.get_rollup_timeseries(metric="cost", since_date=today)
        total = sum(row["value"] for row in result)
        assert abs(total - 0.20) < 1e-9
