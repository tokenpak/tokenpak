"""
Tests for tokenpak.core.config_validator.ConfigValidator and ConfigValidationError.

Public API under test:
    ConfigValidator.validate(config) -> List[ConfigValidationError]
    ConfigValidator.is_valid(config) -> bool
    ConfigValidator.validate_file(filepath) -> bool
    ConfigValidationError.to_dict() / __str__()
"""
import json
import os
import tempfile

import pytest

from tokenpak.core.config_validator import ConfigValidationError, ConfigValidator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_VALID = {"api_keys": {"anthropic": "sk-test"}}


def make_validator():
    return ConfigValidator()


def field_names(errors):
    return [e.field for e in errors]


# ---------------------------------------------------------------------------
# ConfigValidationError
# ---------------------------------------------------------------------------


class TestConfigValidationError:
    def test_all_attributes_stored(self):
        err = ConfigValidationError(
            field="port",
            expected="1024-65535",
            actual=80,
            message="Port out of range",
            suggestion="Use port 8766",
        )
        assert err.field == "port"
        assert err.expected == "1024-65535"
        assert err.actual == 80
        assert "out of range" in err.message
        assert "8766" in err.suggestion

    def test_str_contains_key_fields(self):
        err = ConfigValidationError("f", "x", "y", "msg", "fix")
        s = str(err)
        assert "f" in s
        assert "msg" in s
        assert "fix" in s

    def test_to_dict_has_all_keys(self):
        err = ConfigValidationError("f", "x", "y", "msg", "fix")
        d = err.to_dict()
        for key in ("field", "expected", "actual", "message", "suggestion"):
            assert key in d

    def test_to_dict_values_match(self):
        err = ConfigValidationError("port", "int", 99, "bad port", "fix it")
        d = err.to_dict()
        assert d["field"] == "port"
        assert d["actual"] == 99


# ---------------------------------------------------------------------------
# validate() — required fields
# ---------------------------------------------------------------------------


class TestRequiredFields:
    def test_valid_config_no_errors(self):
        v = make_validator()
        errors = v.validate(MINIMAL_VALID)
        assert errors == []

    def test_missing_api_keys_returns_error(self):
        v = make_validator()
        errors = v.validate({})
        assert any(e.field == "api_keys" for e in errors)

    def test_missing_api_keys_suggestion_mentions_env(self):
        v = make_validator()
        errors = v.validate({})
        err = next(e for e in errors if e.field == "api_keys")
        assert "ANTHROPIC_API_KEY" in err.suggestion or "api_keys" in err.suggestion


# ---------------------------------------------------------------------------
# validate() — type checking
# ---------------------------------------------------------------------------


class TestTypeValidation:
    def test_port_string_is_error(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "port": "8766"})
        assert "port" in field_names(errors)

    def test_port_int_is_ok(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "port": 8766})
        assert "port" not in field_names(errors)

    def test_cache_ttl_string_is_error(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "cache_ttl": "3600"})
        assert "cache_ttl" in field_names(errors)

    def test_cache_ttl_int_is_ok(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "cache_ttl": 3600})
        assert "cache_ttl" not in field_names(errors)

    def test_api_keys_list_is_error(self):
        v = make_validator()
        errors = v.validate({"api_keys": ["sk-1", "sk-2"]})
        assert "api_keys" in field_names(errors)

    def test_rate_limit_requests_float_is_error(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "rate_limit_requests": 10.5})
        assert "rate_limit_requests" in field_names(errors)

    def test_rate_limit_window_float_is_error(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "rate_limit_window": 60.0})
        assert "rate_limit_window" in field_names(errors)


# ---------------------------------------------------------------------------
# validate() — value range checks
# ---------------------------------------------------------------------------


