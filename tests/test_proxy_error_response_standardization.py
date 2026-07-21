"""Tests for PROXY-ERR-01: canonical upstream error response normalization.

Covers:
  - normalize_upstream_error() for each provider's native 4xx/5xx format
  - Canonical envelope structure (required keys present)
  - Status-to-error-type mapping
  - Message extraction from provider-specific bodies
  - Fallback behavior for non-JSON and unknown provider bodies
"""

from __future__ import annotations

import json

import pytest

from tokenpak.proxy.error_response import (
    STATEFUL_API_UNSUPPORTED,
    STATEFUL_API_UNSUPPORTED_STATUS,
    STATEFUL_SURFACES_REGISTRY,
    _extract_message_from_provider_body,
    _status_to_error_type,
    build_stateful_api_unsupported_error,
    normalize_upstream_error,
)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _parse(body: bytes) -> dict:
    """Parse normalized error bytes as JSON."""
    return json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------------------
# Canonical envelope structure
# ---------------------------------------------------------------------------

class TestCanonicalEnvelopeStructure:
    """Verify the canonical error envelope has all required keys."""

    def test_envelope_has_error_key(self):
        body = normalize_upstream_error(401, b"{}", "anthropic")
        data = _parse(body)
        assert "error" in data

    def test_error_has_type(self):
        data = _parse(normalize_upstream_error(401, b"{}", "anthropic"))
        assert "type" in data["error"]

    def test_error_has_message(self):
        data = _parse(normalize_upstream_error(401, b"{}", "anthropic"))
        assert "message" in data["error"]
        assert isinstance(data["error"]["message"], str)
        assert data["error"]["message"]  # non-empty

    def test_error_has_provider(self):
        data = _parse(normalize_upstream_error(401, b"{}", "openai"))
        assert data["error"]["provider"] == "openai"

    def test_error_has_upstream_status(self):
        data = _parse(normalize_upstream_error(429, b"{}", "openai"))
        assert data["error"]["upstream_status"] == 429

    def test_output_is_valid_utf8_json(self):
        out = normalize_upstream_error(500, b"{}", "google")
        parsed = json.loads(out.decode("utf-8"))
        assert isinstance(parsed, dict)


# ---------------------------------------------------------------------------
# Stateful provider API unsupported payload
# ---------------------------------------------------------------------------

class TestStatefulApiUnsupportedError:
    """Verify the typed payload for explicitly unsupported provider stateful APIs."""

    def test_error_code_constant_is_stable(self):
        assert STATEFUL_API_UNSUPPORTED == "stateful_api_unsupported"

    def test_http_status_constant_is_422(self):
        assert STATEFUL_API_UNSUPPORTED_STATUS == 422

    def test_payload_shape(self):
        body = build_stateful_api_unsupported_error(
            "provider-managed conversation memory",
            "Use local PAK memory or disable provider-managed memory for this request.",
        )
        data = _parse(body)
        assert data == {
            "tokenpak_error_type": "stateful_api_unsupported",
            "surface": "provider-managed conversation memory",
            "support_state": "explicitly_unsupported",
            "remediation": "Use local PAK memory or disable provider-managed memory for this request.",
            "registry_link": (
                f"{STATEFUL_SURFACES_REGISTRY}#provider-managed-conversation-memory"
            ),
        }

    def test_custom_registry_link(self):
        body = build_stateful_api_unsupported_error(
            "real-time websocket session IDs",
            "Use standard request/response APIs.",
            registry_link="tokenpak/registry/schemas/stateful_surfaces.yaml#realtime",
        )
        data = _parse(body)
        assert data["registry_link"] == "tokenpak/registry/schemas/stateful_surfaces.yaml#realtime"

    @pytest.mark.parametrize("surface, remediation", [("", "Use another API."), ("memory", "")])
    def test_requires_surface_and_remediation(self, surface: str, remediation: str):
        with pytest.raises(ValueError):
            build_stateful_api_unsupported_error(surface, remediation)


