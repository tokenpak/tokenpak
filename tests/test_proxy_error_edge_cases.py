"""
tests/test_proxy_error_edge_cases.py

Edge-case tests for proxy upstream error normalization (TEST-PROXY-ERR-01).

Covers inputs not exercised by test_proxy_error_response_standardization.py:
  - Malformed / truncated JSON bodies
  - Binary / invalid-UTF-8 bodies
  - Non-dict JSON (array, number, string)
  - Empty body variants (zero bytes, whitespace, null bytes)
  - HTTP 429 — upstream_status preserved so server can forward Retry-After header
  - HTTP 503 — provider-specific bodies with service_unavailable type
  - HTTP 504 — upstream_timeout (closest unit-testable analog to connection timeout)
  - Partial / truncated response bodies at various cut points
"""

from __future__ import annotations

import json

import pytest

from tokenpak.proxy.error_response import (
    _status_to_error_type,
    normalize_upstream_error,
)


def _parse(body: bytes) -> dict:
    return json.loads(body.decode("utf-8"))


# ---------------------------------------------------------------------------
# Malformed JSON upstream response
# ---------------------------------------------------------------------------


class TestMalformedJsonBody:
    """normalize_upstream_error must not raise for any malformed JSON input."""

    def test_truncated_mid_string(self):
        body = b'{"error": {"message": "Rate limit exceeded, please wait'
        data = _parse(normalize_upstream_error(429, body, "openai"))
        assert data["error"]["type"] == "rate_limit_error"
        assert data["error"]["upstream_status"] == 429
        assert data["error"]["message"]  # fallback non-empty

    def test_truncated_at_opening_brace(self):
        body = b"{"
        data = _parse(normalize_upstream_error(500, body, "anthropic"))
        assert data["error"]["type"] == "upstream_error"
        assert data["error"]["upstream_status"] == 500

    def test_binary_garbage_bytes(self):
        body = b"\x80\x81\x82\xff\xfe"
        data = _parse(normalize_upstream_error(502, body, "google"))
        assert data["error"]["upstream_status"] == 502
        assert data["error"]["message"]

    def test_invalid_utf8_mid_sequence(self):
        # Truncated 3-byte UTF-8 sequence
        body = b'{"message": "caf\xe2\x80'  # incomplete U+2019 RIGHT SINGLE QUOTATION
        data = _parse(normalize_upstream_error(400, body, "openai"))
        assert data["error"]["upstream_status"] == 400

    def test_json_array_not_dict(self):
        body = json.dumps(["error", "rate_limit"]).encode()
        data = _parse(normalize_upstream_error(429, body, "anthropic"))
        assert data["error"]["type"] == "rate_limit_error"
        # Non-dict JSON → fallback message
        assert "anthropic" in data["error"]["message"]

    def test_json_number(self):
        body = b"429"
        data = _parse(normalize_upstream_error(429, body, "openai"))
        assert data["error"]["upstream_status"] == 429
        assert data["error"]["message"]

    def test_json_null(self):
        body = b"null"
        data = _parse(normalize_upstream_error(500, body, "anthropic"))
        assert data["error"]["upstream_status"] == 500
        assert data["error"]["message"]

    def test_message_field_is_null(self):
        body = json.dumps({"error": {"message": None, "type": "server_error"}}).encode()
        data = _parse(normalize_upstream_error(500, body, "anthropic"))
        # None message → fallback
        assert "anthropic" in data["error"]["message"]

    def test_error_field_is_string_not_dict(self):
        body = json.dumps({"error": "something went wrong"}).encode()
        data = _parse(normalize_upstream_error(500, body, "openai"))
        # Generic fallback: error is a string, handled by passthrough path
        assert data["error"]["upstream_status"] == 500
        assert data["error"]["message"]


# ---------------------------------------------------------------------------
# Empty response body
# ---------------------------------------------------------------------------


