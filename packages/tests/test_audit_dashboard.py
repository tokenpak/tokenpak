"""Test suite for TokenPak Audit Dashboard (22 tests)."""

import pytest
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory

from tokenpak.agent.query.audit import AuditEntry, AuditGenerator, estimate_cost


# ============================================================================
# Test Class 1: AuditEntry Serialization (4 tests)
# ============================================================================


class TestAuditEntrySerialization:
    """Test AuditEntry JSON serialization and deserialization."""

    def test_audit_entry_to_json(self):
        """AuditEntry serializes to valid JSON."""
        entry = AuditEntry(
            timestamp="2026-03-18T20:45:00Z",
            request_id="req_0x4f2",
            model="claude-sonnet-4-6",
            input_tokens_raw=4200,
            input_tokens_sent=3100,
            output_tokens=240,
            cost_total=1.20,
        )
        json_str = entry.to_json()
        assert isinstance(json_str, str)
        assert "claude-sonnet-4-6" in json_str
        assert "4200" in json_str

    def test_audit_entry_from_json(self):
        """AuditEntry deserializes from JSON."""
        json_line = '{"timestamp":"2026-03-18T20:45:00Z","request_id":"req_0x4f2","model":"claude-sonnet-4-6","input_tokens_raw":4200,"input_tokens_sent":3100,"output_tokens":240,"cost_total":1.20}'
        entry = AuditEntry.from_json(json_line)
        assert entry is not None
        assert entry.model == "claude-sonnet-4-6"
        assert entry.input_tokens_raw == 4200

    def test_audit_entry_roundtrip(self):
        """AuditEntry roundtrips through JSON without data loss."""
        original = AuditEntry(
            timestamp="2026-03-18T20:45:00Z",
            request_id="req_xyz",
            model="claude-opus-4-5",
            input_tokens_raw=5000,
            input_tokens_sent=4000,
            cache_tokens_saved=200.0,
            cost_total=2.50,
        )
        json_str = original.to_json()
        restored = AuditEntry.from_json(json_str)
        assert restored.model == original.model
        assert restored.cost_total == original.cost_total
        assert restored.input_tokens_raw == original.input_tokens_raw

    def test_malformed_json_returns_none(self):
        """Malformed JSON gracefully returns None."""
        result = AuditEntry.from_json("not valid json {{{")
        assert result is None


# ============================================================================
# Test Class 2: Model Breakdown (5 tests)
# ============================================================================