# ---------------------------------------------------------------------------
# Status-to-error-type mapping
# ---------------------------------------------------------------------------

class TestStatusToErrorType:
    def test_401_maps_to_authentication_error(self):
        assert _status_to_error_type(401) == "authentication_error"

    def test_403_maps_to_permission_error(self):
        assert _status_to_error_type(403) == "permission_error"

    def test_429_maps_to_rate_limit_error(self):
        assert _status_to_error_type(429) == "rate_limit_error"

    def test_400_maps_to_invalid_request_error(self):
        assert _status_to_error_type(400) == "invalid_request_error"

    def test_404_maps_to_not_found_error(self):
        assert _status_to_error_type(404) == "not_found_error"

    def test_500_maps_to_upstream_error(self):
        assert _status_to_error_type(500) == "upstream_error"

    def test_503_maps_to_service_unavailable(self):
        assert _status_to_error_type(503) == "service_unavailable"

    def test_504_maps_to_upstream_timeout(self):
        assert _status_to_error_type(504) == "upstream_timeout"

    def test_422_maps_to_client_error(self):
        assert _status_to_error_type(422) == "client_error"

    def test_502_maps_to_upstream_error(self):
        assert _status_to_error_type(502) == "upstream_error"

    def test_599_maps_to_upstream_error(self):
        assert _status_to_error_type(599) == "upstream_error"


# ---------------------------------------------------------------------------
# Anthropic adapter — error format: {"type": "error", "error": {"type": ..., "message": ...}}
# ---------------------------------------------------------------------------

class TestAnthropicErrorNormalization:
    """Anthropic upstream 4xx/5xx error bodies use the Anthropic Messages error format."""

    PROVIDER = "anthropic"

    def _make_body(self, err_type: str, message: str) -> bytes:
        return json.dumps({
            "type": "error",
            "error": {"type": err_type, "message": message},
        }).encode()

    def test_401_extracts_message(self):
        body = self._make_body("authentication_error", "Invalid API key.")
        data = _parse(normalize_upstream_error(401, body, self.PROVIDER))
        assert data["error"]["message"] == "Invalid API key."

    def test_401_sets_type_authentication_error(self):
        body = self._make_body("authentication_error", "Bad key.")
        data = _parse(normalize_upstream_error(401, body, self.PROVIDER))
        assert data["error"]["type"] == "authentication_error"

    def test_429_extracts_message(self):
        body = self._make_body("rate_limit_error", "Rate limit exceeded.")
        data = _parse(normalize_upstream_error(429, body, self.PROVIDER))
        assert data["error"]["message"] == "Rate limit exceeded."
        assert data["error"]["type"] == "rate_limit_error"

    def test_500_sets_upstream_error_type(self):
        body = self._make_body("api_error", "Internal server error.")
        data = _parse(normalize_upstream_error(500, body, self.PROVIDER))
        assert data["error"]["type"] == "upstream_error"
        assert data["error"]["upstream_status"] == 500

    def test_provider_is_preserved(self):
        body = self._make_body("authentication_error", "Bad key.")
        data = _parse(normalize_upstream_error(401, body, self.PROVIDER))
        assert data["error"]["provider"] == "anthropic"


# ---------------------------------------------------------------------------
# OpenAI adapter — error format: {"error": {"message": ..., "type": ..., "code": ...}}
# ---------------------------------------------------------------------------

