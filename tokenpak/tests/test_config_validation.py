"""
Tests for tokenpak.config_validator.ConfigValidator

Validates config dict fields on proxy startup:
- Required fields (api_keys)
- Field types (port=int, cache_ttl=int, etc.)
- Value ranges (port 1024-65535, TTL > 0)
- Path existence (log_dir, cache_dir)
- URL formats (provider_urls)
"""

import os
import tempfile

from tokenpak.config_validator import ConfigValidationError, ConfigValidator


class TestConfigValidationError:
    """Tests for ConfigValidationError class."""

    def test_error_has_all_fields(self):
        """Error object contains all required fields."""
        err = ConfigValidationError(
            field="port",
            expected="1024-65535",
            actual=99,
            message="Port out of range",
            suggestion="Change port to 8766",
        )
        assert err.field == "port"
        assert err.expected == "1024-65535"
        assert err.actual == 99
        assert err.message == "Port out of range"
        assert err.suggestion == "Change port to 8766"

    def test_error_string_format(self):
        """Error renders nicely as string."""
        err = ConfigValidationError(
            field="port",
            expected="1024-65535",
            actual=99,
            message="Port out of range",
            suggestion="Change port to 8766",
        )
        s = str(err)
        assert "port" in s.lower()
        assert "1024-65535" in s
        assert "99" in s

    def test_error_to_dict(self):
        """Error converts to dict."""
        err = ConfigValidationError(
            field="port",
            expected="1024-65535",
            actual=99,
            message="Port out of range",
            suggestion="Change port to 8766",
        )
        d = err.to_dict()
        assert d["field"] == "port"
        assert d["expected"] == "1024-65535"
        assert d["actual"] == 99


class TestConfigValidatorRequired:
    """Tests for required field validation."""

    def test_valid_minimal_config(self):
        """Minimal config with just api_keys is valid."""
        config = {"api_keys": {"anthropic": "sk-test"}}
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 0

    def test_empty_config_is_valid(self):
        """Empty config is valid — credentials may be supplied via env-pool,
        user-config, claude-cli, codex-cli, or openclaw instead of api_keys.
        See project_tokenpak_creds_architecture.md."""
        config = {}
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert errors == []

    def test_error_has_suggestion(self):
        """When there IS a shape error, it includes a fix suggestion."""
        config = {"api_keys": "not-a-dict"}
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) > 0
        for err in errors:
            assert err.suggestion
            assert len(err.suggestion) > 0


class TestConfigValidatorTypes:
    """Tests for field type validation."""

    def test_api_keys_must_be_dict(self):
        """api_keys must be dict, not string."""
        config = {"api_keys": "sk-test"}
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 1
        assert errors[0].field == "api_keys"
        assert "dict" in errors[0].expected.lower()

    def test_port_must_be_int(self):
        """port must be int, not string."""
        config = {"api_keys": {"anthropic": "sk-test"}, "port": "8766"}
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 1
        assert errors[0].field == "port"
        assert "integer" in errors[0].expected.lower()

    def test_cache_ttl_must_be_int(self):
        """cache_ttl must be int, not string."""
        config = {"api_keys": {"anthropic": "sk-test"}, "cache_ttl": "3600"}
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 1
        assert errors[0].field == "cache_ttl"
        assert "integer" in errors[0].expected.lower()

    def test_rate_limit_requests_must_be_int(self):
        """rate_limit_requests must be int."""
        config = {"api_keys": {"anthropic": "sk-test"}, "rate_limit_requests": "100"}
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 1
        assert errors[0].field == "rate_limit_requests"

    def test_rate_limit_window_must_be_int(self):
        """rate_limit_window must be int."""
        config = {"api_keys": {"anthropic": "sk-test"}, "rate_limit_window": "60"}
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 1
        assert errors[0].field == "rate_limit_window"


class TestConfigValidatorRanges:
    """Tests for value range validation."""

    def test_port_minimum_range(self):
        """port < 1024 produces error."""
        config = {"api_keys": {"anthropic": "sk-test"}, "port": 100}
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 1
        assert errors[0].field == "port"
        assert "1024" in errors[0].expected

    def test_port_maximum_range(self):
        """port > 65535 produces error."""
        config = {"api_keys": {"anthropic": "sk-test"}, "port": 99999}
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 1
        assert errors[0].field == "port"
        assert "65535" in errors[0].expected

    def test_port_valid_range(self):
        """port in range 1024-65535 is valid."""
        for port in [1024, 8766, 65535]:
            config = {"api_keys": {"anthropic": "sk-test"}, "port": port}
            validator = ConfigValidator()
            errors = validator.validate(config)
            assert len(errors) == 0

    def test_cache_ttl_must_be_positive(self):
        """cache_ttl <= 0 produces error."""
        for ttl in [0, -1, -3600]:
            config = {"api_keys": {"anthropic": "sk-test"}, "cache_ttl": ttl}
            validator = ConfigValidator()
            errors = validator.validate(config)
            assert len(errors) == 1
            assert errors[0].field == "cache_ttl"
            assert "positive" in errors[0].expected.lower()

    def test_cache_ttl_valid_positive(self):
        """cache_ttl > 0 is valid."""
        config = {"api_keys": {"anthropic": "sk-test"}, "cache_ttl": 3600}
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 0

    def test_rate_limit_requests_must_be_positive(self):
        """rate_limit_requests <= 0 produces error."""
        config = {"api_keys": {"anthropic": "sk-test"}, "rate_limit_requests": 0}
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 1
        assert errors[0].field == "rate_limit_requests"

    def test_rate_limit_window_must_be_positive(self):
        """rate_limit_window <= 0 produces error."""
        config = {"api_keys": {"anthropic": "sk-test"}, "rate_limit_window": -60}
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 1
        assert errors[0].field == "rate_limit_window"


