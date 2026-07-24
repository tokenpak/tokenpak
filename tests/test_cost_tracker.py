"""Tests for tokenpak.telemetry.cost_tracker."""

import pytest

from tokenpak.telemetry.cost_tracker import CostTracker, estimate_cost, get_cost_tracker

# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------


class TestEstimateCost:
    def test_known_model_sonnet(self):
        # 1M input + 1M output = $3 + $15 = $18
        cost = estimate_cost("claude-sonnet-4-5", 1_000_000, 1_000_000)
        assert abs(cost - 18.0) < 0.001

    def test_known_model_haiku(self):
        # 1M input + 1M output = $0.80 + $4.00 = $4.80
        cost = estimate_cost("claude-haiku-3-5", 1_000_000, 1_000_000)
        assert abs(cost - 4.80) < 0.001

    def test_known_model_gpt4o(self):
        cost = estimate_cost("gpt-4o", 1_000_000, 1_000_000)
        assert abs(cost - 12.50) < 0.001

    def test_known_model_gemini_flash(self):
        cost = estimate_cost("gemini-2-flash", 1_000_000, 1_000_000)
        assert abs(cost - 0.375) < 0.001

    def test_known_model_codex(self):
        # 1M input + 1M output = $1.50 + $6.00 = $7.50
        cost = estimate_cost("codex", 1_000_000, 1_000_000)
        assert abs(cost - 7.50) < 0.001

    def test_generic_fallback_model(self):
        # Unknown model → registry default: $3/1M input, $15/1M output (sonnet-class)
        cost = estimate_cost("unknown-model-xyz", 1_000_000, 1_000_000)
        assert abs(cost - 18.0) < 0.001

    def test_zero_tokens(self):
        cost = estimate_cost("gpt-4o", 0, 0)
        assert cost == 0.0

    def test_small_request(self):
        # 100 prompt + 50 completion for haiku ($0.80/$4.00 per MTok)
        cost = estimate_cost("claude-haiku-3-5", 100, 50)
        expected = (100 * 0.80 + 50 * 4.00) / 1_000_000
        assert abs(cost - expected) < 1e-10

    def test_prefix_match(self):
        # Model name with date suffix should still match
        cost = estimate_cost("claude-sonnet-4-5-20241022", 1_000_000, 0)
        assert abs(cost - 3.0) < 0.001


# ---------------------------------------------------------------------------
# CostTracker
# ---------------------------------------------------------------------------


@pytest.fixture
def tracker():
    """In-memory tracker for isolation."""
    t = CostTracker(":memory:")
    yield t
    t.close()


class TestRecordRequest:
    def test_default_constructor_remains_in_memory(self):
        default_tracker = CostTracker()
        try:
            assert default_tracker._db_path == ":memory:"
        finally:
            default_tracker.close()

    def test_inserts_row_and_returns_cost(self, tracker):
        cost = tracker.record_request("gpt-4o", 1000, 500)
        expected = estimate_cost("gpt-4o", 1000, 500)
        assert abs(cost - expected) < 1e-10

    def test_returns_float(self, tracker):
        result = tracker.record_request("claude-haiku-3-5", 500, 100)
        assert isinstance(result, float)

    def test_multiple_inserts_accumulate(self, tracker):
        tracker.record_request("gpt-4o", 1000, 500)
        tracker.record_request("gpt-4o", 2000, 1000)
        summary = tracker.get_summary("all")
        assert summary["total_requests"] == 2

    def test_fallback_model(self, tracker):
        # Unknown model → registry default: $3/$15 (sonnet-class) = $18
        cost = tracker.record_request("totally-unknown-model", 1_000_000, 1_000_000)
        assert abs(cost - 18.0) < 0.001


class TestGetSummary:
    def test_empty_db_returns_zeros(self, tracker):
        summary = tracker.get_summary("day")
        assert summary["total_requests"] == 0
        assert summary["total_tokens"] == 0
        assert summary["total_cost_usd"] == 0.0

    def test_all_period_aggregates_all(self, tracker):
        tracker.record_request("gpt-4o", 1000, 200)
        tracker.record_request("claude-haiku-3-5", 500, 100)
        summary = tracker.get_summary("all")
        assert summary["total_requests"] == 2
        assert summary["prompt_tokens"] == 1500
        assert summary["completion_tokens"] == 300
        assert summary["total_tokens"] == 1800

    def test_cost_usd_correct(self, tracker):
        tracker.record_request("gpt-4o", 1_000_000, 0)
        summary = tracker.get_summary("all")
        assert abs(summary["total_cost_usd"] - 2.50) < 0.001

    def test_period_key_in_result(self, tracker):
        for p in ("day", "week", "month", "all"):
            s = tracker.get_summary(p)
            assert s["period"] == p

    def test_summary_includes_all_keys(self, tracker):
        tracker.record_request("gpt-4o", 100, 50)
        summary = tracker.get_summary("all")
        for key in (
            "period",
            "total_requests",
            "total_tokens",
            "total_cost_usd",
            "prompt_tokens",
            "completion_tokens",
        ):
            assert key in summary


class TestGetByModel:
    def test_empty_db_returns_empty_list(self, tracker):
        assert tracker.get_by_model("all") == []

    def test_groups_by_model(self, tracker):
        tracker.record_request("gpt-4o", 1000, 200)
        tracker.record_request("gpt-4o", 500, 100)
        tracker.record_request("claude-haiku-3-5", 300, 50)
        rows = tracker.get_by_model("all")
        models = {r["model"] for r in rows}
        assert "gpt-4o" in models
        assert "claude-haiku-3-5" in models

    def test_request_counts_per_model(self, tracker):
        tracker.record_request("gpt-4o", 100, 10)
        tracker.record_request("gpt-4o", 200, 20)
        tracker.record_request("claude-haiku-3-5", 50, 5)
        rows = {r["model"]: r for r in tracker.get_by_model("all")}
        assert rows["gpt-4o"]["requests"] == 2
        assert rows["claude-haiku-3-5"]["requests"] == 1

    def test_token_totals_per_model(self, tracker):
        tracker.record_request("gpt-4o", 1000, 500)
        tracker.record_request("gpt-4o", 2000, 1000)
        rows = {r["model"]: r for r in tracker.get_by_model("all")}
        assert rows["gpt-4o"]["prompt_tokens"] == 3000
        assert rows["gpt-4o"]["completion_tokens"] == 1500
        assert rows["gpt-4o"]["total_tokens"] == 4500

    def test_ordered_by_cost_desc(self, tracker):
        tracker.record_request("claude-haiku-3-5", 100, 10)  # cheap
        tracker.record_request("gpt-4o", 1_000_000, 500_000)  # expensive
        rows = tracker.get_by_model("all")
        assert rows[0]["model"] == "gpt-4o"  # highest cost first


class TestSingleton:
    def test_get_cost_tracker_returns_same_instance(self):
        t1 = get_cost_tracker()
        t2 = get_cost_tracker()
        assert t1 is t2
