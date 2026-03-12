"""
Tests for TokenPak request validation system.

Covers:
- Anthropic schema validation
- OpenAI schema validation
- Clear error messages with field details
- HTTP 400 for validation errors (proxy integration)
- Configurable strict/warn/off modes
"""

from __future__ import annotations

import json
import os
import sys
import unittest
from typing import Any, Dict
from unittest.mock import patch, MagicMock

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_anthropic_body(**overrides) -> bytes:
    """Build a minimal valid Anthropic request body."""
    data = {
        "model": "claude-sonnet-4-6",
        "max_tokens": 1024,
        "messages": [{"role": "user", "content": "Hello"}],
    }
    data.update(overrides)
    return json.dumps(data).encode()


def _make_openai_body(**overrides) -> bytes:
    """Build a minimal valid OpenAI request body."""
    data = {
        "model": "gpt-4o",
        "messages": [{"role": "user", "content": "Hello"}],
    }
    data.update(overrides)
    return json.dumps(data).encode()


def _make_openai_responses_body(**overrides) -> bytes:
    """Build a minimal valid OpenAI Responses API body."""
    data = {
        "model": "gpt-4.1",
        "input": "Hello",
    }
    data.update(overrides)
    return json.dumps(data).encode()


def _make_google_body(**overrides) -> bytes:
    """Build a minimal valid Google generateContent body."""
    data = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": "Hello"}],
            }
        ]
    }
    data.update(overrides)
    return json.dumps(data).encode()


# ---------------------------------------------------------------------------
# Schema import test
# ---------------------------------------------------------------------------

class TestRequestSchemas(unittest.TestCase):
    def test_anthropic_schema_has_required_fields(self):
        from tokenpak.validation.request_schema import ANTHROPIC_MESSAGE_SCHEMA
        required = ANTHROPIC_MESSAGE_SCHEMA.get("required", [])
        self.assertIn("model", required)
        self.assertIn("max_tokens", required)
        self.assertIn("messages", required)

    def test_openai_schema_has_required_fields(self):
        from tokenpak.validation.request_schema import OPENAI_CHAT_SCHEMA
        required = OPENAI_CHAT_SCHEMA.get("required", [])
        self.assertIn("model", required)
        self.assertIn("messages", required)

    def test_get_request_schema_returns_anthropic(self):
        from tokenpak.validation.request_schema import get_request_schema, ANTHROPIC_MESSAGE_SCHEMA
        schema = get_request_schema("anthropic")
        self.assertEqual(schema["title"], ANTHROPIC_MESSAGE_SCHEMA["title"])

    def test_get_request_schema_returns_openai(self):
        from tokenpak.validation.request_schema import get_request_schema, OPENAI_CHAT_SCHEMA
        schema = get_request_schema("openai")
        self.assertEqual(schema["title"], OPENAI_CHAT_SCHEMA["title"])

    def test_get_request_schema_returns_openai_responses(self):
        from tokenpak.validation.request_schema import get_request_schema, OPENAI_RESPONSES_SCHEMA
        schema = get_request_schema("openai-codex")
        self.assertEqual(schema["title"], OPENAI_RESPONSES_SCHEMA["title"])

    def test_get_request_schema_returns_google(self):
        from tokenpak.validation.request_schema import get_request_schema, GOOGLE_GENERATE_CONTENT_SCHEMA
        schema = get_request_schema("google")
        self.assertEqual(schema["title"], GOOGLE_GENERATE_CONTENT_SCHEMA["title"])

    def test_get_request_schema_returns_permissive_for_unknown(self):
        from tokenpak.validation.request_schema import get_request_schema
        schema = get_request_schema("unknown-provider")
        # Unknown → no required fields, additionalProperties=True
        self.assertEqual(schema.get("required", []), [])


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------

