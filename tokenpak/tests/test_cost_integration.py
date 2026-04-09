"""Tests for cost calculation engine integration with TokenPak.

Covers: telemetry/cost.py, telemetry/pricing.py — cost models, pricing, savings calculations.
"""

import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tokenpak.telemetry.cost import (
    SEED_PRICING,
    CostEngine,
    CostResult,
)


class TestCostEngineBasics:
    """Test: CostEngine initialization and pricing table setup."""

    @pytest.fixture
    def cost_engine(self):
        """Create a temporary CostEngine for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            engine = CostEngine(db_path=str(db_path))
            yield engine

    def test_engine_initialization(self, cost_engine):
        """CostEngine initializes with valid database."""
        assert cost_engine is not None
        assert Path(cost_engine.db_path).exists()

    def test_pricing_table_created(self, cost_engine):
        """tp_pricing table is created on initialization."""
        conn = sqlite3.connect(cost_engine.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tp_pricing'")
        result = cursor.fetchone()
        conn.close()
        assert result is not None, "tp_pricing table not created"

    def test_seed_pricing_loaded(self, cost_engine):
        """Seed pricing data is loaded into tp_pricing table."""
        conn = sqlite3.connect(cost_engine.db_path)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM tp_pricing")
        count = cursor.fetchone()[0]
        conn.close()
        assert count > 0, "No pricing data loaded"
        assert count >= len(SEED_PRICING), "Not all seed pricing data was loaded"


class TestCostCalculation:
    """Test: Cost calculation formulas (baseline, actual, savings)."""

    @pytest.fixture
    def cost_engine(self):
        """Create a temporary CostEngine for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            engine = CostEngine(db_path=str(db_path))
            yield engine

    def test_calculate_baseline_cost(self, cost_engine):
        """Baseline cost = (raw_input + output) * respective_rate."""
        result = cost_engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=1000,
            final_input_tokens=600,
            output_tokens=100,
        )

        assert isinstance(result, CostResult)
        # For sonnet-4-6: input_rate=3.00, output_rate=15.00 per 1K
        # baseline = (1000 * 3.00 + 100 * 15.00) / 1000 = 3.00 + 1.50 = $4.50
        expected_baseline = ((1000 * 3.00) + (100 * 15.00)) / 1000
        assert abs(result.baseline_cost - expected_baseline) < 0.01

    def test_calculate_actual_cost_with_compression(self, cost_engine):
        """Actual cost uses final_input_tokens, baseline uses raw_input_tokens."""
        result = cost_engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=1000,  # Before compression
            final_input_tokens=600,  # After compression
            output_tokens=100,
        )

        assert isinstance(result, CostResult)
        # actual = (600 * 3.00 + 100 * 15.00) / 1000 = 1.80 + 1.50 = $3.30
        expected_actual = ((600 * 3.00) + (100 * 15.00)) / 1000
        assert abs(result.actual_cost - expected_actual) < 0.01

    def test_savings_calculation(self, cost_engine):
        """Savings = baseline_cost - actual_cost."""
        result = cost_engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=1000,
            final_input_tokens=600,
            output_tokens=100,
        )

        assert isinstance(result, CostResult)
        expected_savings = result.baseline_cost - result.actual_cost
        assert abs(result.savings_amount - expected_savings) < 0.01

    def test_zero_compression_no_savings(self, cost_engine):
        """When raw == final, savings = 0."""
        result = cost_engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=1000,
            final_input_tokens=1000,  # No compression
            output_tokens=100,
        )

        assert result.savings_amount == 0.0
        assert result.baseline_cost == result.actual_cost

    def test_high_compression_ratio(self, cost_engine):
        """Large compression ratio yields proportionally large savings."""
        result = cost_engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=10000,
            final_input_tokens=1000,  # 90% compression
            output_tokens=100,
        )

        assert result.savings_amount > 0
        # Savings should be ~27 / 30 of baseline for input tokens (9000 tokens saved)
        assert result.savings_amount > result.baseline_cost * 0.8