class TestConfigValidatorURLs:
    """Tests for provider URL validation."""

    def test_valid_provider_urls(self):
        """Valid URLs are accepted."""
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "provider_urls": {
                "anthropic": "https://api.anthropic.com",
                "openai": "https://api.openai.com",
            },
        }
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 0

    def test_invalid_provider_url_no_scheme(self):
        """URL without scheme is invalid."""
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "provider_urls": {
                "anthropic": "api.anthropic.com"  # missing https://
            },
        }
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 1
        assert "provider_urls.anthropic" in errors[0].field

    def test_invalid_provider_url_no_host(self):
        """URL without host is invalid."""
        config = {"api_keys": {"anthropic": "sk-test"}, "provider_urls": {"anthropic": "https://"}}
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 1

    def test_invalid_provider_url_plaintext(self):
        """Plaintext URL is invalid."""
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "provider_urls": {"anthropic": "not a url at all"},
        }
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 1


class TestConfigValidatorPaths:
    """Tests for file path validation."""

    def test_valid_log_dir_exists(self):
        """Existing log_dir is valid."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"api_keys": {"anthropic": "sk-test"}, "log_dir": tmpdir}
            validator = ConfigValidator()
            errors = validator.validate(config)
            assert len(errors) == 0

    def test_missing_log_dir(self):
        """Non-existent log_dir produces error."""
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "log_dir": "/nonexistent/path/that/does/not/exist/12345",
        }
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 1
        assert errors[0].field == "log_dir"
        assert "does not exist" in errors[0].message

    def test_valid_cache_dir_exists(self):
        """Existing cache_dir is valid."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {"api_keys": {"anthropic": "sk-test"}, "cache_dir": tmpdir}
            validator = ConfigValidator()
            errors = validator.validate(config)
            assert len(errors) == 0

    def test_missing_cache_dir(self):
        """Non-existent cache_dir produces error."""
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "cache_dir": "/nonexistent/cache/path/54321",
        }
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 1
        assert errors[0].field == "cache_dir"

    def test_both_dirs_missing(self):
        """Multiple missing paths produce multiple errors."""
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "log_dir": "/nonexistent/logs",
            "cache_dir": "/nonexistent/cache",
        }
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 2
        fields = [e.field for e in errors]
        assert "log_dir" in fields
        assert "cache_dir" in fields


class TestConfigValidatorMultipleErrors:
    """Tests for reporting multiple errors at once."""

    def test_multiple_type_errors(self):
        """Multiple type errors all reported."""
        config = {
            "api_keys": "not a dict",  # wrong type
            "port": "not an int",  # wrong type
            "cache_ttl": "3600",  # wrong type
        }
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 3
        fields = [e.field for e in errors]
        assert "api_keys" in fields
        assert "port" in fields
        assert "cache_ttl" in fields

    def test_type_and_range_errors(self):
        """Type errors don't prevent range checks."""
        config = {
            "api_keys": {"anthropic": "sk-test"},
            "port": 100,  # out of range (but correct type)
        }
        validator = ConfigValidator()
        errors = validator.validate(config)
        assert len(errors) == 1
        assert errors[0].field == "port"
        assert "1024-65535" in errors[0].expected

    def test_full_error_collection(self):
        """All shape/range validation categories produce errors together."""
        with tempfile.TemporaryDirectory() as tmpdir:
            config = {
                "api_keys": "not-a-dict",  # wrong type
                "port": 100,  # out of range
                "cache_ttl": -1,  # out of range
                "log_dir": "/nonexistent/path",  # path missing
                "provider_urls": {
                    "test": "not a url"  # invalid URL
                },
            }
            validator = ConfigValidator()
            errors = validator.validate(config)
            assert len(errors) >= 4
            fields = [e.field for e in errors]
            assert "api_keys" in fields
            assert "port" in fields
            assert "cache_ttl" in fields
            assert "log_dir" in fields


class TestConfigValidatorIsValid:
    """Tests for is_valid() convenience method."""

    def test_is_valid_returns_true_for_valid_config(self):
        """is_valid() returns True when no errors."""
        config = {"api_keys": {"anthropic": "sk-test"}}
        validator = ConfigValidator()
        assert validator.is_valid(config) is True

    def test_is_valid_returns_false_for_invalid_config(self):
        """is_valid() returns False when errors exist."""
        config = {"api_keys": "not a dict"}
        validator = ConfigValidator()
        assert validator.is_valid(config) is False


class TestConfigValidatorValidateFile:
    """Tests for validate_file() method."""

    def test_validate_file_reads_json(self):
        """validate_file loads and validates JSON file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({"api_keys": {"anthropic": "sk-test"}}, f)
            f.flush()

            try:
                validator = ConfigValidator()
                result = validator.validate_file(f.name)
                assert result is True
            finally:
                os.unlink(f.name)

    def test_validate_file_missing_file(self):
        """validate_file returns False for missing file."""
        validator = ConfigValidator()
        result = validator.validate_file("/nonexistent/config.json")
        assert result is False

    def test_validate_file_invalid_json(self):
        """validate_file returns False for invalid JSON."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid json}")
            f.flush()

            try:
                validator = ConfigValidator()
                result = validator.validate_file(f.name)
                assert result is False
            finally:
                os.unlink(f.name)

    def test_validate_file_invalid_config(self):
        """validate_file returns False for invalid config."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            import json

            json.dump({"api_keys": "not a dict"}, f)
            f.flush()

            try:
                validator = ConfigValidator()
                result = validator.validate_file(f.name)
                assert result is False
            finally:
                os.unlink(f.name)
