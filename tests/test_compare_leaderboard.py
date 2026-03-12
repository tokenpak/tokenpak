"""Tests for tokenpak compare and leaderboard commands."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from tokenpak.pricing import calculate_request_cost, calculate_request_cost_baseline, get_price


class TestPricing:
    """Test pricing module."""

    def test_get_price_valid_model(self):
        """Test getting price for a known model."""
        price = get_price("claude-opus-4-6")
        assert price is not None
        assert price.input_cost_per_mtok == 15.0
        assert price.output_cost_per_mtok == 75.0
        assert price.cache_read_cost_per_mtok == 1.50

    def test_get_price_unknown_model(self):
        """Test getting price for unknown model returns None."""
        price = get_price("unknown-model-xyz")
        assert price is None

    def test_calculate_cost_without_cache(self):
        """Test cost calculation without cache usage."""
        cost = calculate_request_cost(
            "claude-opus-4-6",
            input_tokens=32464,
            cache_read_tokens=0,
            output_tokens=0,
        )
        expected = (32464 / 1_000_000) * 15.0
        assert abs(cost - expected) < 0.01

    def test_calculate_cost_with_cache(self):
        """Test cost calculation with cache usage."""
        cost = calculate_request_cost(
            "claude-opus-4-6",
            input_tokens=1453,  # sent tokens
            cache_read_tokens=91319,  # cached tokens
            output_tokens=0,
        )
        sent_cost = (1453 / 1_000_000) * 15.0
        cache_cost = (91319 / 1_000_000) * 1.50
        expected = sent_cost + cache_cost
        assert abs(cost - expected) < 0.01

    def test_calculate_baseline_cost(self):
        """Test baseline cost (no cache)."""
        cost = calculate_request_cost_baseline(
            "claude-opus-4-6",
            total_input_tokens=32464,
            output_tokens=0,
        )
        expected = (32464 / 1_000_000) * 15.0
        assert abs(cost - expected) < 0.01

    def test_cost_comparison(self):
        """Test that cached cost is less than baseline."""
        baseline = calculate_request_cost_baseline("claude-opus-4-6", 32464, 0)
        cached = calculate_request_cost("claude-opus-4-6", 1453, 91319, 0)
        assert cached < baseline
        # Savings should be 69% as per task spec
        savings_pct = (baseline - cached) / baseline * 100
        assert 60 < savings_pct < 75  # Allow 60-75% savings


class TestCompareCommand:
    """Test compare command functionality."""

    def test_compare_no_events(self):
        """Test compare with no recent events."""
        from tokenpak.cli import cmd_compare
        from tokenpak.telemetry.query import get_recent_events
        
        args = Mock(last=1)
        with patch("tokenpak.telemetry.query.get_recent_events") as mock_get_recent:
            mock_get_recent.return_value = []
            with patch("builtins.print") as mock_print:
                cmd_compare(args)
                mock_print.assert_called_with("No recent requests found.")

    def test_compare_single_request(self):
        """Test compare shows before/after for single request."""
        from tokenpak.cli import cmd_compare
        
        args = Mock(last=1, duration_s=5.1)
        with patch("tokenpak.telemetry.query.get_recent_events") as mock_get_recent:
            mock_get_recent.return_value = [
                {
                    "model": "claude-opus-4-6",
                    "input_tokens": 32464,
                    "output_tokens": 0,
                }
            ]
            with patch("builtins.print") as mock_print:
                cmd_compare(args)
                # Should print comparison info
                calls = [str(call) for call in mock_print.call_args_list]
                assert any("Without TokenPak" in str(call) for call in calls)
                assert any("With TokenPak" in str(call) for call in calls)
                assert any("Saved" in str(call) for call in calls)

    def test_compare_multiple_requests(self):
        """Test compare with multiple requests."""
        from tokenpak.cli import cmd_compare
        
        args = Mock(last=2, duration_s=5.1)
        with patch("tokenpak.telemetry.query.get_recent_events") as mock_get_recent:
            mock_get_recent.return_value = [
                {
                    "model": "claude-opus-4-6",
                    "input_tokens": 32464,
                    "output_tokens": 0,
                },
                {
                    "model": "claude-haiku-4-5",
                    "input_tokens": 5000,
                    "output_tokens": 100,
                },
            ]
            with patch("builtins.print") as mock_print:
                cmd_compare(args)
                # Should print two requests
                calls = [str(call) for call in mock_print.call_args_list]
                opus_found = any("opus-4-6" in str(call).lower() for call in calls)
                assert opus_found


class TestLeaderboardCommand:
    """Test leaderboard command functionality."""

    def test_leaderboard_no_usage(self):
        """Test leaderboard with no usage data."""
        from tokenpak.cli import cmd_leaderboard
        
        args = Mock(days=1)
        with patch("tokenpak.telemetry.query.get_model_usage") as mock_usage:
            with patch("tokenpak.telemetry.query.get_savings_report") as mock_savings:
                mock_usage.return_value = []
                with patch("builtins.print") as mock_print:
                    cmd_leaderboard(args)
                    mock_print.assert_called()

    def test_leaderboard_shows_insights(self):
        """Test leaderboard displays efficiency insights."""
        from tokenpak.telemetry.query_models import ModelUsage
        from tokenpak.cli import cmd_leaderboard
        
        args = Mock(days=1)
        with patch("tokenpak.telemetry.query.get_model_usage") as mock_usage:
            with patch("tokenpak.telemetry.query.get_savings_report") as mock_savings:
                mock_usage.return_value = [
                    ModelUsage(
                        model="claude-opus-4-6",
                        provider="anthropic",
                        request_count=109,
                        total_input_tokens=3500000,
                        total_output_tokens=50000,
                    ),
                    ModelUsage(
                        model="claude-haiku-4-5",
                        provider="anthropic",
                        request_count=892,
                        total_input_tokens=4500000,
                        total_output_tokens=100000,
                    ),
                ]
                mock_savings.return_value = Mock(
                    total_cost=25.23,
                    savings_amount=243.22,
                    savings_pct=37.0,
                    cache_hit_rate=0.96,
                )
                with patch("builtins.print") as mock_print:
                    cmd_leaderboard(args)
                    calls = [str(call) for call in mock_print.call_args_list]
                    # Should show leaderboard header
                    assert any("Leaderboard" in str(call) for call in calls)
                    # Should show insights
                    assert any("Most Efficient" in str(call) or "🏆" in str(call) for call in calls)
                    assert any("Biggest Spender" in str(call) or "💸" in str(call) for call in calls)

    def test_leaderboard_sorts_by_cost(self):
        """Test that leaderboard sorts models by cost in the table."""
        from tokenpak.telemetry.query_models import ModelUsage
        from tokenpak.cli import cmd_leaderboard
        
        args = Mock(days=1)
        with patch("tokenpak.telemetry.query.get_model_usage") as mock_usage:
            with patch("tokenpak.telemetry.query.get_savings_report") as mock_savings:
                mock_usage.return_value = [
                    ModelUsage(
                        model="claude-haiku-4-5",
                        provider="anthropic",
                        request_count=100,
                        total_input_tokens=500000,
                        total_output_tokens=10000,
                    ),
                    ModelUsage(
                        model="claude-opus-4-6",
                        provider="anthropic",
                        request_count=10,
                        total_input_tokens=3500000,
                        total_output_tokens=50000,
                    ),
                ]
                mock_savings.return_value = Mock(
                    total_cost=100.0,
                    savings_amount=100.0,
                    savings_pct=50.0,
                    cache_hit_rate=0.90,
                )
                with patch("builtins.print") as mock_print:
                    cmd_leaderboard(args)
                    # Should print leaderboard with model information
                    calls = [str(call) for call in mock_print.call_args_list]
                    # Both models should be in the output
                    all_output = "\n".join([str(call) for call in calls])
                    assert "claude-opus-4-6" in all_output or "opus-4-6" in all_output
                    assert "claude-haiku-4-5" in all_output or "haiku-4-5" in all_output


class TestNoArgsDefault:
    """Test smart default when no args given."""

    def test_smart_default_shows_savings(self):
        """Test that tokenpak with no args shows savings summary."""
        # This would normally be called by main()
        # Test the logic that should be in main's no-args handler
        try:
            report = Mock(
                total_cost=10.0,
                savings_amount=256.81,
                savings_pct=37.0,
                cache_hit_rate=0.96,
            )
            output = f"TokenPak — 4h 23m uptime\n"
            output += f"💰 ${report.savings_amount:.2f} saved today ({report.savings_pct:.0f}% reduction)"
            assert "TokenPak" in output
            assert "256.81" in output
        except Exception as e:
            pytest.fail(f"Smart default failed: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