class TestOpenAIErrorNormalization:
    """OpenAI upstream 4xx/5xx errors use the OpenAI error object format."""

    PROVIDER = "openai"

    def _make_body(self, message: str, err_type: str = "invalid_request_error", code=None) -> bytes:
        err: dict = {"message": message, "type": err_type}
        if code is not None:
            err["code"] = code
        return json.dumps({"error": err}).encode()

    def test_401_extracts_message(self):
        body = self._make_body("Incorrect API key provided.")
        data = _parse(normalize_upstream_error(401, body, self.PROVIDER))
        assert data["error"]["message"] == "Incorrect API key provided."

    def test_429_extracts_message(self):
        body = self._make_body("Rate limit reached for model.", "rate_limit_exceeded")
        data = _parse(normalize_upstream_error(429, body, self.PROVIDER))
        assert data["error"]["message"] == "Rate limit reached for model."

    def test_400_sets_invalid_request_error_type(self):
        body = self._make_body("Bad request.")
        data = _parse(normalize_upstream_error(400, body, self.PROVIDER))
        assert data["error"]["type"] == "invalid_request_error"

    def test_500_sets_upstream_error_type(self):
        body = self._make_body("The server had an error.", "api_error")
        data = _parse(normalize_upstream_error(500, body, self.PROVIDER))
        assert data["error"]["type"] == "upstream_error"
        assert data["error"]["upstream_status"] == 500

    def test_provider_is_openai(self):
        body = self._make_body("Bad key.")
        data = _parse(normalize_upstream_error(401, body, self.PROVIDER))
        assert data["error"]["provider"] == "openai"

    def test_403_extracts_message(self):
        body = self._make_body("Your account is not allowed.")
        data = _parse(normalize_upstream_error(403, body, self.PROVIDER))
        assert data["error"]["message"] == "Your account is not allowed."
        assert data["error"]["type"] == "permission_error"


# ---------------------------------------------------------------------------
# Google/Gemini adapter — error format: {"error": {"code": N, "message": ..., "status": ...}}
# ---------------------------------------------------------------------------

class TestGoogleErrorNormalization:
    """Google upstream 4xx/5xx errors use the Google APIs error object format."""

    PROVIDER = "google"

    def _make_body(self, code: int, message: str, status: str = "INVALID_ARGUMENT") -> bytes:
        return json.dumps({
            "error": {"code": code, "message": message, "status": status}
        }).encode()

    def test_401_extracts_message(self):
        body = self._make_body(401, "Request had invalid authentication credentials.")
        data = _parse(normalize_upstream_error(401, body, self.PROVIDER))
        assert data["error"]["message"] == "Request had invalid authentication credentials."

    def test_429_extracts_message(self):
        body = self._make_body(429, "Resource has been exhausted.", "RESOURCE_EXHAUSTED")
        data = _parse(normalize_upstream_error(429, body, self.PROVIDER))
        assert data["error"]["message"] == "Resource has been exhausted."
        assert data["error"]["type"] == "rate_limit_error"

    def test_400_sets_invalid_request_error_type(self):
        body = self._make_body(400, "Invalid value.")
        data = _parse(normalize_upstream_error(400, body, self.PROVIDER))
        assert data["error"]["type"] == "invalid_request_error"

    def test_500_sets_upstream_error_type(self):
        body = self._make_body(500, "Internal error.", "INTERNAL")
        data = _parse(normalize_upstream_error(500, body, self.PROVIDER))
        assert data["error"]["type"] == "upstream_error"
        assert data["error"]["upstream_status"] == 500

    def test_provider_is_google(self):
        body = self._make_body(401, "Bad credentials.")
        data = _parse(normalize_upstream_error(401, body, self.PROVIDER))
        assert data["error"]["provider"] == "google"

    def test_403_permission_error(self):
        body = self._make_body(403, "Permission denied.", "PERMISSION_DENIED")
        data = _parse(normalize_upstream_error(403, body, self.PROVIDER))
        assert data["error"]["type"] == "permission_error"


# ---------------------------------------------------------------------------
# Grok adapter (xAI) — OpenAI-compatible error format
# ---------------------------------------------------------------------------