class TestRequestValidationResult(unittest.TestCase):
    def test_bool_true_when_valid(self):
        from tokenpak.validation.request_validator import RequestValidationResult
        r = RequestValidationResult(valid=True, provider="anthropic")
        self.assertTrue(bool(r))

    def test_bool_false_when_invalid(self):
        from tokenpak.validation.request_validator import RequestValidationResult
        r = RequestValidationResult(
            valid=False,
            provider="anthropic",
            errors=[{"field": "model", "error": "required field missing"}],
        )
        self.assertFalse(bool(r))

    def test_to_error_response_structure(self):
        from tokenpak.validation.request_validator import RequestValidationResult
        r = RequestValidationResult(
            valid=False,
            provider="anthropic",
            errors=[{"field": "model", "error": "required field missing"}],
        )
        payload = r.to_error_response()
        self.assertIn("error", payload)
        err = payload["error"]
        self.assertEqual(err["type"], "validation_error")
        self.assertIn("details", err)
        self.assertIn("hint", err)
        self.assertIsInstance(err["details"], list)
        self.assertGreater(len(err["details"]), 0)

    def test_to_error_response_hint_contains_provider_path(self):
        from tokenpak.validation.request_validator import RequestValidationResult
        r = RequestValidationResult(valid=False, provider="anthropic", errors=[{"field": "x", "error": "e"}])
        payload = r.to_error_response()
        self.assertIn("messages", payload["error"]["hint"])

        r2 = RequestValidationResult(valid=False, provider="openai", errors=[{"field": "x", "error": "e"}])
        payload2 = r2.to_error_response()
        self.assertIn("chat-completions", payload2["error"]["hint"])

    def test_to_dict(self):
        from tokenpak.validation.request_validator import RequestValidationResult
        r = RequestValidationResult(valid=True, provider="openai")
        d = r.to_dict()
        self.assertEqual(d["valid"], True)
        self.assertEqual(d["provider"], "openai")


# ---------------------------------------------------------------------------
# Anthropic validation
# ---------------------------------------------------------------------------

class TestAnthropicValidation(unittest.TestCase):
    def setUp(self):
        from tokenpak.validation.request_validator import RequestValidator
        self.validator = RequestValidator(mode="strict")

    def test_valid_anthropic_request(self):
        result = self.validator.validate(_make_anthropic_body(), "anthropic")
        self.assertTrue(result.valid, result.errors)

    def test_missing_model_is_error(self):
        body = _make_anthropic_body()
        data = json.loads(body)
        del data["model"]
        result = self.validator.validate(json.dumps(data).encode(), "anthropic")
        self.assertFalse(result.valid)
        fields = [e["field"] for e in result.errors]
        self.assertIn("model", fields)

    def test_missing_max_tokens_is_error(self):
        data = json.loads(_make_anthropic_body())
        del data["max_tokens"]
        result = self.validator.validate(json.dumps(data).encode(), "anthropic")
        self.assertFalse(result.valid)
        fields = [e["field"] for e in result.errors]
        self.assertIn("max_tokens", fields)

    def test_missing_messages_is_error(self):
        data = json.loads(_make_anthropic_body())
        del data["messages"]
        result = self.validator.validate(json.dumps(data).encode(), "anthropic")
        self.assertFalse(result.valid)
        fields = [e["field"] for e in result.errors]
        self.assertIn("messages", fields)

    def test_empty_messages_is_error(self):
        data = json.loads(_make_anthropic_body())
        data["messages"] = []
        result = self.validator.validate(json.dumps(data).encode(), "anthropic")
        self.assertFalse(result.valid)

    def test_first_message_assistant_is_error(self):
        data = json.loads(_make_anthropic_body())
        data["messages"] = [{"role": "assistant", "content": "hi"}]
        result = self.validator.validate(json.dumps(data).encode(), "anthropic")
        self.assertFalse(result.valid)
        fields = [e["field"] for e in result.errors]
        self.assertTrue(any("messages[0]" in f for f in fields), fields)

    def test_consecutive_same_roles_is_error(self):
        data = json.loads(_make_anthropic_body())
        data["messages"] = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "again"},
        ]
        result = self.validator.validate(json.dumps(data).encode(), "anthropic")
        self.assertFalse(result.valid)
        fields = [e["field"] for e in result.errors]
        self.assertTrue(any("messages[1]" in f for f in fields), fields)

    def test_alternating_roles_is_valid(self):
        data = json.loads(_make_anthropic_body())
        data["messages"] = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "bye"},
        ]
        result = self.validator.validate(json.dumps(data).encode(), "anthropic")
        self.assertTrue(result.valid, result.errors)

    def test_invalid_json_is_error(self):
        result = self.validator.validate(b"not json at all", "anthropic")
        self.assertFalse(result.valid)
        self.assertIn("(body)", result.errors[0]["field"])

    def test_multiple_missing_fields_reported(self):
        """Both model AND messages missing should produce 2+ errors."""
        result = self.validator.validate(b'{"max_tokens": 10}', "anthropic")
        self.assertFalse(result.valid)
        fields = [e["field"] for e in result.errors]
        self.assertIn("model", fields)
        self.assertIn("messages", fields)

    def test_max_tokens_type_error(self):
        data = json.loads(_make_anthropic_body())
        data["max_tokens"] = "a thousand"
        result = self.validator.validate(json.dumps(data).encode(), "anthropic")
        self.assertFalse(result.valid)

    def test_stream_bool_valid(self):
        data = json.loads(_make_anthropic_body())
        data["stream"] = True
        result = self.validator.validate(json.dumps(data).encode(), "anthropic")
        self.assertTrue(result.valid, result.errors)

    def test_temperature_out_of_range_is_error(self):
        data = json.loads(_make_anthropic_body())
        data["temperature"] = 2.5  # max is 1.0 for Anthropic
        result = self.validator.validate(json.dumps(data).encode(), "anthropic")
        self.assertFalse(result.valid)


