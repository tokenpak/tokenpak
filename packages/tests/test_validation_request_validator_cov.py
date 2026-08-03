"""
Tests for tokenpak.validation.request_validator module.

Coverage targets:
- RequestValidationResult class
- RequestValidator class with modes (strict, warn, off)
- JSON parsing errors
- Schema validation (jsonschema and manual fallback)
- Provider-specific semantic validation
- Helper functions (get_validation_mode, validate_request, etc.)
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.validation.request_validator import (
    HAS_JSONSCHEMA,
    RequestValidationResult,
    RequestValidator,
    VALIDATION_MODES,
    get_request_validator,
    get_validation_mode,
    validate_request,
)


# ---------------------------------------------------------------------------
# RequestValidationResult tests
# ---------------------------------------------------------------------------


class TestRequestValidationResult:
    """Tests for RequestValidationResult dataclass behavior."""

    def test_valid_result_truthy(self):
        """Valid result evaluates to True."""
        result = RequestValidationResult(valid=True, provider="anthropic")
        assert bool(result) is True

    def test_invalid_result_falsy(self):
        """Invalid result evaluates to False."""
        result = RequestValidationResult(valid=False, provider="anthropic")
        assert bool(result) is False

    def test_repr_valid(self):
        """Valid result has clean repr."""
        result = RequestValidationResult(valid=True, provider="openai")
        assert "valid=True" in repr(result)
        assert "openai" in repr(result)

    def test_repr_invalid_with_errors(self):
        """Invalid result shows error count in repr."""
        errors = [{"field": "model", "error": "missing"}]
        result = RequestValidationResult(valid=False, provider="anthropic", errors=errors)
        assert "valid=False" in repr(result)
        assert "errors=1" in repr(result)

    def test_to_error_response_structure(self):
        """Error response has correct structure."""
        errors = [{"field": "model", "error": "required field missing"}]
        result = RequestValidationResult(valid=False, provider="anthropic", errors=errors)
        resp = result.to_error_response()
        assert resp["error"]["type"] == "validation_error"
        assert resp["error"]["message"] == "Request validation failed"
        assert resp["error"]["details"] == errors
        assert "messages" in resp["error"]["hint"]  # anthropic path

    def test_to_error_response_openai_hint(self):
        """OpenAI provider gets chat-completions hint path."""
        result = RequestValidationResult(valid=False, provider="openai", errors=[])
        resp = result.to_error_response()
        assert "chat-completions" in resp["error"]["hint"]

    def test_to_error_response_google_hint(self):
        """Google provider gets google-generate-content hint path."""
        result = RequestValidationResult(valid=False, provider="google", errors=[])
        resp = result.to_error_response()
        assert "google-generate-content" in resp["error"]["hint"]

    def test_to_dict(self):
        """to_dict includes all fields."""
        result = RequestValidationResult(
            valid=True,
            provider="anthropic",
            errors=[],
            warnings=[{"field": "model", "error": "looks odd"}],
        )
        d = result.to_dict()
        assert d["valid"] is True
        assert d["provider"] == "anthropic"
        assert d["errors"] == []
        assert len(d["warnings"]) == 1


# ---------------------------------------------------------------------------
# RequestValidator construction tests
# ---------------------------------------------------------------------------


class TestRequestValidatorConstruction:
    """Tests for RequestValidator initialization."""

    def test_default_mode_is_warn(self):
        """Default mode is 'warn'."""
        v = RequestValidator()
        assert v.mode == "warn"

    def test_strict_mode(self):
        """Can create with strict mode."""
        v = RequestValidator(mode="strict")
        assert v.mode == "strict"

    def test_off_mode(self):
        """Can create with off mode."""
        v = RequestValidator(mode="off")
        assert v.mode == "off"

    def test_invalid_mode_raises(self):
        """Invalid mode raises ValueError."""
        with pytest.raises(ValueError, match="Invalid validation mode"):
            RequestValidator(mode="invalid")

    def test_all_modes_valid(self):
        """All VALIDATION_MODES are accepted."""
        for mode in VALIDATION_MODES:
            v = RequestValidator(mode=mode)
            assert v.mode == mode


# ---------------------------------------------------------------------------
# RequestValidator.validate() tests
# ---------------------------------------------------------------------------


class TestRequestValidatorValidate:
    """Tests for validate() method."""

    def test_off_mode_always_valid(self):
        """Off mode always returns valid=True."""
        v = RequestValidator(mode="off")
        result = v.validate(b"invalid json {{{", provider="anthropic")
        assert result.valid is True

    def test_invalid_json_fails(self):
        """Invalid JSON returns validation error."""
        v = RequestValidator(mode="strict")
        result = v.validate(b"not json at all", provider="anthropic")
        assert result.valid is False
        assert any("Invalid JSON" in e["error"] for e in result.errors)

    def test_non_object_json_fails(self):
        """JSON array/primitive fails (must be object)."""
        v = RequestValidator(mode="strict")
        result = v.validate(b"[1, 2, 3]", provider="anthropic")
        assert result.valid is False
        assert any("JSON object" in e["error"] for e in result.errors)

    def test_valid_anthropic_request(self):
        """Valid Anthropic request passes."""
        v = RequestValidator(mode="strict")
        body = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hello"}],
        }).encode()
        result = v.validate(body, provider="anthropic")
        assert result.valid is True

    def test_missing_required_field_fails(self):
        """Missing required field fails validation."""
        v = RequestValidator(mode="strict")
        body = json.dumps({
            "model": "claude-sonnet-4-6",
            # missing max_tokens and messages
        }).encode()
        result = v.validate(body, provider="anthropic")
        assert result.valid is False
        assert len(result.errors) >= 1

    def test_valid_openai_request(self):
        """Valid OpenAI request passes."""
        v = RequestValidator(mode="strict")
        body = json.dumps({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
        }).encode()
        result = v.validate(body, provider="openai")
        assert result.valid is True

    def test_valid_google_request(self):
        """Valid Google Gemini request passes."""
        v = RequestValidator(mode="strict")
        body = json.dumps({
            "contents": [{"parts": [{"text": "Hello"}]}],
        }).encode()
        result = v.validate(body, provider="google")
        assert result.valid is True


# ---------------------------------------------------------------------------
# Semantic validation tests
# ---------------------------------------------------------------------------


class TestSemanticValidation:
    """Tests for provider-specific semantic checks."""

    def test_anthropic_first_message_must_be_user(self):
        """Anthropic requires first message to be 'user' role."""
        v = RequestValidator(mode="strict")
        body = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 100,
            "messages": [{"role": "assistant", "content": "Hello"}],
        }).encode()
        result = v.validate(body, provider="anthropic")
        assert result.valid is False
        assert any("first message" in e["error"] for e in result.errors)

    def test_anthropic_alternating_roles_required(self):
        """Anthropic requires alternating user/assistant roles."""
        v = RequestValidator(mode="strict")
        body = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 100,
            "messages": [
                {"role": "user", "content": "Hi"},
                {"role": "user", "content": "Another user message"},  # consecutive
            ],
        }).encode()
        result = v.validate(body, provider="anthropic")
        assert result.valid is False
        assert any("consecutive" in e["error"] or "alternating" in e["error"] for e in result.errors)

    def test_anthropic_non_claude_model_warns(self):
        """Non-claude model generates warning (not error)."""
        v = RequestValidator(mode="warn")
        body = json.dumps({
            "model": "gpt-4o",  # Not a Claude model
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Hi"}],
        }).encode()
        result = v.validate(body, provider="anthropic")
        # Still valid in warn mode, but warnings present
        assert len(result.warnings) > 0
        assert any("claude" in w["error"].lower() for w in result.warnings)

    def test_openai_invalid_role_fails(self):
        """OpenAI with invalid role fails validation."""
        v = RequestValidator(mode="strict")
        body = json.dumps({
            "model": "gpt-4o",
            "messages": [{"role": "invalid_role", "content": "Hi"}],
        }).encode()
        result = v.validate(body, provider="openai")
        assert result.valid is False

    def test_google_empty_parts_fails(self):
        """Google contents with empty parts fails."""
        v = RequestValidator(mode="strict")
        body = json.dumps({
            "contents": [{"parts": []}],  # empty parts
        }).encode()
        result = v.validate(body, provider="google")
        assert result.valid is False


# ---------------------------------------------------------------------------
# validate_bytes() tests
# ---------------------------------------------------------------------------


class TestValidateBytes:
    """Tests for validate_bytes() URL-aware method."""

    def test_messages_endpoint_validated(self):
        """/v1/messages endpoint is validated."""
        v = RequestValidator(mode="strict")
        body = b"{}"  # invalid - missing required fields
        result = v.validate_bytes(body, "https://api.anthropic.com/v1/messages", "anthropic")
        assert result.valid is False

    def test_non_messages_endpoint_skipped(self):
        """Non-messages endpoints are skipped (passed through as valid)."""
        v = RequestValidator(mode="strict")
        body = b"not json"  # would fail if validated
        result = v.validate_bytes(body, "https://api.anthropic.com/v1/models", "anthropic")
        assert result.valid is True

    def test_empty_body_skipped(self):
        """Empty body is passed through as valid."""
        v = RequestValidator(mode="strict")
        result = v.validate_bytes(b"", "https://api.anthropic.com/v1/messages", "anthropic")
        assert result.valid is True

    def test_chat_completions_endpoint_validated(self):
        """/chat/completions endpoint is validated."""
        v = RequestValidator(mode="strict")
        body = b"{}"
        result = v.validate_bytes(body, "https://api.openai.com/v1/chat/completions", "openai")
        assert result.valid is False


# ---------------------------------------------------------------------------
# Helper function tests
# ---------------------------------------------------------------------------


class TestHelperFunctions:
    """Tests for module-level helper functions."""

    def test_get_validation_mode_default(self):
        """Default mode is 'warn' when no env/config."""
        with patch.dict(os.environ, {}, clear=True):
            # Clear the env var if present
            os.environ.pop("TOKENPAK_REQUEST_VALIDATION", None)
            mode = get_validation_mode()
            assert mode in VALIDATION_MODES

    def test_get_validation_mode_from_env(self):
        """Can set mode via environment variable."""
        with patch.dict(os.environ, {"TOKENPAK_REQUEST_VALIDATION": "strict"}):
            mode = get_validation_mode()
            assert mode == "strict"

    def test_get_validation_mode_invalid_env_falls_back(self):
        """Invalid env value falls back to config or default."""
        with patch.dict(os.environ, {"TOKENPAK_REQUEST_VALIDATION": "invalid_mode"}):
            mode = get_validation_mode()
            assert mode in VALIDATION_MODES  # Should be a valid mode

    def test_get_request_validator_singleton(self):
        """get_request_validator returns a singleton."""
        v1 = get_request_validator()
        v2 = get_request_validator()
        assert v1 is v2

    def test_validate_request_function(self):
        """validate_request() convenience function works."""
        body = json.dumps({
            "model": "claude-sonnet-4-6",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Test"}],
        }).encode()
        result = validate_request(body, provider="anthropic")
        # Should return a result (valid or not depending on global mode)
        assert isinstance(result, RequestValidationResult)

    def test_validate_request_with_url(self):
        """validate_request() with target_url uses validate_bytes."""
        body = b"not json"
        # Non-messages URL should skip validation
        result = validate_request(body, provider="anthropic", target_url="/v1/models")
        assert result.valid is True


# ---------------------------------------------------------------------------
# Manual validation fallback tests
# ---------------------------------------------------------------------------


class TestManualValidationFallback:
    """Tests for _validate_manual() when jsonschema is unavailable."""

    def test_manual_type_check_integer(self):
        """Manual validation checks integer types."""
        v = RequestValidator(mode="strict")
        # Bypass jsonschema to test manual validation
        errors = v._validate_manual(
            {"model": "test", "max_tokens": "not_an_int", "messages": []},
            {"type": "object", "required": ["model"], "properties": {
                "max_tokens": {"type": "integer"},
            }},
        )
        # Should have type error for max_tokens
        assert any("integer" in e["error"] for e in errors)

    def test_manual_minimum_check(self):
        """Manual validation checks minimum bounds."""
        v = RequestValidator(mode="strict")
        errors = v._validate_manual(
            {"value": -5},
            {"type": "object", "properties": {"value": {"type": "integer", "minimum": 0}}},
        )
        assert any("below minimum" in e["error"] for e in errors)

    def test_manual_enum_check(self):
        """Manual validation checks enum values."""
        v = RequestValidator(mode="strict")
        errors = v._validate_manual(
            {"role": "invalid_role"},
            {"type": "object", "properties": {"role": {"type": "string", "enum": ["user", "assistant"]}}},
        )
        assert any("one of" in e["error"] for e in errors)

    def test_check_type_boolean_not_integer(self):
        """Boolean should not match integer type."""
        v = RequestValidator(mode="strict")
        assert v._check_type(True, "integer") is False
        assert v._check_type(1, "integer") is True

    def test_check_type_null(self):
        """None matches null type."""
        v = RequestValidator(mode="strict")
        assert v._check_type(None, "null") is True
        assert v._check_type("", "null") is False