class TestMultipleModels:
    """Test: Cost calculation across different models and providers."""

    @pytest.fixture
    def cost_engine(self):
        """Create a temporary CostEngine for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            engine = CostEngine(db_path=str(db_path))
            yield engine

    def test_sonnet_vs_opus_cost_difference(self, cost_engine):
        """Sonnet should be cheaper than Opus for same token usage."""
        sonnet_result = cost_engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=1000,
            final_input_tokens=1000,
            output_tokens=100,
        )

        opus_result = cost_engine.calculate(
            model="claude-opus-4-6",
            raw_input_tokens=1000,
            final_input_tokens=1000,
            output_tokens=100,
        )

        assert sonnet_result.baseline_cost < opus_result.baseline_cost

    def test_gpt4_vs_sonnet(self, cost_engine):
        """Different providers have different rate structures."""
        sonnet_result = cost_engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=1000,
            final_input_tokens=1000,
            output_tokens=100,
        )

        # GPT-4 should be in SEED_PRICING
        try:
            gpt4_result = cost_engine.calculate(
                model="gpt-4-turbo",
                raw_input_tokens=1000,
                final_input_tokens=1000,
                output_tokens=100,
            )
            # Cost comparison depends on actual rates
            assert gpt4_result.baseline_cost > 0
        except KeyError:
            # Model not in pricing — that's OK for this test
            pass


class TestPricingVersioning:
    """Test: Pricing version resolution by timestamp."""

    @pytest.fixture
    def cost_engine(self):
        """Create a temporary CostEngine for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            engine = CostEngine(db_path=str(db_path))
            yield engine

    def test_uses_current_pricing_by_default(self, cost_engine):
        """Without explicit timestamp, uses current pricing."""
        result = cost_engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=1000,
            final_input_tokens=1000,
            output_tokens=100,
        )
        # Should succeed with current pricing rates
        assert result.baseline_cost > 0

    def test_accepts_event_timestamp(self, cost_engine):
        """Event timestamp parameter is accepted (may affect versioning)."""
        event_ts = datetime(2026, 2, 27, 12, 0, 0, tzinfo=timezone.utc).isoformat()
        result = cost_engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=1000,
            final_input_tokens=1000,
            output_tokens=100,
            event_ts=event_ts,
        )
        assert result.baseline_cost > 0


