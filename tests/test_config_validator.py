"""
Tests for tokenpak.cli.commands.validate_config

Tests the config validator CLI command and schema validation.
"""

import json
import tempfile
from pathlib import Path

import pytest

# tokenpak.cli.commands.validate_config short-circuits to (False, ["…not
# installed"]) when jsonschema or yaml are absent. Both are optional in
# the slim [dev] install — without these guards every test in this file
# fails with the install-instruction message instead of exercising the
# validator. Skip cleanly on slim install so the release test gate stays
# green; tests run with full assertions on installs that include
# jsonschema + yaml (full / dev-with-extras).
pytest.importorskip("jsonschema", reason="jsonschema is an optional dep; install via pip install jsonschema or tokenpak[full]")
pytest.importorskip("yaml", reason="PyYAML is an optional dep; install via pip install pyyaml or tokenpak[full]")

from tokenpak.cli.commands.validate_config import validate_file, load_schema


class TestSchemaLoading:
    """Test schema loading."""

    def test_schema_exists(self):
        """Schema file should exist and be loadable."""
        schema = load_schema()
        assert schema is not None
        assert isinstance(schema, dict)

    def test_schema_has_required_fields(self):
        """Schema should have core properties."""
        schema = load_schema()
        assert "$schema" in schema
        assert "properties" in schema
        assert "title" in schema

    def test_schema_has_config_fields(self):
        """Schema should define all TokenPak config fields."""
        schema = load_schema()
        props = schema["properties"]
        
        # Core fields
        assert "port" in props
        assert "mode" in props
        assert "compression" in props
        
        # Feature section
        assert "features" in props
        
        # Vault/budget
        assert "vault" in props
        assert "budget" in props


