"""
Tests for config schema validation (tokenpak/config_schema_validator.py).

Tests the JSON Schema-based validator with:
- Valid configs (full and minimal)
- Invalid configs (type mismatches, out-of-range values, unknown fields)
- Error message quality (human-friendly)
- File I/O (YAML and JSON)
"""

import json
import tempfile
from pathlib import Path

from tokenpak.config_schema_validator import (
    format_errors,
    validate_config_dict,
    validate_config_file,
)


class TestValidConfigDict:
    """Tests for valid configuration dictionaries."""

    def test_minimal_valid_config(self):
        """Empty config (no required fields) is technically valid."""
        config = {}
        is_valid, errors = validate_config_dict(config)
        assert is_valid
        assert len(errors) == 0

    def test_minimal_with_port(self):
        """Config with just port is valid."""
        config = {"port": 8766}
        is_valid, errors = validate_config_dict(config)
        assert is_valid
        assert len(errors) == 0

    def test_full_config_minimal_fields(self):
        """Config with top-level sections is valid."""
        config = {
            "port": 8766,
            "mode": "hybrid",
            "compression": {
                "enabled": True,
                "threshold_tokens": 4500,
            },
            "features": {
                "skeleton": True,
                "router": True,
            },
        }
        is_valid, errors = validate_config_dict(config)
        assert is_valid
        assert len(errors) == 0

    def test_all_top_level_fields(self):
        """Config with all supported top-level fields is valid."""
        config = {
            "port": 8766,
            "mode": "strict",
            "db": "~/.tokenpak/data/monitor.db",
            "compression": {"enabled": True},
            "features": {"skeleton": True},
            "budget": {"total_tokens": 12000},
            "capsule": {"min_chars": 400},
            "vault": {"index_path": "~/vault/.tokenpak"},
            "term_resolver": {"top_k": 3},
            "upstream": {"timeout": 300},
            "rate_limit_rpm": 60,
            "failover": {"enabled": False},
        }
        is_valid, errors = validate_config_dict(config)
        assert is_valid
        assert len(errors) == 0


class TestInvalidConfigDict:
    """Tests for invalid configuration dictionaries."""

    def test_port_out_of_range_low(self):
        """Port < 1024 is invalid."""
        config = {"port": 500}
        is_valid, errors = validate_config_dict(config)
        assert not is_valid
        assert len(errors) >= 1
        assert any("port" in e["path"].lower() for e in errors)
        error = [e for e in errors if "port" in e["path"].lower()][0]
        assert "1024" in str(error["message"])

    def test_port_out_of_range_high(self):
        """Port > 65535 is invalid."""
        config = {"port": 70000}
        is_valid, errors = validate_config_dict(config)
        assert not is_valid
        assert any("port" in e["path"].lower() for e in errors)

    def test_port_wrong_type(self):
        """Port must be integer, not string."""
        config = {"port": "8766"}
        is_valid, errors = validate_config_dict(config)
        assert not is_valid
        error = [e for e in errors if "port" in e["path"].lower()][0]
        assert "type" in error["validator"].lower() or "integer" in error["message"].lower()

    def test_mode_invalid_value(self):
        """Mode must be one of: strict, hybrid, aggressive."""
        config = {"mode": "turbo"}
        is_valid, errors = validate_config_dict(config)
        assert not is_valid
        error = [e for e in errors if "mode" in e["path"].lower()][0]
        assert "strict" in error["message"] or "enum" in error["validator"]

    def test_compression_threshold_negative(self):
        """compression.threshold_tokens must be positive."""
        config = {"compression": {"threshold_tokens": -100}}
        is_valid, errors = validate_config_dict(config)
        assert not is_valid

    def test_compression_cache_size_too_small(self):
        """compression.cache_size minimum is 10."""
        config = {"compression": {"cache_size": 5}}
        is_valid, errors = validate_config_dict(config)
        assert not is_valid

    def test_budget_total_tokens_wrong_type(self):
        """budget.total_tokens must be integer."""
        config = {"budget": {"total_tokens": "12000"}}
        is_valid, errors = validate_config_dict(config)
        assert not is_valid

    def test_vault_inject_score_out_of_range(self):
        """vault.inject_min_score must be 0.0-10.0."""
        config = {"vault": {"inject_min_score": 15.0}}
        is_valid, errors = validate_config_dict(config)
        assert not is_valid

    def test_rate_limit_rpm_negative(self):
        """rate_limit_rpm must be >= 1."""
        config = {"rate_limit_rpm": 0}
        is_valid, errors = validate_config_dict(config)
        assert not is_valid

    def test_upstream_timeout_out_of_range(self):
        """upstream.timeout must be 1-3600 seconds."""
        config = {"upstream": {"timeout": 5000}}
        is_valid, errors = validate_config_dict(config)
        assert not is_valid

    def test_unknown_top_level_field(self):
        """Unknown fields should cause validation error."""
        config = {"port": 8766, "unknown_field": "value"}
        is_valid, errors = validate_config_dict(config)
        assert not is_valid
        # Error path could be "unknown_field" or "<root>" depending on validator
        assert any(
            "unknown" in e["path"].lower() or "allowed" in e["message"].lower() for e in errors
        )

    def test_unknown_feature_flag(self):
        """Unknown feature flags should cause validation error."""
        config = {"features": {"skeleton": True, "nonexistent_feature": True}}
        is_valid, errors = validate_config_dict(config)
        assert not is_valid

    def test_multiple_errors(self):
        """Multiple invalid fields produce multiple errors."""
        config = {
            "port": 70000,  # Out of range
            "mode": "invalid",  # Invalid value
            "compression": {"threshold_tokens": -100},  # Negative
        }
        is_valid, errors = validate_config_dict(config)
        assert not is_valid
        assert len(errors) >= 3

    def test_failover_provider_enum(self):
        """failover.chain[].provider must be valid provider name."""
        config = {
            "failover": {
                "enabled": True,
                "chain": [{"provider": "invalid_provider"}],
            }
        }
        is_valid, errors = validate_config_dict(config)
        assert not is_valid

    def test_vault_retrieval_backend_enum(self):
        """vault.retrieval_backend must be json_blocks or sqlite."""
        config = {"vault": {"retrieval_backend": "postgresql"}}
        is_valid, errors = validate_config_dict(config)
        assert not is_valid


