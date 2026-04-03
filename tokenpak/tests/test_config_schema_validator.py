"""
Tests for tokenpak.config_schema_validator public API.

Public API under test:
    validate_config_dict(config) -> (is_valid: bool, errors: list[dict])
    validate_config_file(filepath) -> (is_valid: bool, errors: list[dict])
    format_errors(errors, filepath=None) -> str

Error dicts have keys: path, message, suggestion, validator, instance
"""
import json
import os
import tempfile

import pytest

from tokenpak.config_schema_validator import (
    format_errors,
    validate_config_dict,
    validate_config_file,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

MINIMAL_VALID = {"port": 8766}

FULL_VALID = {
    "port": 8766,
    "mode": "strict",
    "compression": {
        "threshold_tokens": 4500,
        "cache_size": 2000,
        "max_chars": 120,
    },
    "budget": {"total_tokens": 12000},
    "vault": {"inject_min_score": 0.5, "retrieval_backend": "sqlite"},
    "rate_limit_rpm": 60,
    "upstream": {"timeout": 300},
    "features": {"router": True, "skeleton": False},
    "failover": {
        "chain": [{"provider": "anthropic"}, {"provider": "openai"}]
    },
}


def paths(errors):
    return [e["path"] for e in errors]


def validators(errors):
    return [e["validator"] for e in errors]


# ---------------------------------------------------------------------------
# validate_config_dict — valid configs
# ---------------------------------------------------------------------------


class TestValidConfigDict:
    def test_minimal_valid_passes(self):
        ok, errors = validate_config_dict(MINIMAL_VALID)
        assert ok is True
        assert errors == []

    def test_full_valid_passes(self):
        ok, errors = validate_config_dict(FULL_VALID)
        assert ok is True
        assert errors == []

    def test_empty_dict_passes(self):
        # No required fields in schema validator
        ok, errors = validate_config_dict({})
        assert ok is True
        assert errors == []

    def test_returns_tuple_of_two(self):
        result = validate_config_dict(MINIMAL_VALID)
        assert isinstance(result, tuple)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# validate_config_dict — port
# ---------------------------------------------------------------------------


class TestPortValidation:
    def test_port_string_is_type_error(self):
        ok, errors = validate_config_dict({"port": "8766"})
        assert not ok
        assert "port" in paths(errors)
        assert "type" in validators(errors)

    def test_port_bool_is_type_error(self):
        ok, errors = validate_config_dict({"port": True})
        assert not ok
        assert "port" in paths(errors)

    def test_port_too_low_is_range_error(self):
        ok, errors = validate_config_dict({"port": 80})
        assert not ok
        err = next(e for e in errors if e["path"] == "port")
        assert err["validator"] == "range"

    def test_port_too_high_is_range_error(self):
        ok, errors = validate_config_dict({"port": 70000})
        assert not ok
        assert "port" in paths(errors)

    def test_port_1024_is_valid(self):
        ok, errors = validate_config_dict({"port": 1024})
        assert ok

    def test_port_65535_is_valid(self):
        ok, errors = validate_config_dict({"port": 65535})
        assert ok


# ---------------------------------------------------------------------------
# validate_config_dict — mode
# ---------------------------------------------------------------------------


class TestModeValidation:
    def test_valid_modes(self):
        for mode in ("strict", "hybrid", "aggressive"):
            ok, errors = validate_config_dict({"mode": mode})
            assert ok, f"mode {mode!r} should be valid"

    def test_invalid_mode_is_enum_error(self):
        ok, errors = validate_config_dict({"mode": "turbo"})
        assert not ok
        err = next(e for e in errors if e["path"] == "mode")
        assert err["validator"] == "enum"
        assert err["instance"] == "turbo"


# ---------------------------------------------------------------------------
# validate_config_dict — compression
# ---------------------------------------------------------------------------


class TestCompressionValidation:
    def test_valid_compression_passes(self):
        ok, errors = validate_config_dict(
            {"compression": {"threshold_tokens": 4500, "cache_size": 100, "max_chars": 80}}
        )
        assert ok

    def test_negative_threshold_tokens_is_error(self):
        ok, errors = validate_config_dict({"compression": {"threshold_tokens": -1}})
        assert not ok
        assert "compression.threshold_tokens" in paths(errors)

    def test_cache_size_below_10_is_error(self):
        ok, errors = validate_config_dict({"compression": {"cache_size": 5}})
        assert not ok
        assert "compression.cache_size" in paths(errors)

    def test_cache_size_exactly_10_is_ok(self):
        ok, errors = validate_config_dict({"compression": {"cache_size": 10}})
        assert ok

    def test_max_chars_zero_is_error(self):
        ok, errors = validate_config_dict({"compression": {"max_chars": 0}})
        assert not ok
        assert "compression.max_chars" in paths(errors)


# ---------------------------------------------------------------------------
# validate_config_dict — vault
# ---------------------------------------------------------------------------


class TestVaultValidation:
    def test_inject_min_score_valid(self):
        ok, errors = validate_config_dict({"vault": {"inject_min_score": 5.0}})
        assert ok

    def test_inject_min_score_above_10_is_error(self):
        ok, errors = validate_config_dict({"vault": {"inject_min_score": 10.1}})
        assert not ok
        assert "vault.inject_min_score" in paths(errors)

    def test_inject_min_score_negative_is_error(self):
        ok, errors = validate_config_dict({"vault": {"inject_min_score": -0.1}})
        assert not ok

    def test_retrieval_backend_valid(self):
        for backend in ("json_blocks", "sqlite"):
            ok, errors = validate_config_dict({"vault": {"retrieval_backend": backend}})
            assert ok, f"backend {backend!r} should be valid"

    def test_retrieval_backend_invalid_is_enum_error(self):
        ok, errors = validate_config_dict({"vault": {"retrieval_backend": "redis"}})
        assert not ok
        err = next(e for e in errors if e["path"] == "vault.retrieval_backend")
        assert err["validator"] == "enum"


# ---------------------------------------------------------------------------
# validate_config_dict — other fields
# ---------------------------------------------------------------------------


class TestOtherFields:
    def test_rate_limit_rpm_zero_is_error(self):
        ok, errors = validate_config_dict({"rate_limit_rpm": 0})
        assert not ok
        assert "rate_limit_rpm" in paths(errors)

    def test_rate_limit_rpm_valid(self):
        ok, errors = validate_config_dict({"rate_limit_rpm": 60})
        assert ok

    def test_upstream_timeout_zero_is_error(self):
        ok, errors = validate_config_dict({"upstream": {"timeout": 0}})
        assert not ok
        assert "upstream.timeout" in paths(errors)

    def test_upstream_timeout_valid(self):
        ok, errors = validate_config_dict({"upstream": {"timeout": 300}})
        assert ok

    def test_unknown_top_level_key_is_error(self):
        ok, errors = validate_config_dict({"mystery_field": "value"})
        assert not ok
        err = next(e for e in errors if e["path"] == "mystery_field")
        assert err["validator"] == "unknown_field"

    def test_unknown_feature_flag_is_error(self):
        ok, errors = validate_config_dict({"features": {"unknown_flag": True}})
        assert not ok
        assert any("unknown_flag" in e["path"] for e in errors)

    def test_known_feature_flag_is_ok(self):
        ok, errors = validate_config_dict({"features": {"router": True}})
        assert ok

    def test_failover_unknown_provider_is_error(self):
        ok, errors = validate_config_dict(
            {"failover": {"chain": [{"provider": "fakeai"}]}}
        )
        assert not ok
        assert any("provider" in e["path"] for e in errors)

    def test_failover_valid_provider_is_ok(self):
        ok, errors = validate_config_dict(
            {"failover": {"chain": [{"provider": "anthropic"}, {"provider": "openai"}]}}
        )
        assert ok


# ---------------------------------------------------------------------------
# validate_config_dict — non-dict input
# ---------------------------------------------------------------------------


class TestNonDictInput:
    def test_list_input_is_error(self):
        ok, errors = validate_config_dict([])
        assert not ok
        assert any(e["validator"] == "type" for e in errors)

    def test_string_input_is_error(self):
        ok, errors = validate_config_dict("not a dict")
        assert not ok

    def test_none_input_is_error(self):
        ok, errors = validate_config_dict(None)
        assert not ok


# ---------------------------------------------------------------------------
# validate_config_file
# ---------------------------------------------------------------------------


class TestValidateConfigFile:
    def test_missing_file_returns_false(self):
        ok, errors = validate_config_file("/tmp/tokenpak_no_such_file_xyz.json")
        assert not ok
        assert len(errors) >= 1

    def test_invalid_json_returns_false(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{bad json}")
            fname = f.name
        try:
            ok, errors = validate_config_file(fname)
            assert not ok
        finally:
            os.unlink(fname)

    def test_valid_json_returns_true(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"port": 8766}, f)
            fname = f.name
        try:
            ok, errors = validate_config_file(fname)
            assert ok
        finally:
            os.unlink(fname)

    def test_invalid_config_returns_errors(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"port": 80}, f)  # port out of range
            fname = f.name
        try:
            ok, errors = validate_config_file(fname)
            assert not ok
            assert "port" in paths(errors)
        finally:
            os.unlink(fname)

    def test_not_a_file_path_returns_false(self):
        ok, errors = validate_config_file("/tmp")  # directory not file
        assert not ok


# ---------------------------------------------------------------------------
# format_errors
# ---------------------------------------------------------------------------


class TestFormatErrors:
    def test_empty_errors_returns_empty_string(self):
        assert format_errors([]) == ""

    def test_nonempty_errors_returns_string(self):
        _, errors = validate_config_dict({"port": 80})
        result = format_errors(errors)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_includes_error_message(self):
        _, errors = validate_config_dict({"port": 80})
        result = format_errors(errors)
        assert "port" in result

    def test_includes_filepath_when_provided(self):
        _, errors = validate_config_dict({"port": 80})
        result = format_errors(errors, filepath="/some/config.json")
        assert "/some/config.json" in result

    def test_suggestion_included(self):
        _, errors = validate_config_dict({"port": 80})
        result = format_errors(errors)
        # suggestion line should appear
        assert "Fix:" in result or "suggestion" in result.lower() or "8766" in result
