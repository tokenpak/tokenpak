"""
Test coverage for pricing.py and config_validator.py modules.

Tests:
- pricing.py: get_rates, estimate_savings, calculate_request_cost,
              calculate_request_cost_baseline, get_price, edge cases
- config_validator.py: ConfigValidationError, ConfigValidator.validate,
                       type/value/path validation, edge cases

Task: TPK-COV-PRICE-CFG-001
"""

import os
import sys
import tempfile
import json
import pytest

# Ensure tokenpak is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from tokenpak.pricing import (
    get_rates,
    estimate_savings,
    calculate_request_cost,
    calculate_request_cost_baseline,
    get_price,
    MODEL_RATES,
    DEFAULT_RATE,
    calculate_savings_breakdown,
)
from tokenpak.config_validator import (
    ConfigValidator,
    ConfigValidationError,
)


# =============================================================================
# pricing.py Tests
# =============================================================================

class TestGetRates:
    """Tests for get_rates() function."""

    def test_returns_correct_rates_for_known_model(self):
        """get_rates returns accurate rates for claude-opus-4-5."""
        rates = get_rates("claude-opus-4-5")
        assert rates["input"] == 15.0
        assert rates["cached"] == 1.50
        assert rates["output"] == 75.0

    def test_returns_correct_rates_for_gpt4o(self):
        """get_rates returns accurate rates for gpt-4o."""
        rates = get_rates("gpt-4o")
        assert rates["input"] == 2.50
        assert rates["cached"] == 1.25
        assert rates["output"] == 10.0

    def test_falls_back_to_default_for_unknown_model(self):
        """get_rates returns DEFAULT_RATE for unknown models."""
        rates = get_rates("unknown-model-xyz")
        assert rates == DEFAULT_RATE
        assert rates["input"] == 3.0
        assert rates["cached"] == 0.30
        assert rates["output"] == 15.0

    def test_falls_back_to_default_for_none(self):
        """get_rates returns DEFAULT_RATE when model is None."""
        rates = get_rates(None)
        assert rates == DEFAULT_RATE

    def test_falls_back_to_default_for_empty_string(self):
        """get_rates returns DEFAULT_RATE when model is empty string."""
        rates = get_rates("")
        assert rates == DEFAULT_RATE


class TestEstimateSavings:
    """Tests for estimate_savings() function."""

    def test_compression_savings_calculation(self):
        """estimate_savings correctly calculates compression savings."""
        stats = {
            "tokens_raw": 1_000_000,  # 1M tokens
            "tokens_saved": 200_000,   # 200K saved by compression
            "cache_read_tokens": 0,
            "model": "claude-sonnet-4-5",
        }
        result = estimate_savings(stats)
        # Compression savings: 200K tokens * $3.0/M = $0.60
        assert result["compression_tokens_saved"] == 200_000
        assert result["compression_cost_saved"] == 0.6

    def test_cache_savings_calculation(self):
        """estimate_savings correctly calculates cache hit savings."""
        stats = {
            "tokens_raw": 1_000_000,
            "tokens_saved": 0,  # no compression
            "cache_read_tokens": 500_000,  # 500K from cache
            "model": "claude-sonnet-4-5",
        }
        result = estimate_savings(stats)
        # Cache savings: 500K tokens * (3.0 - 0.30)/M = $1.35
        assert result["cache_tokens_saved"] == 500_000
        assert result["cache_cost_saved"] == 1.35

    def test_combined_savings_calculation(self):
        """estimate_savings correctly combines compression + cache savings."""
        stats = {
            "tokens_raw": 1_000_000,
            "tokens_saved": 100_000,       # 100K compression
            "cache_read_tokens": 200_000,  # 200K cache
            "model": "claude-sonnet-4-5",
        }
        result = estimate_savings(stats)
        assert result["total_tokens_saved"] == 300_000  # compression + cache

    def test_reduction_percent(self):
        """estimate_savings calculates reduction percentage correctly."""
        stats = {
            "tokens_raw": 1_000_000,
            "tokens_saved": 500_000,  # 50% compression
            "cache_read_tokens": 0,
            "model": "claude-sonnet-4-5",
        }
        result = estimate_savings(stats)
        # Without: 1M * $3/M = $3.00
        # With: 500K * $3/M = $1.50
        # Reduction: (1.50 / 3.00) * 100 = 50%
        assert result["reduction_percent"] == 50.0

    def test_model_override_parameter(self):
        """estimate_savings uses model parameter over stats['model']."""
        stats = {
            "tokens_raw": 1_000_000,
            "tokens_saved": 0,
            "cache_read_tokens": 0,
            "model": "claude-opus-4-5",  # $15/M
        }
        # Override with sonnet ($3/M)
        result = estimate_savings(stats, model="claude-sonnet-4-5")
        # Cost should be based on sonnet rates
        assert result["cost_without_tokenpak"] == 3.0  # 1M * $3/M

    def test_zero_tokens_edge_case(self):
        """estimate_savings handles zero tokens without division errors."""
        stats = {
            "tokens_raw": 0,
            "tokens_saved": 0,
            "cache_read_tokens": 0,
        }
        result = estimate_savings(stats)
        assert result["reduction_percent"] == 0.0
        assert result["total_cost_saved"] == 0.0
        assert result["cache_hit_rate"] == 0.0