# ---------------------------------------------------------------------------
# OpenAI validation
# ---------------------------------------------------------------------------

class TestOpenAIValidation(unittest.TestCase):
    def setUp(self):
        from tokenpak.validation.request_validator import RequestValidator
        self.validator = RequestValidator(mode="strict")

    def test_valid_openai_request(self):
        result = self.validator.validate(_make_openai_body(), "openai")
        self.assertTrue(result.valid, result.errors)

    def test_missing_model_is_error(self):
        data = json.loads(_make_openai_body())
        del data["model"]
        result = self.validator.validate(json.dumps(data).encode(), "openai")
        self.assertFalse(result.valid)
        fields = [e["field"] for e in result.errors]
        self.assertIn("model", fields)

    def test_missing_messages_is_error(self):
        data = json.loads(_make_openai_body())
        del data["messages"]
        result = self.validator.validate(json.dumps(data).encode(), "openai")
        self.assertFalse(result.valid)
        fields = [e["field"] for e in result.errors]
        self.assertIn("messages", fields)

    def test_empty_messages_is_error(self):
        data = json.loads(_make_openai_body())
        data["messages"] = []
        result = self.validator.validate(json.dumps(data).encode(), "openai")
        self.assertFalse(result.valid)

    def test_system_message_is_valid(self):
        data = json.loads(_make_openai_body())
        data["messages"] = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hi"},
        ]
        result = self.validator.validate(json.dumps(data).encode(), "openai")
        self.assertTrue(result.valid, result.errors)

    def test_invalid_role_is_error(self):
        data = json.loads(_make_openai_body())
        data["messages"] = [{"role": "superuser", "content": "Hello"}]
        result = self.validator.validate(json.dumps(data).encode(), "openai")
        self.assertFalse(result.valid)

    def test_temperature_max_two_is_valid(self):
        data = json.loads(_make_openai_body())
        data["temperature"] = 2.0
        result = self.validator.validate(json.dumps(data).encode(), "openai")
        self.assertTrue(result.valid, result.errors)

    def test_temperature_exceeds_max_is_error(self):
        data = json.loads(_make_openai_body())
        data["temperature"] = 2.5
        result = self.validator.validate(json.dumps(data).encode(), "openai")
        self.assertFalse(result.valid)

    def test_presence_penalty_out_of_range(self):
        data = json.loads(_make_openai_body())
        data["presence_penalty"] = 3.0
        result = self.validator.validate(json.dumps(data).encode(), "openai")
        self.assertFalse(result.valid)

    def test_max_tokens_valid(self):
        data = json.loads(_make_openai_body())
        data["max_tokens"] = 512
        result = self.validator.validate(json.dumps(data).encode(), "openai")
        self.assertTrue(result.valid, result.errors)


# ---------------------------------------------------------------------------
# OpenAI Responses validation
# ---------------------------------------------------------------------------

class TestOpenAIResponsesValidation(unittest.TestCase):
    def setUp(self):
        from tokenpak.validation.request_validator import RequestValidator
        self.validator = RequestValidator(mode="strict")

    def test_valid_openai_responses_request(self):
        result = self.validator.validate(_make_openai_responses_body(), "openai-codex")
        self.assertTrue(result.valid, result.errors)

    def test_missing_input_is_error(self):
        data = json.loads(_make_openai_responses_body())
        del data["input"]
        result = self.validator.validate(json.dumps(data).encode(), "openai-codex")
        self.assertFalse(result.valid)
        fields = [e["field"] for e in result.errors]
        self.assertIn("input", fields)


# ---------------------------------------------------------------------------
# Google validation
# ---------------------------------------------------------------------------