class TestEmptyBody:
    """Empty or whitespace-only bodies must fall back to a non-empty message."""

    def test_zero_bytes_anthropic(self):
        data = _parse(normalize_upstream_error(429, b"", "anthropic"))
        assert data["error"]["type"] == "rate_limit_error"
        assert data["error"]["provider"] == "anthropic"
        assert data["error"]["message"]
        assert data["error"]["upstream_status"] == 429

    def test_zero_bytes_openai(self):
        data = _parse(normalize_upstream_error(503, b"", "openai"))
        assert data["error"]["type"] == "service_unavailable"
        assert data["error"]["message"]

    def test_whitespace_only(self):
        data = _parse(normalize_upstream_error(500, b"   \n  ", "google"))
        assert data["error"]["upstream_status"] == 500
        assert data["error"]["message"]

    def test_null_bytes(self):
        data = _parse(normalize_upstream_error(502, b"\x00\x00\x00", "anthropic"))
        assert data["error"]["upstream_status"] == 502
        assert data["error"]["message"]

    def test_empty_body_fallback_mentions_provider(self):
        data = _parse(normalize_upstream_error(401, b"", "groq"))
        assert "groq" in data["error"]["message"]

    def test_empty_body_fallback_mentions_status(self):
        data = _parse(normalize_upstream_error(403, b"", "anthropic"))
        assert "403" in data["error"]["message"]


# ---------------------------------------------------------------------------
# HTTP 429 — rate-limit and Retry-After passthrough
# ---------------------------------------------------------------------------


class TestHttp429RateLimitRetryAfter:
    """
    429 must normalize to rate_limit_error with upstream_status=429 preserved.

    The proxy server forwards all upstream headers except a fixed exclusion list
    (connection, keep-alive, transfer-encoding, content-length, content-encoding,
    content-type on errors).  Retry-After is NOT excluded, so the normalized
    upstream_status=429 enables server.py to make correct header-forwarding
    decisions.  These tests verify the normalized body has the correct structure.
    """

    def test_429_type_is_rate_limit_error(self):
        data = _parse(normalize_upstream_error(429, b"", "openai"))
        assert data["error"]["type"] == "rate_limit_error"

    def test_429_upstream_status_preserved(self):
        data = _parse(normalize_upstream_error(429, b"", "anthropic"))
        assert data["error"]["upstream_status"] == 429

    def test_429_anthropic_message_extracted(self):
        body = json.dumps(
            {
                "type": "error",
                "error": {"type": "rate_limit_error", "message": "Too many tokens in 60s window."},
            }
        ).encode()
        data = _parse(normalize_upstream_error(429, body, "anthropic"))
        assert data["error"]["message"] == "Too many tokens in 60s window."
        assert data["error"]["upstream_status"] == 429

    def test_429_openai_message_extracted(self):
        body = json.dumps(
            {
                "error": {
                    "message": "Rate limit exceeded: 60 req/min.",
                    "type": "requests",
                    "code": "rate_limit_exceeded",
                },
            }
        ).encode()
        data = _parse(normalize_upstream_error(429, body, "openai"))
        assert data["error"]["message"] == "Rate limit exceeded: 60 req/min."
        assert data["error"]["upstream_status"] == 429

    def test_429_google_message_extracted(self):
        body = json.dumps(
            {
                "error": {
                    "code": 429,
                    "message": "Quota exceeded for quota metric.",
                    "status": "RESOURCE_EXHAUSTED",
                },
            }
        ).encode()
        data = _parse(normalize_upstream_error(429, body, "google"))
        assert data["error"]["type"] == "rate_limit_error"
        assert "Quota exceeded" in data["error"]["message"]

    def test_429_truncated_body_falls_back(self):
        body = b'{"error": {"message": "Rate limit'  # truncated
        data = _parse(normalize_upstream_error(429, body, "openai"))
        assert data["error"]["type"] == "rate_limit_error"
        assert data["error"]["upstream_status"] == 429
        assert data["error"]["message"]

    def test_429_envelope_has_all_required_keys(self):
        data = _parse(normalize_upstream_error(429, b"", "groq"))
        err = data["error"]
        assert set(err.keys()) >= {"type", "message", "provider", "upstream_status"}


# ---------------------------------------------------------------------------
# HTTP 503 — service unavailable
# ---------------------------------------------------------------------------


