"""Tests for config_validator.py — proxy config validation on boot."""

import json
import os
import tempfile
from pathlib import Path

import pytest

from tokenpak.config_validator import ConfigValidator, ConfigValidationError


class TestConfigValidationError:
    """Test ConfigValidationError data class."""

    def test_error_creation(self):
        """Test creating a validation error."""
        error = ConfigValidationError(
            field="port",
            expected="1024-65535",
            actual=9999999,
            message="Port out of range",
            suggestion="Use port between 1024-65535",
        )
        assert error.field == "port"
        assert error.expected == "1024-65535"
        assert error.actual == 9999999

    def test_error_to_dict(self):
        """Test error serialization."""
        error = ConfigValidationError(
            field="api_keys", expected="dict", actual="string", message="Wrong type", suggestion="Fix it"
        )
        d = error.to_dict()
        assert d["field"] == "api_keys"
        assert d["expected"] == "dict"


class TestConfigValidatorBasic:
    """Test basic validator functionality."""

    def test_valid_minimal_config(self):
        """Test minimal valid config (api_keys only)."""
        validator = ConfigValidator()
        config = {"api_keys": {"anthropic": "sk-test"}}
        errors = validator.validate(config)
        assert len(errors) == 0
        assert validator.is_valid(config)

    def test_missing_required_field(self):
        """Test detection of missing required field."""
        validator = ConfigValidator()
        config = {"port": 8766}
        errors = validator.validate(config)
        assert len(errors) == 1
        assert errors[0].field == "api_keys"
        assert "missing" in errors[0].message.lower()

    def test_multiple_errors_reported_together(self):
        """Test that all errors are reported at once."""
        validator = ConfigValidator()
        config = {
            "api_keys": "not-a-dict",  # wrong type
            "port": 9999999,  # out of range
            "cache_ttl": -1,  # invalid value
        }
        errors = validator.validate(config)
        assert len(errors) >= 3  # At least 3 errors
        field_names = [e.field for e in errors]
        assert "api_keys" in field_names
        assert "port" in field_names
        assert "cache_ttl" in field_names


class TestConfigValidatorTypes:
    """Test type validation."""

    def test_api_keys_must_be_dict(self):
        """Test that api_keys must be a dict."""
        validator = ConfigValidator()
        config = {"api_keys": "sk-test"}  # string instead of dict
        errors = validator.validate(config)
        assert len(errors) == 1
        assert errors[0].field == "api_keys"
        assert "dict" in errors[0].expected.lower()

    def test_port_must_be_int(self):
        """Test that port must be an integer."""
        validator = ConfigValidator()
        config = {"api_keys": {"a": "k"}, "port": "8766"}  # string instead of int
        errors = validator.validate(config)
        port_errors = [e for e in errors if e.field == "port"]
        assert len(port_errors) >= 1
        assert "integer" in port_errors[0].expected.lower()

    def test_cache_ttl_must_be_int(self):
        """Test that cache_ttl must be an integer."""
        validator = ConfigValidator()
        config = {"api_keys": {"a": "k"}, "cache_ttl": "3600"}  # string instead of int
        errors = validator.validate(config)
        ttl_errors = [e for e in errors if e.field == "cache_ttl"]
        assert len(ttl_errors) >= 1

    def test_rate_limit_fields_must_be_int(self):
        """Test that rate limit fields must be integers."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"a": "k"},
            "rate_limit_requests": "100",  # string instead of int
            "rate_limit_window": "60",  # string instead of int
        }
        errors = validator.validate(config)
        rl_errors = [e for e in errors if "rate_limit" in e.field]
        assert len(rl_errors) >= 2


class TestConfigValidatorValues:
    """Test value range validation."""

    def test_port_range_validation(self):
        """Test that port must be in range 1024-65535."""
        validator = ConfigValidator()

        # Too low
        config = {"api_keys": {"a": "k"}, "port": 100}
        errors = validator.validate(config)
        port_errors = [e for e in errors if e.field == "port"]
        assert len(port_errors) >= 1

        # Too high
        config = {"api_keys": {"a": "k"}, "port": 99999}
        errors = validator.validate(config)
        port_errors = [e for e in errors if e.field == "port"]
        assert len(port_errors) >= 1

        # Valid
        config = {"api_keys": {"a": "k"}, "port": 8766}
        errors = validator.validate(config)
        port_errors = [e for e in errors if e.field == "port"]
        assert len(port_errors) == 0

    def test_cache_ttl_must_be_positive(self):
        """Test that cache_ttl must be positive."""
        validator = ConfigValidator()
        config = {"api_keys": {"a": "k"}, "cache_ttl": 0}
        errors = validator.validate(config)
        ttl_errors = [e for e in errors if e.field == "cache_ttl"]
        assert len(ttl_errors) >= 1
        assert "positive" in ttl_errors[0].message.lower()

    def test_rate_limit_values_must_be_positive(self):
        """Test that rate limit values must be positive."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"a": "k"},
            "rate_limit_requests": 0,
            "rate_limit_window": -1,
        }
        errors = validator.validate(config)
        rl_errors = [e for e in errors if "rate_limit" in e.field]
        assert len(rl_errors) >= 2


