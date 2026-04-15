"""Tests for error handling infrastructure."""


import pytest
pytest.importorskip("tokenpak.infrastructure", reason="module not available in current build")
import pytest

from tokenpak.infrastructure.error_handling import (
    CacheCorruptedError,
    ConfigValidationError,
    InvalidAPIKeyError,
    MissingAPIKeyError,
    MissingConfigError,
    ProviderError,
    RateLimitError,
    TimeoutError,
    TokenPakError,
    format_error,
)


class TestTokenPakError:
    """Test base error class."""

    def test_error_creation(self):
        """Test creating a TokenPak error."""
        error = TokenPakError(
            code="TP-E999",
            message="Test error",
            suggestion="Test fix",
            context="test context",
        )
        assert error.code == "TP-E999"
        assert error.message == "Test error"
        assert error.suggestion == "Test fix"
        assert error.context == "test context"

    def test_error_string_representation(self):
        """Test error string format."""
        error = TokenPakError(
            code="TP-E999",
            message="Test message",
            suggestion="Test fix",
            context="test context",
        )
        s = str(error)
        assert "TP-E999" in s
        assert "Test message" in s
        assert "Test fix" in s
        assert "test context" in s

    def test_error_to_dict(self):
        """Test error serialization to dict."""
        error = TokenPakError(
            code="TP-E999", message="Test", suggestion="Fix", context="ctx"
        )
        d = error.to_dict()
        assert d["error_code"] == "TP-E999"
        assert d["message"] == "Test"
        assert d["suggestion"] == "Fix"
        assert d["context"] == "ctx"

    def test_error_with_default_suggestion(self):
        """Test error with default suggestion."""
        error = TokenPakError(code="TP-E999", message="Test")
        assert "Check TokenPak logs" in error.suggestion


class TestConfigErrors:
    """Test config-related errors."""

    def test_config_validation_error(self):
        """Test config validation error."""
        error = ConfigValidationError("port", "out of range")
        assert "TP-E002" in error.code
        assert "port" in error.message
        assert "out of range" in error.message

    def test_missing_config_error(self):
        """Test missing config field error."""
        error = MissingConfigError("api_keys")
        assert "TP-E003" in error.code
        assert "api_keys" in error.message
        assert "api_keys" in error.suggestion

    def test_custom_config_validation_suggestion(self):
        """Test custom suggestion in config error."""
        error = ConfigValidationError(
            "port", "invalid", suggestion="Use port between 1024-65535"
        )
        assert "1024-65535" in error.suggestion


class TestAuthenticationErrors:
    """Test authentication errors."""

    def test_invalid_api_key_error(self):
        """Test invalid API key error."""
        error = InvalidAPIKeyError("anthropic")
        assert "TP-E202" in error.code
        assert "anthropic" in error.message
        assert "anthropic" in error.suggestion

    def test_missing_api_key_error(self):
        """Test missing API key error."""
        error = MissingAPIKeyError("openai")
        assert "TP-E203" in error.code
        assert "openai" in error.message
        assert "openai" in error.suggestion


class TestNetworkErrors:
    """Test network-related errors."""

    def test_timeout_error(self):
        """Test timeout error."""
        error = TimeoutError("anthropic", timeout_seconds=30)
        assert "TP-E103" in error.code
        assert "anthropic" in error.message
        assert "30" in error.message

    def test_provider_error(self):
        """Test provider error."""
        error = ProviderError("openai", 429, "Rate limit exceeded")
        assert "TP-E501" in error.code
        assert "openai" in error.message
        assert "429" in error.message
        assert "Rate limit exceeded" in error.message


class TestRateLimitError:
    """Test rate limit errors."""

    def test_rate_limit_without_retry(self):
        """Test rate limit error without retry info."""
        error = RateLimitError("anthropic")
        assert "TP-E301" in error.code
        assert "anthropic" in error.message

    def test_rate_limit_with_retry(self):
        """Test rate limit error with retry info."""
        error = RateLimitError("openai", retry_after_seconds=60)
        assert "TP-E301" in error.code
        assert "openai" in error.message
        assert "60" in error.message


class TestCacheErrors:
    """Test cache-related errors."""

    def test_cache_corrupted_error(self):
        """Test corrupted cache error."""
        error = CacheCorruptedError()
        assert "TP-E402" in error.code
        assert "corrupted" in error.message.lower()
        assert "cache clear" in error.suggestion.lower()


class TestErrorFormatting:
    """Test error formatting utility."""

    def test_format_tokenpak_error(self):
        """Test formatting TokenPak error."""
        error = InvalidAPIKeyError("anthropic")
        formatted = format_error(error)
        assert "TP-E202" in formatted
        assert "anthropic" in formatted

    def test_format_unknown_exception(self):
        """Test formatting unknown exception."""
        try:
            x = 1 / 0
        except ZeroDivisionError as e:
            formatted = format_error(e)
            assert "TP-E601" in formatted
            assert "ZeroDivisionError" in formatted
            assert "Traceback" not in formatted  # No raw traceback


class TestErrorCodes:
    """Test error code consistency."""

    def test_all_errors_have_codes(self):
        """Test that all errors have valid codes."""
        errors = [
            ConfigValidationError("field", "reason"),
            MissingConfigError("field"),
            InvalidAPIKeyError("provider"),
            MissingAPIKeyError("provider"),
            TimeoutError("service", 30),
            ProviderError("provider", 500, "reason"),
            RateLimitError("provider"),
            CacheCorruptedError(),
        ]
        for error in errors:
            assert error.code.startswith("TP-E")
            assert len(error.code) == 7  # TP-EXXX format

    def test_error_code_ranges(self):
        """Test that error codes are in expected ranges."""
        # Config errors (E0xx)
        assert ConfigValidationError("f", "r").code == "TP-E002"
        assert MissingConfigError("f").code == "TP-E003"

        # Auth errors (E2xx)
        assert InvalidAPIKeyError("p").code == "TP-E202"
        assert MissingAPIKeyError("p").code == "TP-E203"

        # Rate limit errors (E3xx)
        assert RateLimitError("p").code == "TP-E301"

        # Cache errors (E4xx)
        assert CacheCorruptedError().code == "TP-E402"

        # Provider errors (E5xx)
        assert ProviderError("p", 500, "r").code == "TP-E501"


class TestErrorMessages:
    """Test error message quality."""

    def test_error_has_message(self):
        """Test that all errors have messages."""
        errors = [
            ConfigValidationError("field", "reason"),
            InvalidAPIKeyError("anthropic"),
            TimeoutError("service", 30),
        ]
        for error in errors:
            assert len(error.message) > 0
            assert error.message is not None

    def test_error_has_suggestion(self):
        """Test that all errors have fix suggestions."""
        errors = [
            ConfigValidationError("field", "reason"),
            InvalidAPIKeyError("anthropic"),
            TimeoutError("service", 30),
            RateLimitError("anthropic"),
        ]
        for error in errors:
            assert len(error.suggestion) > 0
            assert error.suggestion is not None

    def test_suggestions_are_actionable(self):
        """Test that suggestions are actionable (not vague)."""
        error = InvalidAPIKeyError("anthropic")
        suggestion = error.suggestion.lower()
        # Should contain specific action words
        assert any(
            word in suggestion
            for word in ["check", "add", "verify", "configure", "update"]
        )