class TestCalculateRequestCost:
    """Tests for calculate_request_cost() function."""

    def test_basic_cost_calculation(self):
        """calculate_request_cost computes basic input cost."""
        # 1M input tokens at $3/M = $3.00
        cost = calculate_request_cost("claude-sonnet-4-5", input_tokens=1_000_000)
        assert cost == 3.0

    def test_includes_output_cost(self):
        """calculate_request_cost includes output token cost."""
        # 1M input @ $3/M + 100K output @ $15/M = $3.00 + $1.50
        cost = calculate_request_cost(
            "claude-sonnet-4-5",
            input_tokens=1_000_000,
            output_tokens=100_000,
        )
        assert cost == 4.5

    def test_includes_cache_costs(self):
        """calculate_request_cost includes cache read at reduced rate."""
        # Cache read: 500K @ $0.30/M = $0.15
        cost = calculate_request_cost(
            "claude-sonnet-4-5",
            input_tokens=0,
            cache_read_tokens=500_000,
        )
        assert cost == 0.15

    def test_cache_creation_premium(self):
        """calculate_request_cost applies 1.25x premium to cache creation."""
        # Cache creation: 1M @ $3/M * 1.25 = $3.75
        cost = calculate_request_cost(
            "claude-sonnet-4-5",
            input_tokens=0,
            cache_creation_tokens=1_000_000,
        )
        assert cost == 3.75


class TestCalculateRequestCostBaseline:
    """Tests for calculate_request_cost_baseline() function."""

    def test_baseline_no_cache(self):
        """calculate_request_cost_baseline ignores cache rates."""
        # 1M input + 500K that would be cache = all at input rate
        cost = calculate_request_cost_baseline(
            "claude-sonnet-4-5",
            total_input_tokens=1_500_000,
        )
        assert cost == 4.5  # 1.5M * $3/M

    def test_baseline_with_output(self):
        """calculate_request_cost_baseline includes output cost."""
        cost = calculate_request_cost_baseline(
            "claude-sonnet-4-5",
            total_input_tokens=1_000_000,
            output_tokens=200_000,
        )
        # 1M input @ $3/M + 200K output @ $15/M = $3 + $3 = $6
        assert cost == 6.0


class TestGetPrice:
    """Tests for get_price() function."""

    def test_input_price(self):
        """get_price returns input rate by default."""
        price = get_price("claude-opus-4-5")
        assert price == 15.0  # Opus input rate

    def test_output_price(self):
        """get_price returns output rate when specified."""
        price = get_price("claude-opus-4-5", direction="output")
        assert price == 75.0

    def test_cached_price(self):
        """get_price returns cached rate when specified."""
        price = get_price("claude-opus-4-5", direction="cached")
        assert price == 1.50

    def test_unknown_model_defaults(self):
        """get_price returns default rates for unknown models."""
        price = get_price("unknown-model", direction="input")
        assert price == 3.0  # sonnet default


class TestCalculateSavingsBreakdown:
    """Tests for calculate_savings_breakdown() function."""

    def test_aggregates_savings(self):
        """calculate_savings_breakdown sums per-model savings."""
        per_model = [
            {"model": "sonnet", "saved": 1.50},
            {"model": "opus", "saved": 2.50},
        ]
        result = calculate_savings_breakdown(per_model)
        assert result["cache_optimization"] == 4.0
        assert result["total"] == 4.0

    def test_handles_empty_list(self):
        """calculate_savings_breakdown handles empty input."""
        result = calculate_savings_breakdown([])
        assert result["cache_optimization"] == 0.0
        assert result["total"] == 0.0