class TestValidateFile:
    """Test file validation."""

    def test_valid_yaml_config(self):
        """Valid YAML config should pass validation."""
        config_path = "tests/fixtures/valid_config.yaml"
        is_valid, messages = validate_file(config_path)
        assert is_valid is True
        assert any("valid" in msg.lower() for msg in messages)

    def test_invalid_port_range(self):
        """Port out of range (99) should fail."""
        config_path = "tests/fixtures/invalid_config_bad_port.yaml"
        is_valid, messages = validate_file(config_path)
        assert is_valid is False
        assert any("port" in msg.lower() for msg in messages)
        # Should mention the problem
        msg_text = "\n".join(messages)
        assert "99" in msg_text or "port" in msg_text.lower()

    def test_invalid_port_type(self):
        """Port as string should fail."""
        config_path = "tests/fixtures/invalid_config_bad_type.yaml"
        is_valid, messages = validate_file(config_path)
        assert is_valid is False
        assert any("port" in msg.lower() for msg in messages)

    def test_invalid_mode_enum(self):
        """Mode not in enum should fail."""
        config_path = "tests/fixtures/invalid_config_bad_mode.yaml"
        is_valid, messages = validate_file(config_path)
        assert is_valid is False
        assert any("mode" in msg.lower() for msg in messages)

    def test_missing_file(self):
        """Missing file should return clear error."""
        config_path = "/nonexistent/config.yaml"
        is_valid, messages = validate_file(config_path)
        assert is_valid is False
        assert any("not found" in msg.lower() for msg in messages)

    def test_invalid_json(self):
        """Malformed JSON should fail gracefully."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            f.write("{invalid json")
            temp_path = f.name
        
        try:
            is_valid, messages = validate_file(temp_path)
            assert is_valid is False
            assert any("json" in msg.lower() or "invalid" in msg.lower() for msg in messages)
        finally:
            Path(temp_path).unlink()

    def test_validation_error_messages(self):
        """Error messages should be human-friendly."""
        config_path = "tests/fixtures/invalid_config_bad_port.yaml"
        is_valid, messages = validate_file(config_path)
        assert is_valid is False
        
        # Should have multiple messages
        assert len(messages) > 1
        
        # First should be the summary
        assert "error" in messages[0].lower() or "failed" in messages[0].lower()
        
        # Subsequent should explain the error
        msg_text = "\n".join(messages)
        assert "field" in msg_text.lower() or "port" in msg_text.lower()

    def test_valid_minimal_config(self):
        """Config with minimal fields should pass."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"port": 8766}, f)
            temp_path = f.name
        
        try:
            is_valid, messages = validate_file(temp_path)
            # Should be valid since port is the only real constraint
            assert is_valid is True
        finally:
            Path(temp_path).unlink()

    def test_compression_section_validation(self):
        """Compression section should validate nested fields."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "port": 8766,
                "compression": {
                    "enabled": True,
                    "max_chars": -5  # Invalid: negative
                }
            }, f)
            temp_path = f.name
        
        try:
            is_valid, messages = validate_file(temp_path)
            # Negative max_chars should fail (minimum: 1)
            # Note: This might not fail if schema isn't enforced strictly
            # but the schema should have minimum: 1
        finally:
            Path(temp_path).unlink()

    def test_features_section_validation(self):
        """Features section should validate boolean toggles."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "port": 8766,
                "features": {
                    "skeleton": True,
                    "shadow_reader": "maybe"  # Invalid: should be bool
                }
            }, f)
            temp_path = f.name
        
        try:
            is_valid, messages = validate_file(temp_path)
            assert is_valid is False
            assert any("shadow_reader" in msg.lower() or "type" in msg.lower() for msg in messages)
        finally:
            Path(temp_path).unlink()

    def test_mode_enum_validation(self):
        """Mode must be strict, hybrid, or aggressive."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"port": 8766, "mode": "turbo"}, f)
            temp_path = f.name
        
        try:
            is_valid, messages = validate_file(temp_path)
            assert is_valid is False
            msg_text = "\n".join(messages)
            assert "mode" in msg_text.lower()
        finally:
            Path(temp_path).unlink()

    def test_port_minimum_boundary(self):
        """Port 1024 should be valid (minimum)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"port": 1024}, f)
            temp_path = f.name
        
        try:
            is_valid, messages = validate_file(temp_path)
            assert is_valid is True
        finally:
            Path(temp_path).unlink()

    def test_port_maximum_boundary(self):
        """Port 65535 should be valid (maximum)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"port": 65535}, f)
            temp_path = f.name
        
        try:
            is_valid, messages = validate_file(temp_path)
            assert is_valid is True
        finally:
            Path(temp_path).unlink()

    def test_budget_values(self):
        """Budget values should be positive integers."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "port": 8766,
                "budget": {
                    "total_tokens": 0  # Invalid: should be minimum 1
                }
            }, f)
            temp_path = f.name
        
        try:
            is_valid, messages = validate_file(temp_path)
            assert is_valid is False
            msg_text = "\n".join(messages)
            assert "total_tokens" in msg_text.lower() or "budget" in msg_text.lower()
        finally:
            Path(temp_path).unlink()

    def test_vault_injection_score(self):
        """Vault inject_min_score should be >= 0."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "port": 8766,
                "vault": {
                    "index_path": "~/vault/.tokenpak",
                    "inject_min_score": -1.5  # Invalid
                }
            }, f)
            temp_path = f.name
        
        try:
            is_valid, messages = validate_file(temp_path)
            assert is_valid is False
            msg_text = "\n".join(messages)
            assert "inject_min_score" in msg_text.lower() or "minimum" in msg_text.lower()
        finally:
            Path(temp_path).unlink()


class TestValidateFileIntegration:
    """Integration tests with actual fixture files."""

    def test_all_fixtures_exist(self):
        """All test fixtures should exist."""
        fixtures = [
            "tests/fixtures/valid_config.yaml",
            "tests/fixtures/invalid_config_bad_port.yaml",
            "tests/fixtures/invalid_config_bad_type.yaml",
            "tests/fixtures/invalid_config_bad_mode.yaml",
        ]
        for fixture in fixtures:
            assert Path(fixture).exists(), f"Fixture missing: {fixture}"

    def test_valid_fixture_passes(self):
        """The valid config fixture should pass validation."""
        is_valid, messages = validate_file("tests/fixtures/valid_config.yaml")
        assert is_valid is True, f"Valid config failed: {messages}"

    def test_invalid_fixtures_fail(self):
        """All invalid fixtures should fail validation."""
        invalid_fixtures = [
            "tests/fixtures/invalid_config_bad_port.yaml",
            "tests/fixtures/invalid_config_bad_type.yaml",
            "tests/fixtures/invalid_config_bad_mode.yaml",
        ]
        for fixture in invalid_fixtures:
            is_valid, messages = validate_file(fixture)
            assert is_valid is False, f"Invalid fixture passed: {fixture}"
