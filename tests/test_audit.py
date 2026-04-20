"""tests/test_audit.py — Cost Audit & Breakdown Tests

Covers:
- model_breakdown() — cost aggregation by model
- feature_breakdown() — cost attribution by feature
- combined_breakdown() — unified view
- session_audit() — simplified dashboard audit
"""

from __future__ import annotations

import pytest

from tests.conftest import make_entries, make_entry
from tokenpak.agent.query.audit import AuditGenerator


class TestModelBreakdown:
    """AuditGenerator.model_breakdown() tests."""

    def test_single_model_single_entry(self):
        """Empty list → no breakdown, zero totals."""
        audit = AuditGenerator()
        entries = [make_entry(model="claude-sonnet-4-6", cost=0.05)]
        result = audit.model_breakdown(entries)

        assert result["total_cost"] == 0.05
        assert result["total_requests"] == 1
        assert "claude-sonnet-4-6" in result["models"]
        assert result["models"]["claude-sonnet-4-6"]["cost"] == 0.05
        assert result["models"]["claude-sonnet-4-6"]["requests"] == 1
        assert result["models"]["claude-sonnet-4-6"]["percentage"] == 100.0

    def test_multiple_models(self):
        """Multiple models sorted by cost descending."""
        audit = AuditGenerator()
        entries = [
            make_entry(model="claude-sonnet-4-6", cost=0.05),
            make_entry(model="claude-opus-4-5", cost=0.15),
            make_entry(model="gpt-4-turbo", cost=0.08),
        ]
        result = audit.model_breakdown(entries)

        assert result["total_cost"] == pytest.approx(0.28, abs=1e-2)
        assert result["total_requests"] == 3

        # Check first model is highest cost
        models_list = list(result["models"].items())
        assert models_list[0][0] == "claude-opus-4-5"
        assert models_list[0][1]["cost"] == 0.15

    def test_cost_percentage_calculation(self):
        """Percentages sum to 100."""
        audit = AuditGenerator()
        entries = [
            make_entry(model="model-a", cost=10.0),
            make_entry(model="model-b", cost=20.0),
            make_entry(model="model-c", cost=20.0),
        ]
        result = audit.model_breakdown(entries)

        percentages = [m["percentage"] for m in result["models"].values()]
        assert sum(percentages) == pytest.approx(100.0, abs=0.1)

    def test_same_model_aggregation(self):
        """Same model from multiple entries aggregates correctly."""
        audit = AuditGenerator()
        entries = [
            make_entry(model="claude-sonnet-4-6", cost=0.05),
            make_entry(model="claude-sonnet-4-6", cost=0.05),
            make_entry(model="claude-sonnet-4-6", cost=0.05),
        ]
        result = audit.model_breakdown(entries)

        assert result["total_requests"] == 3
        assert result["models"]["claude-sonnet-4-6"]["requests"] == 3
        assert result["models"]["claude-sonnet-4-6"]["cost"] == pytest.approx(0.15)

    def test_empty_entries(self):
        """Empty entries list → zero breakdown."""
        audit = AuditGenerator()
        result = audit.model_breakdown([])

        assert result["total_cost"] == 0.0
        assert result["total_requests"] == 0
        assert result["models"] == {}

    def test_unknown_model_handling(self):
        """Entries without model field get 'unknown' model."""
        audit = AuditGenerator()
        entries = [
            {"tokens": 100, "cost": 0.05},  # Missing 'model'
        ]
        result = audit.model_breakdown(entries)

        assert "unknown" in result["models"]
        assert result["models"]["unknown"]["cost"] == 0.05


class TestFeatureBreakdown:
    """AuditGenerator.feature_breakdown() tests."""

    def test_base_feature_only(self):
        """Entry without extra features → all cost in 'base'."""
        audit = AuditGenerator()
        entries = [make_entry(tokens=1000, cost=0.05)]
        result = audit.feature_breakdown(entries)

        assert "base" in result["features"]
        assert "caching" in result["features"]
        assert "compression" in result["features"]
        assert "tools" in result["features"]

        # Base should be the dominant cost
        assert result["features"]["base"]["cost"] > 0.0
        assert result["total_cost"] == pytest.approx(0.05)

    def test_cache_feature(self):
        """Cache tokens → caching feature cost."""
        audit = AuditGenerator()
        entries = [
            make_entry(tokens=1000, cost=0.05, cache_tokens=200),
        ]
        result = audit.feature_breakdown(entries)

        # Caching feature should exist and have cost
        assert result["features"]["caching"]["tokens"] == 200
        assert result["features"]["caching"]["cost"] > 0.0

    def test_compression_feature(self):
        """Compressed tokens → compression feature cost."""
        audit = AuditGenerator()
        entries = [
            make_entry(
                tokens=1000,
                cost=0.05,
                compressed_tokens=150,
                compression_ratio=1.2,
            ),
        ]
        result = audit.feature_breakdown(entries)

        assert result["features"]["compression"]["tokens"] == 150
        assert result["features"]["compression"]["cost"] > 0.0

    def test_tool_feature(self):
        """Tool tokens → tools feature cost."""
        audit = AuditGenerator()
        entries = [
            make_entry(tokens=1000, cost=0.05, tool_tokens=100),
        ]
        result = audit.feature_breakdown(entries)

        assert result["features"]["tools"]["tokens"] == 100
        assert result["features"]["tools"]["cost"] > 0.0

    def test_feature_percentages_sum_to_100(self):
        """Feature percentages sum to 100."""
        audit = AuditGenerator()
        entries = [
            make_entry(
                tokens=1000,
                cost=0.10,
                cache_tokens=100,
                compressed_tokens=50,
                tool_tokens=25,
            ),
        ]
        result = audit.feature_breakdown(entries)

        percentages = [f["percentage"] for f in result["features"].values()]
        assert sum(percentages) == pytest.approx(100.0, abs=0.5)

    def test_empty_entries_features(self):
        """Empty entries → zero feature breakdown."""
        audit = AuditGenerator()
        result = audit.feature_breakdown([])

        assert result["total_cost"] == 0.0
        assert all(f["cost"] == 0.0 for f in result["features"].values())

    def test_total_cost_preserved(self):
        """Total cost in feature breakdown = sum of entry costs."""
        audit = AuditGenerator()
        entries = make_entries(3)  # 0.05, 0.06, 0.07
        result = audit.feature_breakdown(entries)

        expected_total = sum(e["cost"] for e in entries)
        assert result["total_cost"] == pytest.approx(expected_total)