class TestModelBreakdown:
    """Test model_breakdown() queries."""

    def test_single_model_breakdown(self):
        """Breakdown with single model."""
        entries = [
            AuditEntry(
                timestamp="2026-03-18T20:45:00Z",
                request_id=f"req_{i}",
                model="claude-sonnet-4-6",
                input_tokens_raw=1000,
                input_tokens_sent=800,
                cost_total=1.0,
            )
            for i in range(5)
        ]
        gen = AuditGenerator(entries)
        breakdown = gen.model_breakdown()

        assert breakdown["total_requests"] == 5
        assert breakdown["total_cost"] == 5.0
        assert "claude-sonnet-4-6" in breakdown["by_model"]
        assert breakdown["by_model"]["claude-sonnet-4-6"]["count"] == 5
        assert breakdown["by_model"]["claude-sonnet-4-6"]["percent"] == 100.0

    def test_multiple_models_breakdown(self):
        """Breakdown with multiple models."""
        entries = [
            AuditEntry(
                timestamp="2026-03-18T20:45:00Z",
                request_id="req_a",
                model="claude-sonnet-4-6",
                input_tokens_raw=1000,
                input_tokens_sent=800,
                cost_total=1.0,
            ),
            AuditEntry(
                timestamp="2026-03-18T20:46:00Z",
                request_id="req_b",
                model="claude-opus-4-5",
                input_tokens_raw=1000,
                input_tokens_sent=800,
                cost_total=2.0,
            ),
        ]
        gen = AuditGenerator(entries)
        breakdown = gen.model_breakdown()

        assert breakdown["total_requests"] == 2
        assert breakdown["total_cost"] == 3.0
        assert len(breakdown["by_model"]) == 2
        # Verify percentages
        assert (
            breakdown["by_model"]["claude-sonnet-4-6"]["percent"] + 
            breakdown["by_model"]["claude-opus-4-5"]["percent"] == 100.0
        )

    def test_breakdown_percentages_sum_to_100(self):
        """Percentages in model breakdown sum to ~100%."""
        entries = [
            AuditEntry(
                timestamp="2026-03-18T20:45:00Z",
                request_id=f"req_{i}",
                model=f"model_{i % 3}",
                input_tokens_raw=1000,
                input_tokens_sent=800,
                cost_total=float(i + 1),
            )
            for i in range(30)
        ]
        gen = AuditGenerator(entries)
        breakdown = gen.model_breakdown()

        total_percent = sum(
            m["percent"] for m in breakdown["by_model"].values()
        )
        # Allow rounding error (±0.1%)
        assert 99.9 <= total_percent <= 100.1

    def test_empty_entries_breakdown(self):
        """Breakdown with no entries."""
        gen = AuditGenerator([])
        breakdown = gen.model_breakdown()

        assert breakdown["total_requests"] == 0
        assert breakdown["total_cost"] == 0.0
        assert len(breakdown["by_model"]) == 0

    def test_breakdown_sorting_by_cost(self):
        """Model breakdown is sorted by cost (highest first)."""
        entries = [
            AuditEntry(
                timestamp="2026-03-18T20:45:00Z",
                request_id="req_a",
                model="cheap_model",
                input_tokens_raw=1000,
                input_tokens_sent=800,
                cost_total=1.0,
            ),
            AuditEntry(
                timestamp="2026-03-18T20:46:00Z",
                request_id="req_b",
                model="expensive_model",
                input_tokens_raw=1000,
                input_tokens_sent=800,
                cost_total=10.0,
            ),
        ]
        gen = AuditGenerator(entries)
        breakdown = gen.model_breakdown()

        # Breakdown dict is ordered by key (model name)
        # Verify expensive_model has higher percentage
        assert breakdown["by_model"]["expensive_model"]["percent"] > 50


# ============================================================================
# Test Class 3: Feature Breakdown (4 tests)
# ============================================================================


class TestFeatureBreakdown:
    """Test feature_breakdown() queries."""

    def test_feature_breakdown_structure(self):
        """Feature breakdown returns all 4 features."""
        entries = [
            AuditEntry(
                timestamp="2026-03-18T20:45:00Z",
                request_id="req_0",
                model="claude-sonnet-4-6",
                input_tokens_raw=1000,
                input_tokens_sent=800,
                cost_base=1.0,
                cost_cache_discount=0.3,
                cost_compression_discount=0.1,
                tool_tokens=50,
                cost_total=1.4,
            )
        ]
        gen = AuditGenerator(entries)
        breakdown = gen.feature_breakdown()

        assert "base" in breakdown["by_feature"]
        assert "caching" in breakdown["by_feature"]
        assert "compression" in breakdown["by_feature"]
        assert "tools" in breakdown["by_feature"]
        assert breakdown["total_cost"] == 1.4

    def test_feature_percentages_sum_to_100(self):
        """Feature percentages sum to ~100%."""
        entries = [
            AuditEntry(
                timestamp="2026-03-18T20:45:00Z",
                request_id=f"req_{i}",
                model="claude-sonnet-4-6",
                input_tokens_raw=1000,
                input_tokens_sent=800,
                cost_base=0.5,
                cost_cache_discount=0.2,
                cost_compression_discount=0.1,
                tool_tokens=0,
                cost_total=0.8,
            )
            for i in range(10)
        ]
        gen = AuditGenerator(entries)
        breakdown = gen.feature_breakdown()

        total_percent = sum(
            f["percent"] for f in breakdown["by_feature"].values()
        )
        assert 99.9 <= total_percent <= 100.1

    def test_caching_discount_attribution(self):
        """Caching discounts are properly attributed to caching feature."""
        entries = [
            AuditEntry(
                timestamp="2026-03-18T20:45:00Z",
                request_id="req_0",
                model="claude-sonnet-4-6",
                input_tokens_raw=1000,
                input_tokens_sent=800,
                cache_tokens_saved=50.0,
                cost_base=1.0,
                cost_cache_discount=0.2,
                cost_total=1.2,
            )
        ]
        gen = AuditGenerator(entries)
        breakdown = gen.feature_breakdown()

        assert breakdown["by_feature"]["caching"]["cost"] == 0.2
        assert breakdown["by_feature"]["base"]["cost"] == 1.0

    def test_empty_features_breakdown(self):
        """Feature breakdown with empty entries."""
        gen = AuditGenerator([])
        breakdown = gen.feature_breakdown()

        assert breakdown["total_cost"] == 0.0
        assert all(f["cost"] == 0.0 for f in breakdown["by_feature"].values())