# =============================================================================
# config_validator.py Tests
# =============================================================================

class TestConfigValidationError:
    """Tests for ConfigValidationError class."""

    def test_to_dict_format(self):
        """ConfigValidationError.to_dict returns correct structure."""
        error = ConfigValidationError(
            field="port",
            expected="1024-65535",
            actual=80,
            message="Port out of range",
            suggestion="Use port 8766",
        )
        d = error.to_dict()
        assert d["field"] == "port"
        assert d["expected"] == "1024-65535"
        assert d["actual"] == 80
        assert d["message"] == "Port out of range"
        assert d["suggestion"] == "Use port 8766"

    def test_str_format(self):
        """ConfigValidationError.__str__ produces readable output."""
        error = ConfigValidationError(
            field="port",
            expected="1024-65535",
            actual=80,
            message="Port out of range",
            suggestion="Use port 8766",
        )
        s = str(error)
        assert "port" in s
        assert "80" in s
        assert "Port out of range" in s


class TestConfigValidatorRequiredFields:
    """Tests for required field validation."""

    def test_valid_config_returns_empty_errors(self):
        """ConfigValidator.validate returns empty list for valid config."""
        validator = ConfigValidator()
        config = {"api_keys": {"anthropic": "sk-test"}}
        errors = validator.validate(config)
        assert len(errors) == 0

    def test_missing_api_keys_returns_error(self):
        """ConfigValidator catches missing api_keys field."""
        validator = ConfigValidator()
        config = {}  # missing api_keys
        errors = validator.validate(config)
        assert len(errors) >= 1
        assert any(e.field == "api_keys" for e in errors)


class TestConfigValidatorTypes:
    """Tests for type validation."""

    def test_port_must_be_integer(self):
        """ConfigValidator catches non-integer port."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "port": "8766",  # string, should be int
        }
        errors = validator.validate(config)
        port_errors = [e for e in errors if e.field == "port"]
        assert len(port_errors) == 1
        assert "integer" in port_errors[0].message.lower()

    def test_api_keys_must_be_dict(self):
        """ConfigValidator catches non-dict api_keys."""
        validator = ConfigValidator()
        config = {"api_keys": ["key1", "key2"]}  # list, should be dict
        errors = validator.validate(config)
        api_errors = [e for e in errors if e.field == "api_keys"]
        assert len(api_errors) == 1

    def test_cache_ttl_must_be_integer(self):
        """ConfigValidator catches non-integer cache_ttl."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "cache_ttl": "3600",  # string
        }
        errors = validator.validate(config)
        ttl_errors = [e for e in errors if e.field == "cache_ttl"]
        assert len(ttl_errors) == 1


class TestConfigValidatorValues:
    """Tests for value range validation."""

    def test_port_range_low(self):
        """ConfigValidator catches port below 1024."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "port": 80,  # privileged port
        }
        errors = validator.validate(config)
        port_errors = [e for e in errors if e.field == "port"]
        assert len(port_errors) == 1
        assert "1024-65535" in port_errors[0].expected

    def test_port_range_high(self):
        """ConfigValidator catches port above 65535."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "port": 70000,
        }
        errors = validator.validate(config)
        port_errors = [e for e in errors if e.field == "port"]
        assert len(port_errors) == 1

    def test_negative_cache_ttl(self):
        """ConfigValidator catches negative cache_ttl."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "cache_ttl": -1,
        }
        errors = validator.validate(config)
        ttl_errors = [e for e in errors if e.field == "cache_ttl"]
        assert len(ttl_errors) == 1
        assert "positive" in ttl_errors[0].message.lower()

    def test_zero_rate_limit_requests(self):
        """ConfigValidator catches zero rate_limit_requests."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "rate_limit_requests": 0,
        }
        errors = validator.validate(config)
        rate_errors = [e for e in errors if e.field == "rate_limit_requests"]
        assert len(rate_errors) == 1


