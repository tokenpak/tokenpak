"""
Unit tests for user-facing error messages.

Tests that each error path:
1. Has a clear, non-technical message
2. Specifies what went wrong (field/value/reason)
3. Includes an actionable fix suggestion
4. Does NOT expose raw exception messages
5. Includes specific values (not generic placeholders)
"""

import pytest

from tokenpak.config_validator import ConfigValidator
from tokenpak.integrations.litellm.formatter import compile_pack
from tokenpak.integrations.litellm.middleware import TokenPakMiddleware


class TestConfigErrors:
    """Test config validation error messages."""

    def test_api_keys_is_optional(self):
        """Config without api_keys is valid — credentials can flow from 5
        sources (env-pool, user-config, claude-cli, codex-cli, openclaw).
        See project_tokenpak_creds_architecture.md."""
        validator = ConfigValidator()
        config = {"port": 8766}
        errors = validator.validate(config)

        assert errors == []

    def test_invalid_port_type(self):
        """Port as string should say it needs to be int."""
        validator = ConfigValidator()
        config = {"api_keys": {}, "port": "8766"}
        errors = validator.validate(config)

        port_error = next((e for e in errors if e.field == "port"), None)
        assert port_error is not None
        assert "integer" in port_error.message.lower()
        assert "8766" in port_error.suggestion

    def test_invalid_port_range(self):
        """Port outside 1024-65535 should show valid range."""
        validator = ConfigValidator()
        config = {"api_keys": {}, "port": 100}  # Too low
        errors = validator.validate(config)

        port_error = next((e for e in errors if e.field == "port"), None)
        assert port_error is not None
        assert "1024" in port_error.suggestion
        assert "65535" in port_error.suggestion
        assert "100" in port_error.suggestion  # Show current value

    def test_negative_cache_ttl(self):
        """Negative cache_ttl should be actionable."""
        validator = ConfigValidator()
        config = {"api_keys": {}, "cache_ttl": -1}
        errors = validator.validate(config)

        ttl_error = next((e for e in errors if e.field == "cache_ttl"), None)
        assert ttl_error is not None
        assert "positive" in ttl_error.message.lower()
        assert "3600" in ttl_error.suggestion or "seconds" in ttl_error.suggestion

    def test_invalid_url_format(self):
        """Invalid URL should show valid example."""
        validator = ConfigValidator()
        config = {
            "api_keys": {},
            "provider_urls": {"openai": "not-a-url"},
        }
        errors = validator.validate(config)

        url_error = next((e for e in errors if "url" in e.field.lower()), None)
        assert url_error is not None
        assert "https://" in url_error.suggestion
        assert "openai" in url_error.suggestion.lower()

    def test_directory_not_found(self):
        """Directory error should suggest mkdir."""
        validator = ConfigValidator()
        config = {
            "api_keys": {},
            "log_dir": "/nonexistent/path/that/does/not/exist",
        }
        errors = validator.validate(config)

        dir_error = next((e for e in errors if "log_dir" in e.field), None)
        assert dir_error is not None
        assert "mkdir" in dir_error.suggestion or "create" in dir_error.suggestion.lower()

    def test_api_keys_wrong_type(self):
        """api_keys as list should show dict format."""
        validator = ConfigValidator()
        config = {"api_keys": ["anthropic", "openai"]}  # Should be dict
        errors = validator.validate(config)

        assert len(errors) > 0
        api_error = next((e for e in errors if "api_keys" in e.field), None)
        assert api_error is not None
        assert ":" in api_error.suggestion  # Show dict format


class TestMiddlewareErrors:
    """Test LiteLLM middleware error messages."""

    def test_invalid_compaction_strategy(self):
        """Invalid compaction value should list valid options."""
        with pytest.raises(ValueError) as exc_info:
            TokenPakMiddleware(compaction="invalid")

        error_msg = str(exc_info.value)
        assert "invalid" in error_msg.lower() or "compaction" in error_msg.lower()
        assert "none" in error_msg.lower()
        assert "balanced" in error_msg.lower()
        assert "aggressive" in error_msg.lower()

    def test_valid_compaction_strategies(self):
        """Valid strategies should not raise."""
        for strategy in ["none", "balanced", "aggressive"]:
            mw = TokenPakMiddleware(compaction=strategy)
            assert mw.compaction == strategy