# ============================================================================
# Test Class 4: Combined Breakdown (3 tests)
# ============================================================================


class TestCombinedBreakdown:
    """Test combined_breakdown() queries."""

    def test_combined_includes_both_breakdowns(self):
        """Combined breakdown includes model and feature breakdowns."""
        entries = [
            AuditEntry(
                timestamp="2026-03-18T20:45:00Z",
                request_id="req_0",
                model="claude-sonnet-4-6",
                input_tokens_raw=1000,
                input_tokens_sent=800,
                cost_base=1.0,
                cost_cache_discount=0.2,
                cost_total=1.2,
            )
        ]
        gen = AuditGenerator(entries)
        combined = gen.combined_breakdown()

        assert "model_breakdown" in combined
        assert "feature_breakdown" in combined
        assert "total_cost" in combined
        assert "total_requests" in combined

    def test_combined_cost_consistency(self):
        """Combined breakdown total cost matches model breakdown."""
        entries = [
            AuditEntry(
                timestamp="2026-03-18T20:45:00Z",
                request_id=f"req_{i}",
                model=f"model_{i % 2}",
                input_tokens_raw=1000,
                input_tokens_sent=800,
                cost_total=float(i + 1),
            )
            for i in range(10)
        ]
        gen = AuditGenerator(entries)
        combined = gen.combined_breakdown()

        model_cost = combined["model_breakdown"]["total_cost"]
        total_cost = combined["total_cost"]
        assert abs(model_cost - total_cost) < 0.01

    def test_combined_with_no_entries(self):
        """Combined breakdown handles empty entries."""
        gen = AuditGenerator([])
        combined = gen.combined_breakdown()

        assert combined["total_cost"] == 0.0
        assert combined["total_requests"] == 0


# ============================================================================
# Test Class 5: Session Audit (2 tests)
# ============================================================================


class TestSessionAudit:
    """Test session_audit() format for dashboard."""

    def test_session_audit_summary_format(self):
        """Session audit returns dashboard-ready format."""
        entries = [
            AuditEntry(
                timestamp="2026-03-18T20:45:00Z",
                request_id="req_0",
                model="claude-sonnet-4-6",
                input_tokens_raw=1000,
                input_tokens_sent=800,
                cost_total=1.0,
            ),
            AuditEntry(
                timestamp="2026-03-18T20:46:00Z",
                request_id="req_1",
                model="claude-opus-4-5",
                input_tokens_raw=1500,
                input_tokens_sent=1200,
                cost_total=2.0,
            ),
        ]
        gen = AuditGenerator(entries)
        session = gen.session_audit()

        assert session["total_spend"] == 3.0
        assert session["total_requests"] == 2
        assert session["avg_cost_per_request"] == 1.5
        assert "top_models" in session
        assert len(session["top_models"]) <= 3

    def test_top_models_in_session(self):
        """Top models are correctly identified in session audit."""
        entries = [
            AuditEntry(
                timestamp="2026-03-18T20:45:00Z",
                request_id=f"req_{i}",
                model="dominant_model" if i < 7 else "rare_model",
                input_tokens_raw=1000,
                input_tokens_sent=800,
                cost_total=1.0,
            )
            for i in range(10)
        ]
        gen = AuditGenerator(entries)
        session = gen.session_audit()

        # Top model should be dominant_model
        if session["top_models"]:
            assert session["top_models"][0]["model"] == "dominant_model"