class TestGrokErrorNormalization:
    """Grok uses OpenAI-compatible error format."""

    PROVIDER = "groq"

    def _make_body(self, message: str) -> bytes:
        return json.dumps({"error": {"message": message, "type": "invalid_request_error"}}).encode()

    def test_401_extracts_message(self):
        body = self._make_body("Invalid API key.")
        data = _parse(normalize_upstream_error(401, body, self.PROVIDER))
        assert data["error"]["message"] == "Invalid API key."

    def test_provider_is_groq(self):
        body = self._make_body("Bad key.")
        data = _parse(normalize_upstream_error(401, body, self.PROVIDER))
        assert data["error"]["provider"] == "groq"

    def test_429_rate_limit(self):
        body = self._make_body("Rate limit exceeded.")
        data = _parse(normalize_upstream_error(429, body, self.PROVIDER))
        assert data["error"]["type"] == "rate_limit_error"


# ---------------------------------------------------------------------------
# Passthrough adapter — arbitrary upstream bodies
# ---------------------------------------------------------------------------

class TestPassthroughErrorNormalization:
    """Passthrough can return arbitrary error bodies; canonical fallback applies."""

    PROVIDER = "unknown"

    def test_non_json_body_produces_fallback_message(self):
        data = _parse(normalize_upstream_error(502, b"Bad Gateway", self.PROVIDER))
        assert "unknown" in data["error"]["message"]
        assert data["error"]["upstream_status"] == 502

    def test_empty_body_produces_fallback_message(self):
        data = _parse(normalize_upstream_error(500, b"", self.PROVIDER))
        assert data["error"]["message"]
        assert data["error"]["upstream_status"] == 500

    def test_generic_json_without_message_uses_fallback(self):
        body = json.dumps({"status": "error"}).encode()
        data = _parse(normalize_upstream_error(503, body, self.PROVIDER))
        assert data["error"]["type"] == "service_unavailable"
        assert data["error"]["provider"] == "unknown"

    def test_top_level_message_field_extracted(self):
        body = json.dumps({"message": "Service down for maintenance."}).encode()
        data = _parse(normalize_upstream_error(503, body, self.PROVIDER))
        assert data["error"]["message"] == "Service down for maintenance."


# ---------------------------------------------------------------------------
# Message extraction helper
# ---------------------------------------------------------------------------

class TestExtractMessageFromProviderBody:
    def test_anthropic_nested_error_message(self):
        body = json.dumps({"type": "error", "error": {"type": "auth", "message": "Bad key."}}).encode()
        assert _extract_message_from_provider_body(body, "anthropic") == "Bad key."

    def test_openai_nested_error_message(self):
        body = json.dumps({"error": {"message": "Rate limit.", "type": "rate_limit_exceeded"}}).encode()
        assert _extract_message_from_provider_body(body, "openai") == "Rate limit."

    def test_google_nested_error_message(self):
        body = json.dumps({"error": {"code": 401, "message": "Unauthenticated.", "status": "UNAUTHENTICATED"}}).encode()
        assert _extract_message_from_provider_body(body, "google") == "Unauthenticated."

    def test_non_json_returns_none(self):
        assert _extract_message_from_provider_body(b"<html>Error</html>", "openai") is None

    def test_empty_body_returns_none(self):
        assert _extract_message_from_provider_body(b"", "anthropic") is None

    def test_json_without_message_returns_none(self):
        body = json.dumps({"status": "error", "code": 500}).encode()
        assert _extract_message_from_provider_body(body, "anthropic") is None

    def test_generic_fallback_top_level_message(self):
        body = json.dumps({"message": "Maintenance window."}).encode()
        result = _extract_message_from_provider_body(body, "unknown")
        assert result == "Maintenance window."


# ---------------------------------------------------------------------------
# Upstream status code propagation
# ---------------------------------------------------------------------------

class TestUpstreamStatusPropagation:
    """upstream_status in the envelope must always reflect the HTTP status code."""

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 429, 500, 502, 503, 504])
    def test_upstream_status_matches_input(self, status: int):
        data = _parse(normalize_upstream_error(status, b"{}", "openai"))
        assert data["error"]["upstream_status"] == status
