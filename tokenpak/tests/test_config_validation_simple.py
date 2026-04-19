"""Tests for config validation — focused integration tests.

Covers: config_validator.py — configuration validation, error reporting.
"""

import pytest

from tokenpak.config_validator import ConfigValidationError, ConfigValidator


class TestBasicConfigValidation:
    """Test: Basic configuration validation."""

    def test_empty_config_fails(self):
        """Empty config fails (missing api_keys)."""
        validator = ConfigValidator()
        errors = validator.validate({})
        assert len(errors) > 0

    def test_valid_config_passes(self):
        """Config with api_keys passes."""
        validator = ConfigValidator()
        config = {"api_keys": {"anthropic": "sk-test"}}
        errors = validator.validate(config)
        assert len(errors) == 0

    def test_multiple_providers_valid(self):
        """Multiple providers are accepted."""
        validator = ConfigValidator()
        config = {
            "api_keys": {
                "anthropic": "sk-test1",
                "openai": "sk-test2"
            }
        }
        errors = validator.validate(config)
        assert len(errors) == 0


class TestErrorReporting:
    """Test: Error reporting and suggestions."""

    def test_errors_have_suggestions(self):
        """All errors include actionable suggestions."""
        validator = ConfigValidator()
        errors = validator.validate({})
        for error in errors:
            assert error.suggestion is not None
            assert len(error.suggestion) > 0

    def test_errors_have_messages(self):
        """All errors have clear messages."""
        validator = ConfigValidator()
        errors = validator.validate({})
        for error in errors:
            assert error.message is not None
            assert len(error.message) > 0

    def test_error_serialization(self):
        """Errors can be serialized."""
        validator = ConfigValidator()
        errors = validator.validate({})
        for error in errors:
            error_dict = error.to_dict()
            assert "field" in error_dict
            assert "message" in error_dict
            assert "suggestion" in error_dict


class TestPortValidation:
    """Test: Port range validation."""

    def test_valid_port(self):
        """Valid port (1024-65535) passes."""
        validator = ConfigValidator()
        config = {"api_keys": {"anthropic": "key"}, "port": 8766}
        errors = validator.validate(config)
        port_errors = [e for e in errors if "port" in str(e.field).lower()]
        assert len(port_errors) == 0

    def test_port_out_of_range(self):
        """Ports outside 1024-65535 are rejected."""
        validator = ConfigValidator()
        
        # Too low
        errors_low = validator.validate({
            "api_keys": {"anthropic": "key"},
            "port": 100
        })
        assert any("port" in str(e.field).lower() for e in errors_low)
        
        # Too high
        errors_high = validator.validate({
            "api_keys": {"anthropic": "key"},
            "port": 70000
        })
        assert any("port" in str(e.field).lower() for e in errors_high)


class TestCacheTTLValidation:
    """Test: Cache TTL (time-to-live) validation."""

    def test_valid_ttl(self):
        """Valid TTL passes."""
        validator = ConfigValidator()
        config = {"api_keys": {"anthropic": "key"}, "cache_ttl": 3600}
        errors = validator.validate(config)
        ttl_errors = [e for e in errors if "ttl" in str(e.field).lower()]
        assert len(ttl_errors) == 0

    def test_ttl_zero_or_negative_fails(self):
        """Zero or negative TTL is rejected."""
        validator = ConfigValidator()
        config = {"api_keys": {"anthropic": "key"}, "cache_ttl": 0}
        errors = validator.validate(config)
        assert any("ttl" in str(e.field).lower() for e in errors)


class TestRateLimitValidation:
    """Test: Rate limit validation."""

    def test_valid_rate_limit(self):
        """Valid rate limit passes."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"anthropic": "key"},
            "rate_limit_requests": 1000,
            "rate_limit_window": 60
        }
        errors = validator.validate(config)
        rate_errors = [e for e in errors if "rate_limit" in str(e.field).lower()]
        assert len(rate_errors) == 0

    def test_rate_limit_negative_fails(self):
        """Negative rate limit is rejected."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"anthropic": "key"},
            "rate_limit_requests": -100
        }
        errors = validator.validate(config)
        assert any("rate_limit" in str(e.field).lower() for e in errors)


class TestProviderURLValidation:
    """Test: Provider URL validation."""

    def test_valid_provider_urls(self):
        """Valid provider URLs pass."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"custom": "key"},
            "provider_urls": {
                "custom": "https://api.example.com"
            }
        }
        errors = validator.validate(config)
        url_errors = [e for e in errors if "url" in str(e.field).lower()]
        assert len(url_errors) == 0

    def test_invalid_provider_urls_fail(self):
        """Invalid URLs are rejected."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"custom": "key"},
            "provider_urls": {
                "custom": "not-a-url"
            }
        }
        errors = validator.validate(config)
        assert any("url" in str(e.field).lower() for e in errors)


class TestTypeValidation:
    """Test: Field type validation."""

    def test_port_wrong_type(self):
        """Port must be integer."""
        validator = ConfigValidator()
        config = {"api_keys": {"anthropic": "key"}, "port": "8766"}
        errors = validator.validate(config)
        assert any("port" in str(e.field).lower() for e in errors)

    def test_ttl_wrong_type(self):
        """Cache TTL must be integer."""
        validator = ConfigValidator()
        config = {"api_keys": {"anthropic": "key"}, "cache_ttl": "3600"}
        errors = validator.validate(config)
        assert any("ttl" in str(e.field).lower() for e in errors)


class TestMultipleErrors:
    """Test: Multiple validation errors reported."""

    def test_multiple_errors_all_reported(self):
        """Multiple errors are all reported."""
        validator = ConfigValidator()
        config = {
            # Missing api_keys
            "port": 99,  # Out of range
            "cache_ttl": -1,  # Negative
        }
        errors = validator.validate(config)
        # Should have at least 3 errors
        assert len(errors) >= 2