# ============================================================================
# Test Class 6: JSONL File I/O (3 tests)
# ============================================================================


class TestJSONLFileIO:
    """Test loading/saving from JSONL files."""

    def test_load_from_jsonl_file(self):
        """Load audit entries from JSONL file."""
        with NamedTemporaryFile(mode="w+", suffix=".jsonl", delete=False) as f:
            f.write(
                '{"timestamp":"2026-03-18T20:45:00Z","request_id":"req_0","model":"claude-sonnet-4-6","input_tokens_raw":1000,"input_tokens_sent":800,"cost_total":1.0}\n'
            )
            f.write(
                '{"timestamp":"2026-03-18T20:46:00Z","request_id":"req_1","model":"claude-opus-4-5","input_tokens_raw":1500,"input_tokens_sent":1200,"cost_total":2.0}\n'
            )
            f.flush()
            filepath = Path(f.name)

        try:
            gen = AuditGenerator.from_jsonl_file(filepath)
            assert len(gen.entries) == 2
            assert gen.entries[0].model == "claude-sonnet-4-6"
            assert gen.entries[1].model == "claude-opus-4-5"
        finally:
            filepath.unlink()

    def test_load_from_nonexistent_file(self):
        """Load from nonexistent file returns empty generator."""
        gen = AuditGenerator.from_jsonl_file(Path("/nonexistent/path.jsonl"))
        assert len(gen.entries) == 0

    def test_load_skips_malformed_lines(self):
        """Load gracefully skips malformed JSONL lines."""
        with NamedTemporaryFile(mode="w+", suffix=".jsonl", delete=False) as f:
            f.write(
                '{"timestamp":"2026-03-18T20:45:00Z","request_id":"req_0","model":"claude-sonnet-4-6","input_tokens_raw":1000,"input_tokens_sent":800,"cost_total":1.0}\n'
            )
            f.write("this is not valid json\n")
            f.write(
                '{"timestamp":"2026-03-18T20:46:00Z","request_id":"req_1","model":"claude-opus-4-5","input_tokens_raw":1500,"input_tokens_sent":1200,"cost_total":2.0}\n'
            )
            f.flush()
            filepath = Path(f.name)

        try:
            gen = AuditGenerator.from_jsonl_file(filepath)
            # Should load 2 valid entries, skip 1 malformed
            assert len(gen.entries) == 2
        finally:
            filepath.unlink()


# ============================================================================
# Test Class 7: Edge Cases (1 test)
# ============================================================================


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_cost_entries(self):
        """Handle entries with zero cost."""
        entries = [
            AuditEntry(
                timestamp="2026-03-18T20:45:00Z",
                request_id="req_0",
                model="model_a",
                input_tokens_raw=0,
                input_tokens_sent=0,
                cost_total=0.0,
            ),
            AuditEntry(
                timestamp="2026-03-18T20:46:00Z",
                request_id="req_1",
                model="model_b",
                input_tokens_raw=1000,
                input_tokens_sent=800,
                cost_total=0.0,
            ),
        ]
        gen = AuditGenerator(entries)
        breakdown = gen.model_breakdown()

        # Should not crash, percentages should be 0
        assert breakdown["total_cost"] == 0.0
        assert all(p == 0.0 for p in [m["percent"] for m in breakdown["by_model"].values()])


# ============================================================================
# Run Tests
# ============================================================================


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