class TestConfigValidatorUrls:
    """Tests for URL validation."""

    def test_invalid_provider_url(self):
        """ConfigValidator catches invalid provider URLs."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "provider_urls": {
                "anthropic": "not-a-url",
            },
        }
        errors = validator.validate(config)
        url_errors = [e for e in errors if "provider_urls" in e.field]
        assert len(url_errors) == 1

    def test_valid_provider_url(self):
        """ConfigValidator accepts valid provider URLs."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "provider_urls": {
                "anthropic": "https://api.anthropic.com",
            },
        }
        errors = validator.validate(config)
        url_errors = [e for e in errors if "provider_urls" in e.field]
        assert len(url_errors) == 0


class TestConfigValidatorPaths:
    """Tests for path validation."""

    def test_nonexistent_log_dir(self):
        """ConfigValidator catches non-existent log_dir."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "log_dir": "/nonexistent/path/xyz123",
        }
        errors = validator.validate(config)
        path_errors = [e for e in errors if e.field == "log_dir"]
        assert len(path_errors) == 1

    def test_existing_log_dir_passes(self):
        """ConfigValidator accepts existing log_dir."""
        with tempfile.TemporaryDirectory() as tmpdir:
            validator = ConfigValidator()
            config = {
                "api_keys": {"anthropic": "sk-test"},
                "log_dir": tmpdir,
            }
            errors = validator.validate(config)
            path_errors = [e for e in errors if e.field == "log_dir"]
            assert len(path_errors) == 0


class TestConfigValidatorHelpers:
    """Tests for helper methods."""

    def test_is_valid_returns_true_for_valid(self):
        """ConfigValidator.is_valid returns True for valid config."""
        validator = ConfigValidator()
        config = {"api_keys": {"anthropic": "sk-test"}}
        assert validator.is_valid(config) is True

    def test_is_valid_returns_false_for_invalid(self):
        """ConfigValidator.is_valid returns False for invalid config."""
        validator = ConfigValidator()
        config = {}  # missing required field
        assert validator.is_valid(config) is False

    def test_validate_file_nonexistent(self):
        """ConfigValidator.validate_file returns False for missing file."""
        validator = ConfigValidator()
        result = validator.validate_file("/nonexistent/config.json")
        assert result is False

    def test_validate_file_invalid_json(self):
        """ConfigValidator.validate_file returns False for invalid JSON."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            f.write("not valid json {{{")
            tmpfile = f.name
        try:
            validator = ConfigValidator()
            result = validator.validate_file(tmpfile)
            assert result is False
        finally:
            os.unlink(tmpfile)

    def test_validate_file_valid(self):
        """ConfigValidator.validate_file returns True for valid config file."""
        config = {"api_keys": {"anthropic": "sk-test"}}
        with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
            json.dump(config, f)
            tmpfile = f.name
        try:
            validator = ConfigValidator()
            result = validator.validate_file(tmpfile)
            assert result is True
        finally:
            os.unlink(tmpfile)


class TestEdgeCases:
    """Edge case tests for both modules."""

    def test_pricing_very_large_token_count(self):
        """pricing handles very large token counts (billions)."""
        cost = calculate_request_cost(
            "claude-sonnet-4-5",
            input_tokens=10_000_000_000,  # 10 billion
        )
        # 10B * $3/M = $30,000
        assert cost == 30000.0

    def test_pricing_fractional_results_rounded(self):
        """pricing rounds results to avoid floating point artifacts."""
        cost = calculate_request_cost(
            "claude-sonnet-4-5",
            input_tokens=1,
        )
        # 1 token @ $3/M = $0.000003
        assert isinstance(cost, float)
        assert cost == 0.000003

    def test_validator_empty_api_keys_dict(self):
        """validator accepts empty api_keys dict (no providers configured yet)."""
        validator = ConfigValidator()
        config = {"api_keys": {}}  # empty but present
        errors = validator.validate(config)
        # Empty dict is valid type — no errors for api_keys field
        api_errors = [e for e in errors if e.field == "api_keys"]
        assert len(api_errors) == 0

    def test_validator_multiple_errors_accumulated(self):
        """validator accumulates multiple errors in single pass."""
        validator = ConfigValidator()
        config = {
            "api_keys": ["invalid"],  # wrong type
            "port": "string",         # wrong type
            "cache_ttl": -5,          # negative (type ok, value bad)
        }
        errors = validator.validate(config)
        # Should have errors for: api_keys type, port type
        assert len(errors) >= 2
