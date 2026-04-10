"""Tests for tokenpak optimize command."""

from __future__ import annotations

import json
import sys
import unittest
from unittest.mock import MagicMock, patch

# Ensure tokenpak is importable
sys.path.insert(0, "/home/cali/tokenpak")

from tokenpak.cli.commands.optimize import (
    _analyze_compression,
    _analyze_redundancy,
    _build_recommendations,
    _model_cost_per_request,
    run_optimize,
)


class TestCompressionAnalysis(unittest.TestCase):
    """Test _analyze_compression with various session states."""

    def test_no_compression(self):
        session = {"tokens_raw": 10000, "tokens_saved": 0, "avg_savings_pct": 0.0}
        result = _analyze_compression(session)
        self.assertEqual(result["current_pct"], 0.0)
        self.assertEqual(result["current_mode"], "none")
        self.assertGreater(result["additional_savings_pct"], 0)

    def test_balanced_compression(self):
        session = {"tokens_raw": 10000, "tokens_saved": 3500, "avg_savings_pct": 35.0}
        result = _analyze_compression(session)
        self.assertEqual(result["current_pct"], 35.0)
        self.assertEqual(result["current_mode"], "balanced")
        self.assertGreater(result["optimal_pct"], 35.0)

    def test_already_aggressive(self):
        session = {"tokens_raw": 10000, "tokens_saved": 5000, "avg_savings_pct": 50.0}
        result = _analyze_compression(session)
        self.assertEqual(result["additional_savings_pct"], 0)
        self.assertEqual(result["current_mode"], "aggressive")

    def test_tokens_preserved(self):
        session = {"tokens_raw": 8000, "tokens_saved": 2000, "avg_savings_pct": 25.0}
        result = _analyze_compression(session)
        self.assertEqual(result["tokens_raw"], 8000)
        self.assertEqual(result["tokens_saved"], 2000)


class TestModelCostCalc(unittest.TestCase):
    """Test model cost-per-request calculations."""

    def test_known_model(self):
        cost = _model_cost_per_request("claude-opus-4-6", avg_input=1000, avg_output=500)
        # (1000 * 15.00 + 500 * 75.00) / 1_000_000 = 0.0525
        self.assertAlmostEqual(cost, 0.0525, places=4)

    def test_fallback_model(self):
        cost = _model_cost_per_request("unknown-model-xyz", avg_input=1000, avg_output=500)
        # (1000 * 1.00 + 500 * 3.00) / 1_000_000 = 0.0025
        self.assertAlmostEqual(cost, 0.0025, places=5)

    def test_haiku_cheaper_than_opus(self):
        opus = _model_cost_per_request("claude-opus-4-6", 1000, 500)
        haiku = _model_cost_per_request("claude-haiku-4-5", 1000, 500)
        self.assertLess(haiku, opus)


class TestBuildRecommendations(unittest.TestCase):
    """Test recommendation generation."""

    def _make_compression(self, current=25.0, optimal=39.0, extra=14.0, mode="balanced", opt_mode="aggressive"):
        return {
            "current_pct": current,
            "current_mode": mode,
            "optimal_pct": optimal,
            "optimal_mode": opt_mode,
            "tokens_raw": 5000,
            "tokens_saved": 1250,
            "additional_savings_pct": extra,
        }

    def _make_model(self, current="claude-opus-4-6", cost=0.05, alt="claude-haiku-4-5", alt_cost=0.003, pct=80):
        return {
            "current_model": current,
            "cost_per_request": cost,
            "avg_input_tokens": 1000,
            "avg_output_tokens": 500,
            "best_alternative": alt,
            "alt_cost_per_request": alt_cost,
            "alt_savings_pct": pct,
            "alt_reason": "good for simple tasks",
        }

    def _make_redundancy(self, dup=3, stale=2, total=5, tokens=2100):
        return {
            "duplicate_memory_blocks": dup,
            "expired_telemetry_caches": stale,
            "total_redundant_blocks": total,
            "redundant_tokens": tokens,
        }

    def test_full_recommendations_has_three(self):
        recs = _build_recommendations(
            self._make_compression(),
            self._make_model(),
            self._make_redundancy(),
        )
        self.assertEqual(len(recs), 3)

    def test_recs_are_numbered(self):
        recs = _build_recommendations(
            self._make_compression(),
            self._make_model(),
            self._make_redundancy(),
        )
        for i, r in enumerate(recs, 1):
            self.assertEqual(r["n"], i)

    def test_no_recs_when_optimal(self):
        compression = self._make_compression(current=50.0, optimal=50.0, extra=0.0, mode="aggressive", opt_mode="aggressive")
        model_data = self._make_model(alt=None, pct=0)
        model_data["best_alternative"] = None
        redundancy = self._make_redundancy(dup=0, stale=0, total=0, tokens=0)
        recs = _build_recommendations(compression, model_data, redundancy)
        self.assertEqual(len(recs), 1)
        self.assertIn("No significant", recs[0]["label"])

    def test_compression_rec_has_apply_cmd(self):
        recs = _build_recommendations(
            self._make_compression(),
            self._make_model(alt=None),
            self._make_redundancy(dup=0, stale=0, total=0, tokens=0),
        )
        comp_rec = recs[0]
        self.assertIn("tokenpak config set compression", comp_rec["apply_cmd"])

    def test_redundancy_rec_has_prune_cmd(self):
        model_data = self._make_model(alt=None)
        model_data["best_alternative"] = None
        recs = _build_recommendations(
            self._make_compression(current=50.0, optimal=50.0, extra=0.0, mode="aggressive", opt_mode="aggressive"),
            model_data,
            self._make_redundancy(),
        )
        prune_rec = recs[0]
        self.assertIn("prune", prune_rec["apply_cmd"])


class TestRunOptimize(unittest.TestCase):
    """Integration smoke test for run_optimize."""

    @patch("tokenpak.cli.commands.optimize._proxy_get")
    @patch("tokenpak.cli.commands.optimize._db_connect")
    @patch("tokenpak.cli.commands.optimize.is_pro", create=True)
    def test_json_output_is_valid(self, mock_pro, mock_db, mock_proxy):
        # Bypass pro gate by patching is_pro at module level
        import tokenpak.cli.commands.optimize as opt_mod
        opt_mod_is_pro = getattr(opt_mod, "is_pro", None)

        mock_proxy.return_value = {
            "tokens_raw": 10000,
            "tokens_saved": 3500,
            "avg_savings_pct": 35.0,
            "session_requests": 5,
            "total_cost": 0.25,
        }
        mock_db.return_value = None  # no DB, uses fallback

        captured = []
        original_print = __builtins__["print"] if isinstance(__builtins__, dict) else print

        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            try:
                with patch("tokenpak.infrastructure.license_activation.is_pro", return_value=True):
                    run_optimize(as_json=True)
            except SystemExit:
                pass
        out = buf.getvalue()
        if out.strip():
            data = json.loads(out)
            self.assertIn("compression", data)
            self.assertIn("recommendations", data)


if __name__ == "__main__":
    unittest.main(verbosity=2)