class TestValueValidation:
    def test_port_below_1024_is_error(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "port": 80})
        assert "port" in field_names(errors)

    def test_port_above_65535_is_error(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "port": 99999})
        assert "port" in field_names(errors)

    def test_port_boundary_1024_ok(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "port": 1024})
        assert "port" not in field_names(errors)

    def test_port_boundary_65535_ok(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "port": 65535})
        assert "port" not in field_names(errors)

    def test_cache_ttl_zero_is_error(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "cache_ttl": 0})
        assert "cache_ttl" in field_names(errors)

    def test_cache_ttl_negative_is_error(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "cache_ttl": -1})
        assert "cache_ttl" in field_names(errors)

    def test_rate_limit_requests_zero_is_error(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "rate_limit_requests": 0})
        assert "rate_limit_requests" in field_names(errors)

    def test_provider_url_invalid_is_error(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "provider_urls": {"anthropic": "not-a-url"}})
        assert any("provider_urls" in e.field for e in errors)

    def test_provider_url_valid_is_ok(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "provider_urls": {"anthropic": "https://api.anthropic.com"}})
        assert not any("provider_urls" in e.field for e in errors)


# ---------------------------------------------------------------------------
# validate() — path checks
# ---------------------------------------------------------------------------


class TestPathValidation:
    def test_nonexistent_log_dir_is_error(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "log_dir": "/does/not/exist/xyz"})
        assert "log_dir" in field_names(errors)

    def test_nonexistent_cache_dir_is_error(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "cache_dir": "/does/not/exist/xyz"})
        assert "cache_dir" in field_names(errors)

    def test_existing_log_dir_is_ok(self):
        with tempfile.TemporaryDirectory() as d:
            v = make_validator()
            errors = v.validate({"api_keys": {}, "log_dir": d})
            assert "log_dir" not in field_names(errors)

    def test_existing_cache_dir_is_ok(self):
        with tempfile.TemporaryDirectory() as d:
            v = make_validator()
            errors = v.validate({"api_keys": {}, "cache_dir": d})
            assert "cache_dir" not in field_names(errors)


# ---------------------------------------------------------------------------
# is_valid()
# ---------------------------------------------------------------------------


class TestIsValid:
    def test_returns_true_for_valid_config(self):
        v = make_validator()
        assert v.is_valid(MINIMAL_VALID) is True

    def test_returns_false_for_invalid_config(self):
        v = make_validator()
        assert v.is_valid({}) is False

    def test_returns_false_for_bad_port(self):
        v = make_validator()
        assert v.is_valid({"api_keys": {}, "port": 80}) is False


# ---------------------------------------------------------------------------
# validate_file()
# ---------------------------------------------------------------------------


class TestValidateFile:
    def test_missing_file_returns_false(self):
        v = make_validator()
        assert v.validate_file("/tmp/does_not_exist_tokenpak_xyz.json") is False

    def test_invalid_json_returns_false(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{not valid json}")
            fname = f.name
        try:
            v = make_validator()
            assert v.validate_file(fname) is False
        finally:
            os.unlink(fname)

    def test_valid_json_config_returns_true(self):
        config = {"api_keys": {"anthropic": "sk-test"}}
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config, f)
            fname = f.name
        try:
            v = make_validator()
            assert v.validate_file(fname) is True
        finally:
            os.unlink(fname)

    def test_invalid_config_file_returns_false(self):
        config = {"api_keys": {}, "port": 80}  # port out of range
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(config, f)
            fname = f.name
        try:
            v = make_validator()
            assert v.validate_file(fname) is False
        finally:
            os.unlink(fname)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_config_reports_missing_api_keys(self):
        v = make_validator()
        errors = v.validate({})
        assert len(errors) >= 1
        assert "api_keys" in field_names(errors)

    def test_all_optional_fields_absent_is_ok(self):
        v = make_validator()
        errors = v.validate({"api_keys": {"anthropic": "sk-x"}})
        assert errors == []

    def test_multiple_errors_accumulate(self):
        v = make_validator()
        errors = v.validate({"api_keys": {}, "port": 80, "cache_ttl": -1})
        assert len(errors) >= 2

    def test_second_call_resets_errors(self):
        v = make_validator()
        v.validate({})  # generates errors
        errors = v.validate(MINIMAL_VALID)
        assert errors == []