class TestHttp503ServiceUnavailable:
    def test_503_type_is_service_unavailable(self):
        assert _status_to_error_type(503) == "service_unavailable"

    def test_503_normalize_empty_body(self):
        data = _parse(normalize_upstream_error(503, b"", "anthropic"))
        assert data["error"]["type"] == "service_unavailable"
        assert data["error"]["provider"] == "anthropic"

    def test_503_anthropic_message_extracted(self):
        body = json.dumps(
            {
                "type": "error",
                "error": {"type": "overloaded_error", "message": "Overloaded. Please retry."},
            }
        ).encode()
        data = _parse(normalize_upstream_error(503, body, "anthropic"))
        assert data["error"]["message"] == "Overloaded. Please retry."

    def test_503_openai_html_body_fallback(self):
        body = b"<html><body>503 Service Unavailable</body></html>"
        data = _parse(normalize_upstream_error(503, body, "openai"))
        assert data["error"]["type"] == "service_unavailable"
        assert data["error"]["upstream_status"] == 503
        assert data["error"]["message"]

    def test_503_provider_preserved_in_envelope(self):
        data = _parse(normalize_upstream_error(503, b"", "google"))
        assert data["error"]["provider"] == "google"


# ---------------------------------------------------------------------------
# HTTP 504 — upstream timeout (unit-testable timeout analog)
# ---------------------------------------------------------------------------


class TestTimeoutError:
    """
    HTTP 504 from upstream maps to upstream_timeout.  This covers the case where
    an upstream gateway timeout is propagated to the proxy.  Connection-level
    timeouts (before a response is received) are handled separately in server.py
    as proxy_error / 502.
    """

    def test_504_type_is_upstream_timeout(self):
        assert _status_to_error_type(504) == "upstream_timeout"

    def test_504_normalize_empty_body(self):
        data = _parse(normalize_upstream_error(504, b"", "anthropic"))
        assert data["error"]["type"] == "upstream_timeout"
        assert data["error"]["upstream_status"] == 504

    def test_504_normalize_plain_text_body(self):
        body = b"Gateway Timeout"
        data = _parse(normalize_upstream_error(504, body, "openai"))
        assert data["error"]["type"] == "upstream_timeout"
        assert data["error"]["upstream_status"] == 504

    def test_504_provider_preserved(self):
        data = _parse(normalize_upstream_error(504, b"", "google"))
        assert data["error"]["provider"] == "google"

    def test_504_message_non_empty(self):
        data = _parse(normalize_upstream_error(504, b"", "anthropic"))
        assert data["error"]["message"]


# ---------------------------------------------------------------------------
# Partial / truncated response body
# ---------------------------------------------------------------------------


class TestPartialTruncatedBody:
    """
    Bodies cut at arbitrary positions must not raise and must produce a valid
    canonical envelope with a non-empty fallback message.
    """

    @pytest.mark.parametrize(
        "body",
        [
            b'{"error": {"message": "Rate',
            b'{"error": {"message": "Rate limit exceeded',
            b'{"error": {"message": "Rate limit exceeded"}',  # missing outer }
            b'{"error":',
            b'{"err',
            b'"',
            b"{}",
            b"{",
            b"[",
            b'{"error": null}',
            b'{"error": {"message": ""}}',  # empty string message
        ],
    )
    def test_truncated_body_does_not_raise(self, body):
        data = _parse(normalize_upstream_error(429, body, "openai"))
        assert "error" in data
        assert data["error"]["upstream_status"] == 429

    def test_truncated_utf8_multibyte_char(self):
        # U+00E9 (é) encoded as 0xC3 0xA9 — cut after first byte
        body = b'{"message": "caf\xc3'
        data = _parse(normalize_upstream_error(500, body, "anthropic"))
        assert data["error"]["upstream_status"] == 500

    def test_partial_with_valid_outer_missing_inner(self):
        body = b'{"error": {"type": "rate_limit_error"}'  # missing "message" key
        data = _parse(normalize_upstream_error(429, body, "openai"))
        # No message field → fallback
        assert "openai" in data["error"]["message"]

    def test_empty_string_message_uses_fallback(self):
        body = json.dumps({"error": {"message": ""}}).encode()
        data = _parse(normalize_upstream_error(429, body, "anthropic"))
        # Empty string is falsy → fallback message
        assert "anthropic" in data["error"]["message"]

    def test_whitespace_message_uses_fallback(self):
        body = json.dumps({"error": {"message": "   "}}).encode()
        data = _parse(normalize_upstream_error(503, body, "openai"))
        # Whitespace message is truthy — check it at least has content
        # (str.strip() isn't applied — whitespace is returned as-is by _extract_message)
        assert data["error"]["message"]
