"""
test_edge_calculation.py — Edge Calculation Integration Tests

STATUS: BLOCKED — trackedge/model/edge_engine.py not yet implemented.

Required module: trackedge/model/edge_engine.py
Required exports:
  - edge_calculation(model_prob: float, market_prob: float) -> EdgeResult
  - market_probability(ml_odds: str) -> float
  - EdgeResult: dataclass with .edge (float), .classification (str), .market_prob (float)

To unblock:
  1. Implement trackedge/model/edge_engine.py
  2. Rerun: pytest tests/test_edge_calculation.py -v

Edge classification reference:
  - edge > 15%  → "strong_value"
  - 5-15%       → "value"
  - -5 to 5%    → "neutral"
  - -15 to -5%  → "slight_overbet"
  - < -15%      → "overbet"

Market probability formula: 1 / (decimal_odds) OR ML odds → decimal → 1/decimal
  - "2-1" → decimal 3.0 → market_prob 0.333
  - "4-1" → decimal 5.0 → market_prob 0.200
  - "6-5" → decimal 2.2 → market_prob 0.455
  - "Even" / "1-1" → decimal 2.0 → market_prob 0.500
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ---------------------------------------------------------------------------
# Module availability check
# ---------------------------------------------------------------------------

EDGE_ENGINE_AVAILABLE = False
try:
    from trackedge.model.edge_engine import edge_calculation, market_probability
    EDGE_ENGINE_AVAILABLE = True
except ImportError:
    pass

SKIP_EDGE = pytest.mark.skipif(
    not EDGE_ENGINE_AVAILABLE,
    reason=(
        "MISSING: trackedge/model/edge_engine.py\n"
        "Implement edge_calculation() and market_probability() to enable these tests"
    ),
)


# ---------------------------------------------------------------------------
# Market Probability Tests
# ---------------------------------------------------------------------------

class TestMarketProbability:

    @SKIP_EDGE
    def test_even_money(self):
        """Even money (1-1) → 50% market probability."""
        prob = market_probability("1-1")
        assert abs(prob - 0.500) < 0.005

    @SKIP_EDGE
    def test_2_to_1(self):
        """2-1 → ~33.3%."""
        prob = market_probability("2-1")
        assert abs(prob - 0.333) < 0.005

    @SKIP_EDGE
    def test_4_to_1(self):
        """4-1 → 20%."""
        prob = market_probability("4-1")
        assert abs(prob - 0.200) < 0.005

    @SKIP_EDGE
    def test_9_to_2(self):
        """9-2 → ~18.2%."""
        prob = market_probability("9-2")
        assert abs(prob - 0.182) < 0.005

    @SKIP_EDGE
    def test_odds_on_6_to_5(self):
        """6-5 (odds-on) → ~45.5%."""
        prob = market_probability("6-5")
        assert abs(prob - 0.455) < 0.005

    @SKIP_EDGE
    def test_longshot_20_to_1(self):
        """20-1 → ~4.8%."""
        prob = market_probability("20-1")
        assert abs(prob - 0.048) < 0.005

    @SKIP_EDGE
    def test_probability_in_range(self):
        """All market probabilities must be in (0, 1)."""
        for odds_str in ["2-1", "4-1", "6-1", "10-1", "15-1", "30-1", "6-5", "1-1"]:
            prob = market_probability(odds_str)
            assert 0 < prob < 1, f"Probability {prob} out of range for {odds_str}"


# ---------------------------------------------------------------------------
# Edge Calculation Tests
# ---------------------------------------------------------------------------

class TestEdgeCalculation:

    @SKIP_EDGE
    def test_strong_value_classification(self):
        """35% model, 20% market → ~+15% edge → strong_value."""
        result = edge_calculation(0.35, 0.20)
        assert 10 < result.edge < 20, f"Edge {result.edge} out of expected range"
        assert result.classification == "strong_value"

    @SKIP_EDGE
    def test_slight_overbet_classification(self):
        """25% model, 30% market → ~-5% edge → slight_overbet."""
        result = edge_calculation(0.25, 0.30)
        assert -6 < result.edge < -4, f"Edge {result.edge} out of expected range"
        assert result.classification in ["slight_overbet", "overbet"]

    @SKIP_EDGE
    def test_neutral_classification(self):
        """30% model, 32% market → ~-2% → neutral."""
        result = edge_calculation(0.30, 0.32)
        assert -5 < result.edge < 5
        assert result.classification == "neutral"

    @SKIP_EDGE
    def test_edge_result_has_required_fields(self):
        """EdgeResult must have .edge, .classification, .market_prob."""
        result = edge_calculation(0.30, 0.25)
        assert hasattr(result, "edge"), "EdgeResult must have .edge"
        assert hasattr(result, "classification"), "EdgeResult must have .classification"
        assert hasattr(result, "market_prob"), "EdgeResult must have .market_prob"

    @SKIP_EDGE
    def test_edge_is_float(self):
        result = edge_calculation(0.30, 0.25)
        assert isinstance(result.edge, float)

    @SKIP_EDGE
    def test_market_prob_matches_input(self):
        """EdgeResult.market_prob must match the market_prob passed in."""
        result = edge_calculation(0.35, 0.20)
        assert abs(result.market_prob - 0.20) < 1e-9

    @SKIP_EDGE
    def test_edge_in_realistic_range(self):
        """Edge must be in -100 to +100 range."""
        for model_p, market_p in [(0.10, 0.50), (0.50, 0.10), (0.25, 0.25)]:
            result = edge_calculation(model_p, market_p)
            assert -100 <= result.edge <= 100, f"Edge {result.edge} out of -100/+100 bounds"

    @SKIP_EDGE
    def test_classification_values_are_valid(self):
        """Classification must be one of the known values."""
        valid = {"strong_value", "value", "neutral", "slight_overbet", "overbet"}
        for model_p, market_p in [(0.50, 0.10), (0.30, 0.20), (0.25, 0.25), (0.20, 0.30), (0.10, 0.50)]:
            result = edge_calculation(model_p, market_p)
            assert result.classification in valid, (
                f"Unknown classification {result.classification!r}"
            )


# ---------------------------------------------------------------------------
# End-to-End: Market Probability → Edge (chained)
# ---------------------------------------------------------------------------

class TestMarketToEdgeChain:

    @SKIP_EDGE
    def test_chain_2_1_with_high_model_prob(self):
        """2-1 ML odds horse with 40% model prob → +7% edge → value."""
        market_prob = market_probability("2-1")  # ~33.3%
        result = edge_calculation(0.40, market_prob)
        assert result.edge > 5  # Positive edge
        assert result.classification in ["strong_value", "value"]

    @SKIP_EDGE
    def test_chain_4_1_with_low_model_prob(self):
        """4-1 ML horse with 15% model prob → negative edge."""
        market_prob = market_probability("4-1")  # 20%
        result = edge_calculation(0.15, market_prob)
        assert result.edge < 0  # Overbet

    @SKIP_EDGE
    def test_chain_9_race_no_errors(self):
        """Run market→edge chain for all horses in synthetic 9-race field."""
        test_cases = [
            ("2-1", 0.40), ("4-1", 0.25), ("6-1", 0.18), ("10-1", 0.12),
            ("15-1", 0.08), ("20-1", 0.06), ("6-5", 0.50), ("8-5", 0.40), ("3-1", 0.30),
        ]
        for odds, model_p in test_cases:
            market_prob = market_probability(odds)
            result = edge_calculation(model_p, market_prob)
            assert result is not None
            assert isinstance(result.edge, float)
            assert -100 <= result.edge <= 100


# ---------------------------------------------------------------------------
# Missing Module Report
# ---------------------------------------------------------------------------

class TestMissingEdgeModuleReport:
    """Document the exact error so implementation is clear."""

    def test_import_attempt_documents_failure(self):
        """Report the import error for edge_engine."""
        try:
            from trackedge.model.edge_engine import edge_calculation  # noqa
            # If we reach here, module exists
            assert True, "edge_engine is now available"
        except ImportError as e:
            # Document the missing module — this is expected right now
            pytest.skip(
                f"edge_engine import failed: {e}\n\n"
                "To implement trackedge/model/edge_engine.py:\n"
                "  1. Create EdgeResult dataclass with: edge, classification, market_prob\n"
                "  2. Implement market_probability(ml_odds: str) -> float\n"
                "     - Parse 'N-M' format: decimal = N/M + 1, prob = 1/decimal\n"
                "  3. Implement edge_calculation(model_prob, market_prob) -> EdgeResult\n"
                "     - edge = (model_prob - market_prob) * 100\n"
                "     - classification based on edge threshold"
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