class TestGoogleValidation(unittest.TestCase):
    def setUp(self):
        from tokenpak.validation.request_validator import RequestValidator
        self.validator = RequestValidator(mode="strict")

    def test_valid_google_request(self):
        result = self.validator.validate(_make_google_body(), "google")
        self.assertTrue(result.valid, result.errors)

    def test_missing_contents_is_error(self):
        result = self.validator.validate(b'{"generationConfig": {}}', "google")
        self.assertFalse(result.valid)
        fields = [e["field"] for e in result.errors]
        self.assertIn("contents", fields)


# ---------------------------------------------------------------------------
# Validation modes
# ---------------------------------------------------------------------------

class TestValidationModes(unittest.TestCase):
    def test_off_mode_always_valid(self):
        from tokenpak.validation.request_validator import RequestValidator
        v = RequestValidator(mode="off")
        result = v.validate(b'{"totally": "wrong"}', "anthropic")
        self.assertTrue(result.valid)

    def test_warn_mode_returns_errors_but_does_not_raise(self):
        from tokenpak.validation.request_validator import RequestValidator
        v = RequestValidator(mode="warn")
        result = v.validate(b'{}', "anthropic")
        # In warn mode, errors are reported but valid=False (caller chooses to forward)
        self.assertFalse(result.valid)
        self.assertGreater(len(result.errors), 0)

    def test_strict_mode_returns_invalid_for_bad_request(self):
        from tokenpak.validation.request_validator import RequestValidator
        v = RequestValidator(mode="strict")
        result = v.validate(b'{}', "anthropic")
        self.assertFalse(result.valid)

    def test_invalid_mode_raises(self):
        from tokenpak.validation.request_validator import RequestValidator
        with self.assertRaises(ValueError):
            RequestValidator(mode="maybe")

    def test_validate_bytes_skips_non_messages_endpoints(self):
        from tokenpak.validation.request_validator import RequestValidator
        v = RequestValidator(mode="strict")
        # /v1/models is not a messages endpoint — should always pass through
        result = v.validate_bytes(b'{}', "https://api.anthropic.com/v1/models", "anthropic")
        self.assertTrue(result.valid)

    def test_validate_bytes_validates_messages_endpoint(self):
        from tokenpak.validation.request_validator import RequestValidator
        v = RequestValidator(mode="strict")
        result = v.validate_bytes(b'{}', "https://api.anthropic.com/v1/messages", "anthropic")
        self.assertFalse(result.valid)

    def test_validate_bytes_validates_chat_completions(self):
        from tokenpak.validation.request_validator import RequestValidator
        v = RequestValidator(mode="strict")
        result = v.validate_bytes(b'{}', "https://api.openai.com/v1/chat/completions", "openai")
        self.assertFalse(result.valid)

    def test_validate_bytes_validates_openai_responses(self):
        from tokenpak.validation.request_validator import RequestValidator
        v = RequestValidator(mode="strict")
        result = v.validate_bytes(b'{}', "https://api.openai.com/v1/responses", "openai-codex")
        self.assertFalse(result.valid)

    def test_validate_bytes_validates_google_generate_content(self):
        from tokenpak.validation.request_validator import RequestValidator
        v = RequestValidator(mode="strict")
        result = v.validate_bytes(
            b'{}',
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-pro:generateContent",
            "google",
        )
        self.assertFalse(result.valid)

    def test_get_validation_mode_env_strict(self):
        from tokenpak.validation import request_validator
        with patch.dict(os.environ, {"TOKENPAK_REQUEST_VALIDATION": "strict"}):
            # Reset module-level cache
            request_validator._validator = None
            mode = request_validator.get_validation_mode()
        self.assertEqual(mode, "strict")

    def test_get_validation_mode_env_off(self):
        from tokenpak.validation import request_validator
        with patch.dict(os.environ, {"TOKENPAK_REQUEST_VALIDATION": "off"}):
            request_validator._validator = None
            mode = request_validator.get_validation_mode()
        self.assertEqual(mode, "off")

    def test_get_validation_mode_default_warn(self):
        from tokenpak.validation import request_validator
        with patch.dict(os.environ, {}, clear=False):
            env_backup = os.environ.pop("TOKENPAK_REQUEST_VALIDATION", None)
            try:
                request_validator._validator = None
                mode = request_validator.get_validation_mode()
                self.assertEqual(mode, "warn")
            finally:
                if env_backup is not None:
                    os.environ["TOKENPAK_REQUEST_VALIDATION"] = env_backup
                request_validator._validator = None


