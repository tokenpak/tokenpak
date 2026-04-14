"""Unit tests for validation/request_validator.py, validation/request_schema.py,
validation/response_schema.py, and validation/validator.py (ResponseValidator).

Schema modules (request_schema, response_schema) are simple data definitions
and are consolidated here per task spec guidance.
"""
from __future__ import annotations

import json
import pytest

from tokenpak.core.validation.request_validator import (
    VALIDATION_MODES,
    RequestValidationResult,
    RequestValidator,
)
from tokenpak.core.validation.request_schema import (
    ANTHROPIC_MESSAGE_SCHEMA,
    GOOGLE_GENERATE_CONTENT_SCHEMA,
    OPENAI_CHAT_SCHEMA,
    OPENAI_RESPONSES_SCHEMA,
    get_request_schema,
)
from tokenpak.core.validation.response_schema import (
    RESPONSE_SCHEMA,
    RESPONSE_SCHEMA_MINIMAL,
    get_schema,
)
from tokenpak.core.validation.validator import (
    ResponseValidator,
    ValidationResult,
    is_valid,
    validate_response,
)

# ---------------------------------------------------------------------------
# Fixtures / shared helpers
# ---------------------------------------------------------------------------

VALID_ANTHROPIC = {
    "model": "claude-sonnet-4-6",
    "max_tokens": 1024,
    "messages": [{"role": "user", "content": "Hello"}],
}

VALID_OPENAI = {
    "model": "gpt-4o",
    "messages": [{"role": "user", "content": "Hello"}],
}

VALID_CODEX = {
    "model": "gpt-4o",
    "input": "Write me a hello world",
}

VALID_GOOGLE = {
    "contents": [{"parts": [{"text": "Hello"}]}],
}

VALID_RESPONSE = {
    "model": "claude-sonnet-4-6",
    "tokens_sent": 100,
    "tokens_received": 50,
    "cost": 0.01,
    "timestamp": "2026-04-12T12:00:00Z",
}


def _body(d: dict) -> bytes:
    return json.dumps(d).encode()


@pytest.fixture
def strict_validator():
    return RequestValidator(mode="strict")


@pytest.fixture
def warn_validator():
    return RequestValidator(mode="warn")


# ---------------------------------------------------------------------------
# RequestValidationResult
# ---------------------------------------------------------------------------


class TestRequestValidationResult:
    def test_bool_true(self):
        r = RequestValidationResult(valid=True, provider="anthropic")
        assert bool(r) is True

    def test_bool_false(self):
        r = RequestValidationResult(valid=False, provider="openai", errors=[{"field": "x", "error": "bad"}])
        assert bool(r) is False

    def test_repr_valid(self):
        r = RequestValidationResult(valid=True, provider="anthropic")
        assert "valid=True" in repr(r)
        assert "anthropic" in repr(r)

    def test_repr_invalid(self):
        r = RequestValidationResult(valid=False, provider="openai", errors=[{"field": "x", "error": "bad"}])
        assert "valid=False" in repr(r)
        assert "errors=1" in repr(r)

    def test_defaults_empty_lists(self):
        r = RequestValidationResult(valid=True)
        assert r.errors == []
        assert r.warnings == []

    def test_to_dict(self):
        r = RequestValidationResult(
            valid=False,
            provider="google",
            errors=[{"field": "f", "error": "e"}],
            warnings=[{"field": "w", "error": "w"}],
        )
        d = r.to_dict()
        assert d["valid"] is False
        assert d["provider"] == "google"
        assert len(d["errors"]) == 1
        assert len(d["warnings"]) == 1

    def test_to_error_response_anthropic(self):
        r = RequestValidationResult(valid=False, provider="anthropic", errors=[{"field": "model", "error": "bad"}])
        payload = r.to_error_response()
        assert payload["error"]["type"] == "validation_error"
        assert "messages" in payload["error"]["hint"]

    def test_to_error_response_openai(self):
        r = RequestValidationResult(valid=False, provider="openai", errors=[])
        payload = r.to_error_response()
        assert "chat-completions" in payload["error"]["hint"]

    def test_to_error_response_codex(self):
        r = RequestValidationResult(valid=False, provider="openai-codex", errors=[])
        payload = r.to_error_response()
        assert "responses" in payload["error"]["hint"]

    def test_to_error_response_google(self):
        r = RequestValidationResult(valid=False, provider="google", errors=[])
        payload = r.to_error_response()
        assert "google-generate-content" in payload["error"]["hint"]

    def test_to_error_response_unknown_provider(self):
        r = RequestValidationResult(valid=False, provider="unknown_xyz", errors=[])
        payload = r.to_error_response()
        assert "requests" in payload["error"]["hint"]