class TestErrorMessages:
    """Tests for error message quality and clarity."""

    def test_error_has_path(self):
        """Each error includes the field path."""
        config = {"port": 70000}
        is_valid, errors = validate_config_dict(config)
        assert errors[0]["path"] == "port"

    def test_error_has_message(self):
        """Each error includes a human-readable message."""
        config = {"port": "not_a_number"}
        is_valid, errors = validate_config_dict(config)
        assert "message" in errors[0]
        assert len(errors[0]["message"]) > 0
        assert "port" in errors[0]["message"].lower()

    def test_error_has_suggestion(self):
        """Each error includes a fix suggestion."""
        config = {"mode": "invalid"}
        is_valid, errors = validate_config_dict(config)
        assert "suggestion" in errors[0]
        assert len(errors[0]["suggestion"]) > 0

    def test_error_format_function(self):
        """format_errors() produces readable output."""
        config = {"port": 70000, "mode": "bad"}
        is_valid, errors = validate_config_dict(config)
        formatted = format_errors(errors, filepath="test.yaml")
        assert "test.yaml" in formatted
        assert len(errors) > 0
        assert str(len(errors)) in formatted


class TestConfigFileValidation:
    """Tests for validate_config_file() with actual files."""

    def test_valid_json_file(self):
        """Valid JSON config file is accepted."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"port": 8766}, f)
            f.flush()
            path = f.name

        try:
            is_valid, errors = validate_config_file(path)
            assert is_valid
            assert len(errors) == 0
        finally:
            Path(path).unlink()

    def test_valid_yaml_file(self):
        """Valid YAML config file is accepted."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write("port: 8766\n")
            f.flush()
            path = f.name

        try:
            is_valid, errors = validate_config_file(path)
            assert is_valid
            assert len(errors) == 0
        finally:
            Path(path).unlink()

    def test_file_not_found(self):
        """Missing file produces readable error."""
        is_valid, errors = validate_config_file("/nonexistent/path/config.yaml")
        assert not is_valid
        assert len(errors) > 0
        assert "not found" in errors[0]["message"].lower()

    def test_invalid_json_file(self):
        """Malformed JSON produces readable error."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid json}")
            f.flush()
            path = f.name

        try:
            is_valid, errors = validate_config_file(path)
            assert not is_valid
            assert any("json" in e["message"].lower() for e in errors)
        finally:
            Path(path).unlink()

    def test_config_validation_then_dict_errors(self):
        """File with valid JSON but invalid config is caught."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"port": 70000}, f)  # Out of range
            f.flush()
            path = f.name

        try:
            is_valid, errors = validate_config_file(path)
            assert not is_valid
            assert any("port" in e["path"] for e in errors)
        finally:
            Path(path).unlink()


class TestRealWorldConfigs:
    """Tests with realistic, complete configs."""

    def test_production_like_config(self):
        """Realistic production config validates."""
        config = {
            "port": 8766,
            "mode": "hybrid",
            "db": "~/.tokenpak/data/monitor.db",
            "compression": {
                "enabled": True,
                "max_chars": 120,
                "threshold_tokens": 4500,
                "cache_size": 2000,
            },
            "features": {
                "skeleton": True,
                "shadow_reader": True,
                "router": True,
                "validation_gate": True,
                "validation_gate_soft": True,
            },
            "budget": {
                "total_tokens": 12000,
                "validation_gate_cap": 120000,
            },
            "vault": {
                "index_path": "~/vault/.tokenpak",
                "inject_budget": 4000,
                "inject_top_k": 5,
            },
            "rate_limit_rpm": 60,
        }
        is_valid, errors = validate_config_dict(config)
        assert is_valid

    def test_minimal_production_config(self):
        """Minimal but valid production config."""
        config = {
            "port": 8766,
            "mode": "hybrid",
        }
        is_valid, errors = validate_config_dict(config)
        assert is_valid

    def test_failover_config(self):
        """Config with failover chain is valid."""
        config = {
            "failover": {
                "enabled": True,
                "chain": [
                    {
                        "provider": "anthropic",
                        "credential_env": "ANTHROPIC_API_KEY",
                    },
                    {
                        "provider": "openai",
                        "credential_env": "OPENAI_API_KEY",
                        "model_map": {
                            "claude-opus-4-5": "gpt-4o",
                        },
                    },
                ],
            }
        }
        is_valid, errors = validate_config_dict(config)
        assert is_valid