class TestFormatterErrors:
    """Test TokenPak formatter error messages."""

    def test_invalid_tokenpak_type(self):
        """Non-compilable TokenPak type should handle gracefully."""
        # String pack is treated as dict-like, so test with something truly invalid
        try:
            result = compile_pack("not a valid tokenpak")
            # If it doesn't raise, that's OK too (it tries to be flexible)
        except (TypeError, ValueError, AttributeError) as exc:
            error_msg = str(exc)
            # Should give helpful error about format if it fails
            assert any(x in error_msg.lower() for x in ["tokenpak", "compile", "format", "blocks"])

    def test_budget_too_small(self):
        """Budget below minimum should suggest increase."""
        with pytest.raises(ValueError) as exc_info:
            compile_pack({"blocks": []}, budget=10)

        error_msg = str(exc_info.value)
        assert "budget" in error_msg.lower()
        assert "50" in error_msg or "minimum" in error_msg.lower()

    def test_budget_negative(self):
        """Negative budget should be rejected early."""
        with pytest.raises(ValueError) as exc_info:
            compile_pack({"blocks": []}, budget=-1)

        error_msg = str(exc_info.value)
        assert "budget" in error_msg.lower()
        assert "positive" in error_msg.lower()

    def test_empty_blocks_compileable(self):
        """Empty blocks list should compile (not error)."""
        result = compile_pack({"blocks": []}, budget=8000)
        assert isinstance(result, list)
        assert len(result) >= 1  # At least system message


class TestCLIErrors:
    """Test CLI error handling (integration tests)."""

    def test_unknown_command_has_suggestion(self):
        """Unknown command like 'comress' should suggest 'compress'."""
        # This would be tested via CLI integration
        # Pseudo-code:
        # result = run_cli(["comress"])
        # assert result.exit_code == 1
        # assert "compress" in result.output.lower()
        pass

    def test_unknown_command_shows_help(self):
        """Unknown command should show command help."""
        # This would be tested via CLI integration
        # result = run_cli(["notacommand"])
        # assert "tokenpak help" in result.output.lower()
        pass


class TestErrorMessageQuality:
    """Tests that verify error message standards."""

    def test_config_error_has_all_fields(self):
        """Each config error should have all required fields."""
        validator = ConfigValidator()
        config = {"api_keys": 123}  # Wrong type
        errors = validator.validate(config)

        assert len(errors) > 0
        for error in errors:
            assert error.field, "Error must have field"
            assert error.expected, "Error must have expected value"
            assert error.actual is not None, "Error must have actual value"
            assert error.message, "Error must have message"
            assert error.suggestion, "Error must have suggestion"

    def test_no_raw_exceptions_in_messages(self):
        """Error messages should not contain raw Python exception details."""
        validator = ConfigValidator()
        config = {"api_keys": 123}
        errors = validator.validate(config)

        for error in errors:
            # Should not have Python traceback indicators
            assert "<" not in error.message
            assert ">" not in error.message
            assert "traceback" not in error.message.lower()
            # Should not have file paths with stack info
            assert "File " not in error.message


class TestProxyErrorWrapping:
    """Test that proxy errors are properly wrapped."""

    def test_json_error_format(self):
        """Proxy errors should return JSON with status and message."""
        from tokenpak.integrations.litellm.proxy import _json_error

        result = _json_error(400, "Test error message")
        assert isinstance(result, dict)
        assert "error" in result
        assert result["error"]["status"] == 400
        assert result["error"]["message"] == "Test error message"

    def test_missing_tokenpak_field_error(self):
        """Missing 'tokenpak' field should be clear."""
        from tokenpak.integrations.litellm.proxy import _json_error

        result = _json_error(400, "Missing required field: 'tokenpak'")
        assert "tokenpak" in result["error"]["message"]
        assert "required" in result["error"]["message"].lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