class TestCombinedBreakdown:
    """AuditGenerator.combined_breakdown() tests."""

    def test_combined_includes_models_and_features(self):
        """Combined result has both models and features."""
        audit = AuditGenerator()
        entries = [
            make_entry(model="claude-sonnet-4-6", cost=0.05),
            make_entry(model="claude-opus-4-5", cost=0.15),
        ]
        result = audit.combined_breakdown(entries)

        assert "models" in result
        assert "features" in result
        assert "total_cost" in result
        assert "total_requests" in result

    def test_summary_top_model(self):
        """Summary identifies top (highest cost) model."""
        audit = AuditGenerator()
        entries = [
            make_entry(model="cheap", cost=0.01),
            make_entry(model="expensive", cost=0.50),
        ]
        result = audit.combined_breakdown(entries)

        assert result["summary"]["top_model"] == "expensive"

    def test_summary_top_feature(self):
        """Summary identifies top feature."""
        audit = AuditGenerator()
        entries = [make_entry(tokens=1000, cost=0.05)]
        result = audit.combined_breakdown(entries)

        # Base is always the top feature in simple cases
        assert result["summary"]["top_feature"] in result["features"]

    def test_summary_model_count(self):
        """Summary reports number of distinct models."""
        audit = AuditGenerator()
        entries = [
            make_entry(model="model-a", cost=0.05),
            make_entry(model="model-b", cost=0.05),
            make_entry(model="model-c", cost=0.05),
        ]
        result = audit.combined_breakdown(entries)

        assert result["summary"]["model_count"] == 3


class TestSessionAudit:
    """AuditGenerator.session_audit() tests."""

    def test_session_audit_format(self):
        """Session audit returns simplified format."""
        audit = AuditGenerator()
        entries = make_entries(3)
        result = audit.session_audit(entries)

        assert "total_spend" in result
        assert "request_count" in result
        assert "avg_cost_per_request" in result
        assert "models" in result
        assert "features" in result

    def test_session_audit_avg_cost(self):
        """Average cost per request calculated correctly."""
        audit = AuditGenerator()
        entries = [
            make_entry(cost=0.10),
            make_entry(cost=0.20),
        ]
        result = audit.session_audit(entries)

        assert result["avg_cost_per_request"] == pytest.approx(0.15)

    def test_session_audit_zero_requests(self):
        """Zero requests → no division by zero."""
        audit = AuditGenerator()
        result = audit.session_audit([])

        assert result["total_spend"] == 0.0
        assert result["request_count"] == 0
        assert result["avg_cost_per_request"] == 0.0

    def test_session_audit_single_request(self):
        """Single request → accurate calculations."""
        audit = AuditGenerator()
        entries = [make_entry(cost=0.25)]
        result = audit.session_audit(entries)

        assert result["total_spend"] == 0.25
        assert result["request_count"] == 1
        assert result["avg_cost_per_request"] == pytest.approx(0.25)


class TestAuditEdgeCases:
    """Edge cases and robustness."""

    def test_zero_cost_entries(self):
        """Entries with zero cost don't crash breakdown."""
        audit = AuditGenerator()
        entries = [
            make_entry(cost=0.0),
            make_entry(cost=0.0),
        ]
        result = audit.model_breakdown(entries)

        assert result["total_cost"] == 0.0
        assert result["total_requests"] == 2

    def test_very_small_costs(self):
        """Very small costs handled with precision."""
        audit = AuditGenerator()
        entries = [
            make_entry(cost=0.000001),
            make_entry(cost=0.000002),
        ]
        result = audit.model_breakdown(entries)

        assert result["total_cost"] > 0.0

    def test_large_cost_values(self):
        """Large costs don't overflow."""
        audit = AuditGenerator()
        entries = [
            make_entry(cost=99999.99),
            make_entry(cost=100000.01),
        ]
        result = audit.model_breakdown(entries)

        assert result["total_cost"] == pytest.approx(200000.00, rel=1e-3)

    def test_missing_token_field(self):
        """Feature breakdown handles missing tokens field."""
        audit = AuditGenerator()
        entries = [{"cost": 0.05, "model": "test"}]  # No tokens
        result = audit.feature_breakdown(entries)

        # Should handle gracefully
        assert result["total_cost"] == 0.05

    def test_null_extra_field(self):
        """Null/missing extra field handled."""
        audit = AuditGenerator()
        entries = [
            make_entry(tokens=1000, cost=0.05),
            {"model": "test", "cost": 0.05, "extra": None},
        ]
        result = audit.feature_breakdown(entries)

        assert result["total_cost"] == pytest.approx(0.10)