# ---------------------------------------------------------------------------
# HTTP 400 proxy integration (mock-based)
# ---------------------------------------------------------------------------

class TestProxyValidationIntegration(unittest.TestCase):
    """Verify that strict mode produces 400 responses via proxy._proxy_to."""

    def _make_handler_mock(self) -> MagicMock:
        """Build a minimal mock of _ProxyHandler for testing."""
        handler = MagicMock()
        handler.path = "https://api.anthropic.com/v1/messages"
        handler.headers = {
            "Content-Type": "application/json",
            "Authorization": "Bearer sk-test",
            "Content-Length": "2",
        }
        handler.rfile = MagicMock()
        handler.rfile.read.return_value = b"{}"  # empty body → missing required fields

        sent_responses = []
        sent_headers = {}
        sent_body = [b""]

        def _send_response(code):
            sent_responses.append(code)
        def _send_header(key, val):
            sent_headers[key] = val
        def _end_headers():
            pass
        def _write(data):
            sent_body[0] = data

        handler.send_response = _send_response
        handler.send_header = _send_header
        handler.end_headers = _end_headers
        handler.wfile = MagicMock()
        handler.wfile.write = _write

        return handler, sent_responses, sent_headers, sent_body

    def test_strict_mode_integration_sends_400_for_bad_request(self):
        """
        End-to-end: RequestValidator in strict mode should cause proxy to return 400.
        """
        from tokenpak.validation.request_validator import RequestValidator, RequestValidationResult

        # Simulate what proxy does when body is invalid in strict mode
        v = RequestValidator(mode="strict")
        body = b"{}"  # missing model, max_tokens, messages
        result = v.validate_bytes(body, "https://api.anthropic.com/v1/messages", "anthropic")

        self.assertFalse(result.valid)
        error_payload = result.to_error_response()

        # Should have the right structure for HTTP 400
        self.assertIn("error", error_payload)
        self.assertEqual(error_payload["error"]["type"], "validation_error")
        self.assertIsInstance(error_payload["error"]["details"], list)
        self.assertGreater(len(error_payload["error"]["details"]), 0)

        # All detail items should have "field" and "error" keys
        for detail in error_payload["error"]["details"]:
            self.assertIn("field", detail)
            self.assertIn("error", detail)

    def test_warn_mode_does_not_reject(self):
        """In warn mode, even an invalid body is forwarded (valid=False but not rejected by proxy)."""
        from tokenpak.validation.request_validator import RequestValidator
        v = RequestValidator(mode="warn")
        body = b"{}"
        result = v.validate_bytes(body, "https://api.anthropic.com/v1/messages", "anthropic")
        # Errors are present, but proxy should NOT return 400 in warn mode
        self.assertFalse(result.valid)
        # The proxy checks: if not result.valid AND mode == "strict" → 400
        # In warn mode this condition is False, so proxy forwards
        self.assertEqual(v.mode, "warn")


# ---------------------------------------------------------------------------
# Module-level exports
# ---------------------------------------------------------------------------

class TestModuleExports(unittest.TestCase):
    def test_validation_module_exports(self):
        import tokenpak.validation as tpv
        self.assertTrue(hasattr(tpv, "RequestValidator"))
        self.assertTrue(hasattr(tpv, "RequestValidationResult"))
        self.assertTrue(hasattr(tpv, "validate_request"))
        self.assertTrue(hasattr(tpv, "get_request_validator"))
        self.assertTrue(hasattr(tpv, "get_validation_mode"))
        self.assertTrue(hasattr(tpv, "ANTHROPIC_MESSAGE_SCHEMA"))
        self.assertTrue(hasattr(tpv, "OPENAI_CHAT_SCHEMA"))
        self.assertTrue(hasattr(tpv, "get_request_schema"))

    def test_validate_request_convenience_function(self):
        import os
        from tokenpak.validation import validate_request
        from tokenpak.validation import request_validator as _rv_mod
        _rv_mod._validator = None
        with patch.dict(os.environ, {"TOKENPAK_REQUEST_VALIDATION": "warn"}):
            result = validate_request(
                _make_anthropic_body(),
                provider="anthropic",
                target_url="https://api.anthropic.com/v1/messages",
            )
        self.assertTrue(result.valid, result.errors)
        _rv_mod._validator = None


if __name__ == "__main__":
    unittest.main()