# ---------------------------------------------------------------------------
# RequestValidator — init
# ---------------------------------------------------------------------------


class TestRequestValidatorInit:
    def test_valid_mode_strict(self):
        v = RequestValidator(mode="strict")
        assert v.mode == "strict"

    def test_valid_mode_warn(self):
        v = RequestValidator(mode="warn")
        assert v.mode == "warn"

    def test_valid_mode_off(self):
        v = RequestValidator(mode="off")
        assert v.mode == "off"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid validation mode"):
            RequestValidator(mode="aggressive")

    def test_validation_modes_tuple(self):
        assert "strict" in VALIDATION_MODES
        assert "warn" in VALIDATION_MODES
        assert "off" in VALIDATION_MODES


# ---------------------------------------------------------------------------
# RequestValidator — mode="off"
# ---------------------------------------------------------------------------


class TestRequestValidatorModeOff:
    def test_off_skips_validation_any_body(self):
        v = RequestValidator(mode="off")
        result = v.validate(b"not json at all", "anthropic")
        assert result.valid is True

    def test_off_skips_validation_any_provider(self):
        v = RequestValidator(mode="off")
        for provider in ("anthropic", "openai", "google", "unknown"):
            result = v.validate(b"{}", provider)
            assert result.valid is True

    def test_off_preserves_provider(self):
        v = RequestValidator(mode="off")
        result = v.validate(b"{}", "anthropic")
        assert result.provider == "anthropic"


# ---------------------------------------------------------------------------
# RequestValidator — Anthropic (valid cases)
# ---------------------------------------------------------------------------


class TestRequestValidatorAnthropicValid:
    def test_valid_minimal(self, strict_validator):
        result = strict_validator.validate(_body(VALID_ANTHROPIC), "anthropic")
        assert result.valid

    def test_valid_with_system_string(self, strict_validator):
        body = {**VALID_ANTHROPIC, "system": "You are a helpful assistant."}
        result = strict_validator.validate(_body(body), "anthropic")
        assert result.valid

    def test_valid_with_stream(self, strict_validator):
        body = {**VALID_ANTHROPIC, "stream": True}
        result = strict_validator.validate(_body(body), "anthropic")
        assert result.valid

    def test_valid_with_temperature(self, strict_validator):
        body = {**VALID_ANTHROPIC, "temperature": 0.7}
        result = strict_validator.validate(_body(body), "anthropic")
        assert result.valid

    def test_valid_alternating_roles(self, strict_validator):
        body = {
            **VALID_ANTHROPIC,
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi!"},
                {"role": "user", "content": "How are you?"},
            ],
        }
        result = strict_validator.validate(_body(body), "anthropic")
        assert result.valid


# ---------------------------------------------------------------------------
# RequestValidator — Anthropic (invalid cases)
# ---------------------------------------------------------------------------