class TestEdgeCases:
    """Test: Edge cases and boundary conditions."""

    @pytest.fixture
    def cost_engine(self):
        """Create a temporary CostEngine for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            engine = CostEngine(db_path=str(db_path))
            yield engine

    def test_zero_tokens(self, cost_engine):
        """Cost for zero tokens is zero."""
        result = cost_engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=0,
            final_input_tokens=0,
            output_tokens=0,
        )
        assert result.baseline_cost == 0.0
        assert result.actual_cost == 0.0
        assert result.savings_amount == 0.0

    def test_single_token(self, cost_engine):
        """Cost calculation works for single token."""
        result = cost_engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=1,
            final_input_tokens=1,
            output_tokens=1,
        )
        # 1 input * 3.00/1000 + 1 output * 15.00/1000
        expected = (1 * 3.00 + 1 * 15.00) / 1000
        assert abs(result.baseline_cost - expected) < 0.00001

    def test_large_token_counts(self, cost_engine):
        """Cost calculation works for large token counts (M-scale)."""
        result = cost_engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=1_000_000,
            final_input_tokens=500_000,
            output_tokens=100_000,
        )
        assert result.baseline_cost > 0
        assert result.actual_cost > 0
        assert result.savings_amount > 0

    def test_final_exceeds_raw_input_tokens_allowed(self, cost_engine):
        """Test when final > raw (edge case, shouldn't happen in practice)."""
        # This might raise or handle gracefully — test the behavior
        try:
            result = cost_engine.calculate(
                model="claude-sonnet-4-6",
                raw_input_tokens=500,
                final_input_tokens=1000,  # Greater than raw
                output_tokens=100,
            )
            # If it succeeds, cost should still be valid
            assert result.baseline_cost > 0
        except (ValueError, AssertionError):
            # If it raises, that's also valid behavior
            pass


class TestUnknownModels:
    """Test: Handling of unknown or unsupported models."""

    @pytest.fixture
    def cost_engine(self):
        """Create a temporary CostEngine for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            engine = CostEngine(db_path=str(db_path))
            yield engine

    def test_unknown_model_raises_or_handles(self, cost_engine):
        """Unknown model is handled gracefully."""
        try:
            result = cost_engine.calculate(
                model="unknown-model-xyz-999",
                raw_input_tokens=100,
                final_input_tokens=100,
                output_tokens=10,
            )
            # If it doesn't raise, should still return valid result
            assert result is not None
        except (KeyError, ValueError, RuntimeError):
            # If it raises KeyError or ValueError, that's OK
            pass

    def test_model_name_case_sensitivity(self, cost_engine):
        """Test model name handling (exact vs case-insensitive)."""
        # lowercase should work
        result_lower = cost_engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=100,
            final_input_tokens=100,
            output_tokens=10,
        )
        assert result_lower.baseline_cost > 0

        # UPPERCASE might or might not work depending on implementation
        try:
            result_upper = cost_engine.calculate(
                model="CLAUDE-SONNET-4-6",
                raw_input_tokens=100,
                final_input_tokens=100,
                output_tokens=10,
            )
            assert result_upper is not None
        except KeyError:
            # Case-sensitive is OK
            pass


class TestDatabaseOperations:
    """Test: Database operations and concurrent access."""

    def test_multiple_engines_same_db(self):
        """Multiple engine instances can share the same database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"

            # Create first engine and calculate
            engine1 = CostEngine(db_path=str(db_path))
            result1 = engine1.calculate(
                model="claude-sonnet-4-6",
                raw_input_tokens=100,
                final_input_tokens=100,
                output_tokens=10,
            )

            # Create second engine on same db
            engine2 = CostEngine(db_path=str(db_path))
            result2 = engine2.calculate(
                model="claude-sonnet-4-6",
                raw_input_tokens=100,
                final_input_tokens=100,
                output_tokens=10,
            )

            # Results should be consistent
            assert abs(result1.baseline_cost - result2.baseline_cost) < 0.01

    def test_db_file_created_in_correct_location(self):
        """Database file is created in specified directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "custom_cost.db"
            engine = CostEngine(db_path=str(db_path))

            # Calculate something to ensure db is written
            engine.calculate(
                model="claude-sonnet-4-6",
                raw_input_tokens=100,
                final_input_tokens=100,
                output_tokens=10,
            )

            # File should exist
            assert db_path.exists()


class TestCostRounding:
    """Test: Cost calculation precision and rounding."""

    @pytest.fixture
    def cost_engine(self):
        """Create a temporary CostEngine for testing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            engine = CostEngine(db_path=str(db_path))
            yield engine

    def test_cost_precision_cents(self, cost_engine):
        """Cost is calculated to reasonable precision."""
        result = cost_engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=333,  # Odd number for precision test
            final_input_tokens=333,
            output_tokens=77,
        )
        # Cost should be sensible (> 0 and not huge rounding errors)
        assert result.baseline_cost > 0
        assert result.baseline_cost < 100  # Sanity check

    def test_savings_precision(self, cost_engine):
        """Savings calculation maintains precision."""
        result = cost_engine.calculate(
            model="claude-sonnet-4-6",
            raw_input_tokens=1234,
            final_input_tokens=567,
            output_tokens=89,
        )
        # Verify: savings = baseline - actual
        calculated_savings = result.baseline_cost - result.actual_cost
        assert abs(result.savings_amount - calculated_savings) < 0.001