class TestConfigValidatorURLs:
    """Test provider URL validation."""

    def test_valid_provider_url(self):
        """Test that valid URLs pass validation."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"a": "k"},
            "provider_urls": {"anthropic": "https://api.anthropic.com"},
        }
        errors = validator.validate(config)
        url_errors = [e for e in errors if "provider_urls" in e.field]
        assert len(url_errors) == 0

    def test_invalid_provider_url(self):
        """Test that invalid URLs are rejected."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"a": "k"},
            "provider_urls": {"anthropic": "not-a-valid-url"},
        }
        errors = validator.validate(config)
        url_errors = [e for e in errors if "provider_urls" in e.field]
        assert len(url_errors) >= 1

    def test_multiple_provider_urls(self):
        """Test validation of multiple provider URLs."""
        validator = ConfigValidator()
        config = {
            "api_keys": {"a": "k"},
            "provider_urls": {
                "anthropic": "https://api.anthropic.com",
                "openai": "invalid",
                "together": "https://api.together.com",
            },
        }
        errors = validator.validate(config)
        url_errors = [e for e in errors if "provider_urls" in e.field]
        assert len(url_errors) >= 1
        assert "openai" in url_errors[0].field


class TestConfigValidatorPaths:
    """Test file path validation."""

    def test_existing_log_dir_passes(self):
        """Test that existing log directory passes validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            validator = ConfigValidator()
            config = {"api_keys": {"a": "k"}, "log_dir": tmpdir}
            errors = validator.validate(config)
            log_errors = [e for e in errors if e.field == "log_dir"]
            assert len(log_errors) == 0

    def test_missing_log_dir_fails(self):
        """Test that missing log directory is detected."""
        validator = ConfigValidator()
        config = {"api_keys": {"a": "k"}, "log_dir": "/nonexistent/path/12345"}
        errors = validator.validate(config)
        log_errors = [e for e in errors if e.field == "log_dir"]
        assert len(log_errors) >= 1
        assert "does not exist" in log_errors[0].message.lower()

    def test_existing_cache_dir_passes(self):
        """Test that existing cache directory passes validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            validator = ConfigValidator()
            config = {"api_keys": {"a": "k"}, "cache_dir": tmpdir}
            errors = validator.validate(config)
            cache_errors = [e for e in errors if e.field == "cache_dir"]
            assert len(cache_errors) == 0

    def test_missing_cache_dir_fails(self):
        """Test that missing cache directory is detected."""
        validator = ConfigValidator()
        config = {"api_keys": {"a": "k"}, "cache_dir": "/nonexistent/cache/12345"}
        errors = validator.validate(config)
        cache_errors = [e for e in errors if e.field == "cache_dir"]
        assert len(cache_errors) >= 1


class TestConfigValidatorFileHandling:
    """Test config file validation."""

    def test_valid_json_file_passes(self):
        """Test that valid JSON config file passes validation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            config_file.write_text(json.dumps({"api_keys": {"anthropic": "sk-test"}}))
            validator = ConfigValidator()
            assert validator.validate_file(str(config_file)) is True

    def test_missing_file_fails(self):
        """Test that missing config file is detected."""
        validator = ConfigValidator()
        assert validator.validate_file("/nonexistent/config.json") is False

    def test_invalid_json_fails(self):
        """Test that invalid JSON is detected."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            config_file.write_text("{invalid json")
            validator = ConfigValidator()
            assert validator.validate_file(str(config_file)) is False

    def test_file_with_validation_errors_fails(self):
        """Test that file with validation errors fails."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config_file = Path(tmpdir) / "config.json"
            config_file.write_text(json.dumps({"api_keys": "not-a-dict"}))
            validator = ConfigValidator()
            assert validator.validate_file(str(config_file)) is False


class TestConfigValidatorSuggestions:
    """Test that error suggestions are helpful."""

    def test_error_suggestions_present(self):
        """Test that all errors have fix suggestions."""
        validator = ConfigValidator()
        config = {
            "api_keys": "wrong",
            "port": 999999,
            "cache_ttl": -1,
        }
        errors = validator.validate(config)
        for error in errors:
            assert error.suggestion is not None
            assert len(error.suggestion) > 0
            assert "Fix:" in str(error) or error.suggestion in str(error)

    def test_suggestion_is_actionable(self):
        """Test that suggestions are actionable."""
        validator = ConfigValidator()
        config = {"api_keys": {"a": "k"}, "port": 100}
        errors = validator.validate(config)
        port_errors = [e for e in errors if e.field == "port"]
        assert len(port_errors) > 0
        assert "1024-65535" in port_errors[0].suggestion