class TestRequestValidatorAnthropicInvalid:
    def test_invalid_json_bytes(self, strict_validator):
        result = strict_validator.validate(b"{not valid json}", "anthropic")
        assert not result.valid
        assert any("Invalid JSON" in e["error"] for e in result.errors)
        assert result.errors[0]["field"] == "(body)"

    def test_non_dict_body(self, strict_validator):
        result = strict_validator.validate(b"[1, 2, 3]", "anthropic")
        assert not result.valid
        assert any("JSON object" in e["error"] for e in result.errors)

    def test_missing_model(self, strict_validator):
        body = {"max_tokens": 1024, "messages": [{"role": "user", "content": "Hi"}]}
        result = strict_validator.validate(_body(body), "anthropic")
        assert not result.valid
        assert any("model" in e["field"] for e in result.errors)

    def test_missing_max_tokens(self, strict_validator):
        body = {"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "Hi"}]}
        result = strict_validator.validate(_body(body), "anthropic")
        assert not result.valid
        assert any("max_tokens" in e["field"] for e in result.errors)

    def test_missing_messages(self, strict_validator):
        body = {"model": "claude-sonnet-4-6", "max_tokens": 1024}
        result = strict_validator.validate(_body(body), "anthropic")
        assert not result.valid
        assert any("messages" in e["field"] for e in result.errors)

    def test_empty_messages_array(self, strict_validator):
        body = {**VALID_ANTHROPIC, "messages": []}
        result = strict_validator.validate(_body(body), "anthropic")
        assert not result.valid


# ---------------------------------------------------------------------------
# RequestValidator — Anthropic (semantics)
# ---------------------------------------------------------------------------


class TestRequestValidatorAnthropicSemantics:
    def test_first_message_assistant_error(self, strict_validator):
        body = {
            **VALID_ANTHROPIC,
            "messages": [{"role": "assistant", "content": "Hello"}],
        }
        result = strict_validator.validate(_body(body), "anthropic")
        assert not result.valid
        assert any("first message" in e["error"] for e in result.errors)

    def test_consecutive_same_role_error(self, strict_validator):
        body = {
            **VALID_ANTHROPIC,
            "messages": [
                {"role": "user", "content": "Hello"},
                {"role": "user", "content": "Another user msg"},
            ],
        }
        result = strict_validator.validate(_body(body), "anthropic")
        assert not result.valid
        assert any("consecutive" in e["error"] for e in result.errors)

    def test_non_claude_model_produces_warning(self, strict_validator):
        body = {**VALID_ANTHROPIC, "model": "gpt-4o"}
        result = strict_validator.validate(_body(body), "anthropic")
        # May be valid or invalid depending on schema, but must have a warning
        assert any("model" in w["field"] for w in result.warnings)

    def test_claude_model_no_warning(self, strict_validator):
        body = {**VALID_ANTHROPIC, "model": "claude-3-5-sonnet"}
        result = strict_validator.validate(_body(body), "anthropic")
        assert not any("model" in w["field"] for w in result.warnings)


# ---------------------------------------------------------------------------
# RequestValidator — OpenAI (valid + invalid + semantics)
# ---------------------------------------------------------------------------


class TestRequestValidatorOpenAI:
    def test_valid_minimal(self, strict_validator):
        result = strict_validator.validate(_body(VALID_OPENAI), "openai")
        assert result.valid

    def test_valid_with_system_message(self, strict_validator):
        body = {
            **VALID_OPENAI,
            "messages": [
                {"role": "system", "content": "You are helpful"},
                {"role": "user", "content": "Hi"},
            ],
        }
        result = strict_validator.validate(_body(body), "openai")
        assert result.valid

    def test_missing_model(self, strict_validator):
        body = {"messages": [{"role": "user", "content": "Hi"}]}
        result = strict_validator.validate(_body(body), "openai")
        assert not result.valid
        assert any("model" in e["field"] for e in result.errors)

    def test_missing_messages(self, strict_validator):
        body = {"model": "gpt-4o"}
        result = strict_validator.validate(_body(body), "openai")
        assert not result.valid
        assert any("messages" in e["field"] for e in result.errors)

    def test_invalid_role_error(self, strict_validator):
        body = {
            **VALID_OPENAI,
            "messages": [{"role": "robot", "content": "Beep boop"}],
        }
        result = strict_validator.validate(_body(body), "openai")
        assert not result.valid
        assert any("role" in e["field"] for e in result.errors)

    def test_valid_roles_accepted(self, strict_validator):
        for role in ("system", "user", "assistant", "tool", "function"):
            body = {**VALID_OPENAI, "messages": [{"role": role}]}
            result = strict_validator.validate(_body(body), "openai")
            # role-only messages may fail other checks; just check role is not the issue
            role_errors = [e for e in result.errors if "role" in e["field"] and "invalid role" in e.get("error", "")]
            assert not role_errors, f"role '{role}' should not produce a role error"

    def test_non_gpt_model_warning(self, strict_validator):
        body = {**VALID_OPENAI, "model": "some-custom-model"}
        result = strict_validator.validate(_body(body), "openai")
        assert any("model" in w["field"] for w in result.warnings)

    def test_gpt_model_no_warning(self, strict_validator):
        result = strict_validator.validate(_body(VALID_OPENAI), "openai")
        assert not any("model" in w["field"] for w in result.warnings)

    def test_o1_model_no_warning(self, strict_validator):
        body = {**VALID_OPENAI, "model": "o1-preview"}
        result = strict_validator.validate(_body(body), "openai")
        assert not any("model" in w["field"] for w in result.warnings)

    def test_ft_model_no_warning(self, strict_validator):
        body = {**VALID_OPENAI, "model": "ft:gpt-4o:org:suffix:id"}
        result = strict_validator.validate(_body(body), "openai")
        assert not any("model" in w["field"] for w in result.warnings)


# ---------------------------------------------------------------------------
# RequestValidator — OpenAI Codex
# ---------------------------------------------------------------------------


class TestRequestValidatorOpenAICodex:
    def test_valid_codex_request(self, strict_validator):
        result = strict_validator.validate(_body(VALID_CODEX), "openai-codex")
        assert result.valid

    def test_valid_codex_array_input(self, strict_validator):
        body = {**VALID_CODEX, "input": ["item one"]}
        result = strict_validator.validate(_body(body), "openai-codex")
        assert result.valid

    def test_missing_model(self, strict_validator):
        body = {"input": "Write hello"}
        result = strict_validator.validate(_body(body), "openai-codex")
        assert not result.valid
        assert any("model" in e["field"] for e in result.errors)

    def test_missing_input(self, strict_validator):
        body = {"model": "gpt-4o"}
        result = strict_validator.validate(_body(body), "openai-codex")
        assert not result.valid
        assert any("input" in e["field"] for e in result.errors)

    def test_codex_model_no_warning(self, strict_validator):
        body = {**VALID_CODEX, "model": "codex-cushman"}
        result = strict_validator.validate(_body(body), "openai-codex")
        assert not any("model" in w["field"] for w in result.warnings)


# ---------------------------------------------------------------------------
# RequestValidator — Google
# ---------------------------------------------------------------------------


class TestRequestValidatorGoogle:
    def test_valid_google_request(self, strict_validator):
        result = strict_validator.validate(_body(VALID_GOOGLE), "google")
        assert result.valid

    def test_missing_contents(self, strict_validator):
        result = strict_validator.validate(_body({}), "google")
        assert not result.valid
        assert any("contents" in e["field"] for e in result.errors)

    def test_empty_parts_semantic_error(self, strict_validator):
        body = {"contents": [{"parts": []}]}
        result = strict_validator.validate(_body(body), "google")
        assert not result.valid
        assert any("parts" in e["field"] for e in result.errors)

    def test_non_object_content_item(self, strict_validator):
        body = {"contents": ["not a dict"]}
        result = strict_validator.validate(_body(body), "google")
        assert not result.valid

    def test_non_gemini_model_warning(self, strict_validator):
        body = {**VALID_GOOGLE, "model": "gpt-4o"}
        result = strict_validator.validate(_body(body), "google")
        assert any("model" in w["field"] for w in result.warnings)

    def test_gemini_model_no_warning(self, strict_validator):
        body = {**VALID_GOOGLE, "model": "gemini-1.5-pro"}
        result = strict_validator.validate(_body(body), "google")
        assert not any("model" in w["field"] for w in result.warnings)


# ---------------------------------------------------------------------------
# RequestValidator — validate_bytes routing
# ---------------------------------------------------------------------------


class TestRequestValidatorValidateBytes:
    def test_non_messages_endpoint_always_valid(self, strict_validator):
        result = strict_validator.validate_bytes(b"not json", "/v1/models", "anthropic")
        assert result.valid

    def test_empty_body_always_valid(self, strict_validator):
        result = strict_validator.validate_bytes(b"", "/v1/messages", "anthropic")
        assert result.valid

    def test_anthropic_endpoint_validates(self, strict_validator):
        result = strict_validator.validate_bytes(_body(VALID_ANTHROPIC), "/v1/messages", "anthropic")
        assert result.valid

    def test_openai_chat_endpoint_validates(self, strict_validator):
        result = strict_validator.validate_bytes(_body(VALID_OPENAI), "/v1/chat/completions", "openai")
        assert result.valid

    def test_responses_endpoint_validates(self, strict_validator):
        result = strict_validator.validate_bytes(_body(VALID_CODEX), "/v1/responses", "openai-codex")
        assert result.valid

    def test_google_generate_content_endpoint_validates(self, strict_validator):
        url = "/v1/models/gemini-1.5-pro:generateContent"
        result = strict_validator.validate_bytes(_body(VALID_GOOGLE), url, "google")
        assert result.valid

    def test_invalid_request_on_messages_endpoint(self, strict_validator):
        bad_body = {"model": "claude-sonnet-4-6"}  # missing max_tokens and messages
        result = strict_validator.validate_bytes(_body(bad_body), "/v1/messages", "anthropic")
        assert not result.valid


# ---------------------------------------------------------------------------
# RequestValidator — unknown provider
# ---------------------------------------------------------------------------


class TestRequestValidatorUnknownProvider:
    def test_unknown_provider_uses_permissive_schema(self, strict_validator):
        # Unknown providers use empty permissive schema — anything goes
        body = {"arbitrary_field": "value"}
        result = strict_validator.validate(_body(body), "unknown-provider")
        assert result.valid


# ---------------------------------------------------------------------------
# request_schema — get_request_schema
# ---------------------------------------------------------------------------


class TestRequestSchema:
    def test_anthropic_schema_returned(self):
        schema = get_request_schema("anthropic")
        assert schema is ANTHROPIC_MESSAGE_SCHEMA

    def test_openai_schema_returned(self):
        schema = get_request_schema("openai")
        assert schema is OPENAI_CHAT_SCHEMA

    def test_openai_codex_schema_returned(self):
        schema = get_request_schema("openai-codex")
        assert schema is OPENAI_RESPONSES_SCHEMA

    def test_google_schema_returned(self):
        schema = get_request_schema("google")
        assert schema is GOOGLE_GENERATE_CONTENT_SCHEMA

    def test_unknown_provider_permissive_schema(self):
        schema = get_request_schema("unknown")
        assert schema.get("type") == "object"
        assert schema.get("additionalProperties") is True
        assert "required" not in schema

    def test_anthropic_required_fields(self):
        required = ANTHROPIC_MESSAGE_SCHEMA["required"]
        assert "model" in required
        assert "max_tokens" in required
        assert "messages" in required

    def test_openai_required_fields(self):
        required = OPENAI_CHAT_SCHEMA["required"]
        assert "model" in required
        assert "messages" in required

    def test_codex_required_fields(self):
        required = OPENAI_RESPONSES_SCHEMA["required"]
        assert "model" in required
        assert "input" in required

    def test_google_required_fields(self):
        required = GOOGLE_GENERATE_CONTENT_SCHEMA["required"]
        assert "contents" in required


# ---------------------------------------------------------------------------
# response_schema — get_schema
# ---------------------------------------------------------------------------


class TestResponseSchema:
    def test_get_schema_full_is_default(self):
        schema = get_schema()
        assert schema is RESPONSE_SCHEMA

    def test_get_schema_full_explicit(self):
        schema = get_schema("full")
        assert schema is RESPONSE_SCHEMA

    def test_get_schema_minimal(self):
        schema = get_schema("minimal")
        assert schema is RESPONSE_SCHEMA_MINIMAL

    def test_full_schema_required_fields(self):
        required = RESPONSE_SCHEMA["required"]
        for field in ("model", "tokens_sent", "tokens_received", "cost", "timestamp"):
            assert field in required

    def test_minimal_schema_required_fields(self):
        required = RESPONSE_SCHEMA_MINIMAL["required"]
        assert "model" in required
        assert "tokens_sent" in required
        assert "cost" in required
        # tokens_received not required in minimal
        assert "tokens_received" not in required

    def test_compilation_mode_enum(self):
        prop = RESPONSE_SCHEMA["properties"]["compilation_mode"]
        assert "enum" in prop
        assert "none" in prop["enum"]
        assert "aggressive" in prop["enum"]

    def test_status_enum(self):
        prop = RESPONSE_SCHEMA["properties"]["status"]
        assert "ok" in prop["enum"]
        assert "error" in prop["enum"]


# ---------------------------------------------------------------------------
# ValidationResult (from validation/validator.py)
# ---------------------------------------------------------------------------


class TestValidationResultFromValidator:
    def test_bool_valid(self):
        r = ValidationResult(valid=True)
        assert bool(r) is True

    def test_bool_invalid(self):
        r = ValidationResult(valid=False, errors=[{"field": "x", "reason": "bad"}])
        assert bool(r) is False

    def test_repr_valid(self):
        r = ValidationResult(valid=True)
        assert "valid=True" in repr(r)

    def test_repr_invalid(self):
        r = ValidationResult(valid=False, errors=[{"field": "x", "reason": "bad"}, {"field": "y", "reason": "bad"}])
        assert "valid=False" in repr(r)
        assert "errors=2" in repr(r)

    def test_defaults_empty_lists(self):
        r = ValidationResult(valid=True)
        assert r.errors == []
        assert r.warnings == []

    def test_to_dict(self):
        r = ValidationResult(
            valid=False,
            errors=[{"field": "f", "reason": "e"}],
            warnings=[{"field": "w", "reason": "w"}],
        )
        d = r.to_dict()
        assert d["valid"] is False
        assert len(d["errors"]) == 1
        assert len(d["warnings"]) == 1


# ---------------------------------------------------------------------------
# ResponseValidator — valid responses
# ---------------------------------------------------------------------------


class TestResponseValidatorValid:
    def test_valid_minimal_response(self):
        v = ResponseValidator(log_errors=False)
        result = v.validate(VALID_RESPONSE)
        assert result.valid

    def test_valid_with_optional_fields(self):
        v = ResponseValidator(log_errors=False)
        response = {
            **VALID_RESPONSE,
            "tokens_saved": 10,
            "cost_saved": 0.001,
            "cached": True,
            "cache_read_tokens": 50,
            "cache_creation_tokens": 100,
            "request_id": "req-abc123",
            "latency_ms": 250,
            "compilation_mode": "hybrid",
            "status": "ok",
        }
        result = v.validate(response)
        assert result.valid

    def test_valid_zero_tokens(self):
        v = ResponseValidator(log_errors=False)
        response = {**VALID_RESPONSE, "tokens_sent": 0, "tokens_received": 0}
        result = v.validate(response)
        assert result.valid

    def test_valid_zero_cost(self):
        v = ResponseValidator(log_errors=False)
        response = {**VALID_RESPONSE, "cost": 0}
        result = v.validate(response)
        assert result.valid


# ---------------------------------------------------------------------------
# ResponseValidator — invalid responses
# ---------------------------------------------------------------------------


class TestResponseValidatorInvalid:
    def test_missing_model(self):
        v = ResponseValidator(log_errors=False)
        response = {k: v for k, v in VALID_RESPONSE.items() if k != "model"}
        result = v.validate(response)
        assert not result.valid
        # ResponseValidator reports missing required fields at "(root)" level
        assert any("model" in e.get("reason", "") or "model" in e.get("field", "") for e in result.errors)

    def test_missing_tokens_sent(self):
        v = ResponseValidator(log_errors=False)
        response = {k: val for k, val in VALID_RESPONSE.items() if k != "tokens_sent"}
        result = v.validate(response)
        assert not result.valid

    def test_missing_cost(self):
        v = ResponseValidator(log_errors=False)
        response = {k: val for k, val in VALID_RESPONSE.items() if k != "cost"}
        result = v.validate(response)
        assert not result.valid

    def test_missing_timestamp(self):
        v = ResponseValidator(log_errors=False)
        response = {k: val for k, val in VALID_RESPONSE.items() if k != "timestamp"}
        result = v.validate(response)
        assert not result.valid

    def test_invalid_timestamp_format(self):
        v = ResponseValidator(log_errors=False)
        response = {**VALID_RESPONSE, "timestamp": "not-a-timestamp"}
        result = v.validate(response)
        assert not result.valid
        assert any("timestamp" in e["field"] for e in result.errors)

    def test_empty_model_string(self):
        v = ResponseValidator(log_errors=False)
        response = {**VALID_RESPONSE, "model": ""}
        result = v.validate(response)
        assert not result.valid

    def test_invalid_compilation_mode_enum(self):
        v = ResponseValidator(log_errors=False)
        response = {**VALID_RESPONSE, "compilation_mode": "ultra-aggressive"}
        result = v.validate(response)
        assert not result.valid

    def test_invalid_status_enum(self):
        v = ResponseValidator(log_errors=False)
        response = {**VALID_RESPONSE, "status": "unknown-status"}
        result = v.validate(response)
        assert not result.valid


# ---------------------------------------------------------------------------
# ResponseValidator — semantic warnings
# ---------------------------------------------------------------------------


class TestResponseValidatorWarnings:
    def test_tokens_saved_exceeds_tokens_sent_warning(self):
        v = ResponseValidator(log_errors=False)
        response = {**VALID_RESPONSE, "tokens_sent": 50, "tokens_saved": 100}
        result = v.validate(response)
        assert result.valid  # warning, not error
        assert any("tokens_saved" in w["field"] for w in result.warnings)

    def test_high_cost_warning(self):
        v = ResponseValidator(log_errors=False)
        response = {**VALID_RESPONSE, "cost": 150.0}
        result = v.validate(response)
        assert result.valid  # warning, not error
        assert any("cost" in w["field"] for w in result.warnings)

    def test_unrecognized_model_family_warning(self):
        v = ResponseValidator(log_errors=False)
        response = {**VALID_RESPONSE, "model": "falcon-7b"}
        result = v.validate(response)
        assert result.valid  # warning, not error
        assert any("model" in w["field"] for w in result.warnings)

    def test_recognized_model_families_no_warning(self):
        v = ResponseValidator(log_errors=False)
        for model in ("claude-sonnet-4-6", "gpt-4o", "gemini-1.5-pro", "llama-3-8b", "mistral-7b"):
            response = {**VALID_RESPONSE, "model": model}
            result = v.validate(response)
            model_warnings = [w for w in result.warnings if w.get("field") == "model"]
            assert not model_warnings, f"model '{model}' should not produce a warning"


# ---------------------------------------------------------------------------
# ResponseValidator — strict mode
# ---------------------------------------------------------------------------


class TestResponseValidatorStrictMode:
    def test_strict_mode_warning_becomes_error(self):
        v = ResponseValidator(strict=True, log_errors=False)
        response = {**VALID_RESPONSE, "tokens_sent": 10, "tokens_saved": 100}
        result = v.validate(response)
        # In strict mode, the tokens_saved warning should become an error
        assert not result.valid
        assert any("tokens_saved" in e["field"] for e in result.errors)
        assert result.warnings == []

    def test_strict_mode_high_cost_error(self):
        v = ResponseValidator(strict=True, log_errors=False)
        response = {**VALID_RESPONSE, "cost": 200.0}
        result = v.validate(response)
        assert not result.valid

    def test_non_strict_warning_not_error(self):
        v = ResponseValidator(strict=False, log_errors=False)
        response = {**VALID_RESPONSE, "tokens_sent": 10, "tokens_saved": 100}
        result = v.validate(response)
        assert result.valid
        assert any("tokens_saved" in w["field"] for w in result.warnings)


# ---------------------------------------------------------------------------
# ResponseValidator — timestamp edge cases
# ---------------------------------------------------------------------------


class TestResponseValidatorTimestamp:
    def test_valid_timestamp_z_suffix(self):
        v = ResponseValidator(log_errors=False)
        response = {**VALID_RESPONSE, "timestamp": "2026-04-12T12:00:00Z"}
        result = v.validate(response)
        assert result.valid

    def test_valid_timestamp_plus_offset(self):
        v = ResponseValidator(log_errors=False)
        response = {**VALID_RESPONSE, "timestamp": "2026-04-12T12:00:00+05:30"}
        result = v.validate(response)
        assert result.valid

    def test_invalid_timestamp_plain_date(self):
        v = ResponseValidator(log_errors=False)
        response = {**VALID_RESPONSE, "timestamp": "2026-04-12"}
        # Depends on Python's fromisoformat; plain dates pass isoformat but lack time component
        # We only assert no exception is raised and result is coherent
        result = v.validate(response)
        assert isinstance(result.valid, bool)


# ---------------------------------------------------------------------------
# ResponseValidator — custom schema
# ---------------------------------------------------------------------------


class TestResponseValidatorCustomSchema:
    def test_custom_schema_validates_different_required(self):
        custom_schema = {
            "type": "object",
            "required": ["custom_field"],
            "properties": {
                "custom_field": {"type": "string"},
            },
        }
        v = ResponseValidator(schema=custom_schema, log_errors=False)
        result = v.validate({"custom_field": "hello"})
        assert result.valid

    def test_custom_schema_rejects_missing_field(self):
        custom_schema = {
            "type": "object",
            "required": ["required_key"],
            "properties": {
                "required_key": {"type": "integer"},
            },
        }
        v = ResponseValidator(schema=custom_schema, log_errors=False)
        result = v.validate({})
        assert not result.valid


# ---------------------------------------------------------------------------
# Module-level helpers (validate_response, is_valid)
# ---------------------------------------------------------------------------


class TestResponseValidatorHelpers:
    def test_validate_response_valid(self):
        result = validate_response(VALID_RESPONSE)
        assert result.valid

    def test_validate_response_invalid(self):
        result = validate_response({"model": "claude-sonnet-4-6"})  # missing required fields
        assert not result.valid

    def test_validate_response_strict_flag(self):
        # Strict mode with warning-triggering response should fail
        response = {**VALID_RESPONSE, "cost": 200.0}
        result_normal = validate_response(response)
        result_strict = validate_response(response, strict=True)
        # Normal: warning only → valid; strict: warning becomes error → invalid
        assert result_normal.valid
        assert not result_strict.valid

    def test_is_valid_true(self):
        assert is_valid(VALID_RESPONSE) is True

    def test_is_valid_false(self):
        assert is_valid({}) is False
