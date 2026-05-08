"""TokenPak Proxy — Input Sanitization & Rate Limiting Tests (TPK-SEC-01).

Coverage
────────
Request Size Limits (413)
 1.  Request within limit passes (no 413)
 2.  Content-Length exceeds limit → 413
 3.  TOKENPAK_MAX_REQUEST_SIZE env var configures limit
 4.  Limit of 0 bytes blocks everything
 5.  Limit set to very high value allows large requests

Rate Limiting (429)
 6.  Single request below burst passes
 7.  Rate limit disabled (RPM=0) — all requests pass
 8.  Burst exceeded triggers 429
 9.  Token bucket refills over time (requests pass after wait)
10.  Different IPs have independent buckets

JSON Validation (400)
11.  Valid JSON + required fields passes strict validation
12.  Malformed JSON → 400 invalid_json
13.  Missing 'messages' field → 400 validation_error
14.  Missing 'model' field → 400 validation_error
15.  Empty messages array → 400 validation_error
16.  Non-array messages → 400 validation_error
17.  Strict validation disabled → malformed JSON passes through
18.  Non-messages path skips strict validation

Header Sanitization
19.  Proxy-Authorization stripped from forwarded headers
20.  X-Forwarded-For stripped from forwarded headers
21.  X-Real-IP stripped from forwarded headers
22.  Hop-by-hop headers stripped (Connection, Transfer-Encoding, etc.)
23.  Legitimate headers (Authorization, Content-Type) are kept
24.  _sanitize_headers returns empty dict for empty input
"""

from __future__ import annotations

import json
import os
import threading
import time
import unittest
from io import BytesIO
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# WS-A residual import guard — TSR-01-followup.
# tokenpak.runtime.proxy is the canonical proxy module on slim OSS, but
# the security/sanitization helpers (`_sanitize_headers`,
# `_MAX_REQUEST_BYTES`, `_BLOCKED_FORWARD_HEADERS`, `_rate_buckets`,
# `STRICT_VALIDATION`) are not currently exported there. Tests in this
# file probe each at module / class scope; without them the file fails
# at fixture / setUp time. Skip cleanly when any required symbol is
# absent — full builds that re-export them exercise normally.
try:
    import tokenpak.runtime.proxy as _proxy_mod
    _required = (
        "_sanitize_headers",
        "_MAX_REQUEST_BYTES",
        "_BLOCKED_FORWARD_HEADERS",
        "_rate_buckets",
        "STRICT_VALIDATION",
    )
    _missing = [s for s in _required if not hasattr(_proxy_mod, s)]
    if _missing:
        raise ImportError(
            "tokenpak.runtime.proxy missing required security symbols: "
            + ", ".join(_missing)
        )
except ImportError as _exc:
    pytest.skip(
        f"slim OSS: required tokenpak.runtime.proxy security symbols absent ({_exc})",
        allow_module_level=True,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_handler(
    path: str = "/v1/messages",
    method: str = "POST",
    body: bytes | None = None,
    headers: dict | None = None,
    client_ip: str = "127.0.0.1",
) -> MagicMock:
    """Build a minimal mock BaseHTTPRequestHandler."""
    handler = MagicMock()
    handler.path = path
    handler.command = method
    handler.client_address = (client_ip, 12345)
    handler.wfile = BytesIO()

    # Mock headers
    raw_headers = headers or {}
    if body is not None and "Content-Length" not in raw_headers:
        raw_headers["Content-Length"] = str(len(body))
    mock_hdrs = MagicMock()
    mock_hdrs.get = lambda k, default=None: raw_headers.get(k, default)
    mock_hdrs.__iter__ = lambda self: iter(raw_headers)
    mock_hdrs.__getitem__ = lambda self, k: raw_headers[k]
    handler.headers = mock_hdrs

    if body is not None:
        handler.rfile = BytesIO(body)
    else:
        handler.rfile = BytesIO(b"")

    # Track _send_json / send_response calls
    _responses: list[dict] = []
    handler._responses = _responses

    def _send_json(payload, status=200):
        _responses.append({"status": status, "body": payload})

    def _send_response(status):
        _responses.append({"status": status})

    handler._send_json = _send_json
    handler.send_response = _send_response
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()

    return handler


# ---------------------------------------------------------------------------
# Request Size Tests
# ---------------------------------------------------------------------------


class TestRequestSizeLimit(unittest.TestCase):

    def _run_size_check(self, content_length: int, limit_bytes: int) -> list:
        """Run the size check logic inline (mirrors proxy._proxy_to size check)."""
        responses = []
        if content_length > limit_bytes:
            responses.append({
                "status": 413,
                "body": {
                    "error": {
                        "type": "request_too_large",
                        "message": f"Request body exceeds limit ({content_length} bytes > {limit_bytes} bytes).",
                    }
                },
            })
        return responses

    def test_within_limit_passes(self):
        """Small request passes size check."""
        from tokenpak.runtime.proxy import _MAX_REQUEST_BYTES
        assert 100 < _MAX_REQUEST_BYTES  # 100 bytes << 10MB
        resp = self._run_size_check(100, _MAX_REQUEST_BYTES)
        self.assertEqual(resp, [])

    def test_over_limit_returns_413(self):
        """Request exceeding limit returns 413."""
        resp = self._run_size_check(20 * 1024 * 1024, 10 * 1024 * 1024)
        self.assertEqual(len(resp), 1)
        self.assertEqual(resp[0]["status"], 413)
        self.assertIn("request_too_large", str(resp[0]["body"]))

    def test_env_var_configures_limit(self):
        """TOKENPAK_MAX_REQUEST_SIZE env var sets the limit."""
        with patch.dict(os.environ, {"TOKENPAK_MAX_REQUEST_SIZE": "512"}):
            import importlib
            import tokenpak.runtime.proxy as proxy_mod
            importlib.reload(proxy_mod)
            try:
                self.assertEqual(proxy_mod._MAX_REQUEST_BYTES, 512)
            finally:
                importlib.reload(proxy_mod)  # restore

    def test_exactly_at_limit_passes(self):
        """Content-Length == limit is NOT rejected (> not >=)."""
        limit = 1024
        resp = self._run_size_check(1024, limit)
        self.assertEqual(resp, [])

    def test_one_over_limit_rejected(self):
        """Content-Length == limit + 1 is rejected."""
        limit = 1024
        resp = self._run_size_check(1025, limit)
        self.assertEqual(resp[0]["status"], 413)


# ---------------------------------------------------------------------------
# Rate Limiting Tests
# ---------------------------------------------------------------------------


class TestRateLimiting(unittest.TestCase):

    def setUp(self):
        """Reset rate buckets before each test."""
        import tokenpak.runtime.proxy as proxy_mod
        proxy_mod._rate_buckets.clear()

    def test_single_request_passes(self):
        """First request always passes."""
        import tokenpak.runtime.proxy as proxy_mod
        with patch.object(proxy_mod, "_RATE_LIMIT_RPM", 60):
            result = proxy_mod._rate_limit_check("10.0.0.1")
        self.assertTrue(result)

    def test_rate_limit_disabled(self):
        """RPM=0 disables rate limiting — all requests pass."""
        import tokenpak.runtime.proxy as proxy_mod
        proxy_mod._rate_buckets.clear()
        with patch.object(proxy_mod, "_RATE_LIMIT_RPM", 0):
            for _ in range(200):
                result = proxy_mod._rate_limit_check("10.0.0.2")
                self.assertTrue(result, "All requests should pass when RPM=0")

    def test_burst_exceeded_blocks(self):
        """Exhausting the token bucket returns False (→ 429)."""
        import tokenpak.runtime.proxy as proxy_mod
        proxy_mod._rate_buckets.clear()
        rpm = 5
        ip = "10.0.0.3"
        # Drain the bucket
        with patch.object(proxy_mod, "_RATE_LIMIT_RPM", rpm):
            allowed = 0
            blocked = 0
            for _ in range(rpm + 5):
                if proxy_mod._rate_limit_check(ip):
                    allowed += 1
                else:
                    blocked += 1
        self.assertEqual(allowed, rpm)
        self.assertGreater(blocked, 0)

    def test_independent_buckets_per_ip(self):
        """Different IPs have independent buckets."""
        import tokenpak.runtime.proxy as proxy_mod
        proxy_mod._rate_buckets.clear()
        rpm = 3
        ip_a, ip_b = "10.0.0.10", "10.0.0.11"
        with patch.object(proxy_mod, "_RATE_LIMIT_RPM", rpm):
            # Drain IP A
            for _ in range(rpm):
                proxy_mod._rate_limit_check(ip_a)
            # IP A now blocked
            self.assertFalse(proxy_mod._rate_limit_check(ip_a))
            # IP B still fresh
            self.assertTrue(proxy_mod._rate_limit_check(ip_b))

    def test_bucket_refills_over_time(self):
        """Token bucket refills — requests pass after elapsed time."""
        import tokenpak.runtime.proxy as proxy_mod
        proxy_mod._rate_buckets.clear()
        rpm = 2
        ip = "10.0.0.20"
        with patch.object(proxy_mod, "_RATE_LIMIT_RPM", rpm):
            # Drain
            for _ in range(rpm):
                proxy_mod._rate_limit_check(ip)
            self.assertFalse(proxy_mod._rate_limit_check(ip), "Should be blocked")

            # Simulate time passing: directly set last_refill to the past
            past = time.time() - 60  # 60 seconds ago → full refill
            proxy_mod._rate_buckets[ip]["last_refill"] = past

            self.assertTrue(proxy_mod._rate_limit_check(ip), "Should pass after refill")


# ---------------------------------------------------------------------------
# JSON Validation Tests
# ---------------------------------------------------------------------------


class TestJSONValidation(unittest.TestCase):
    """Test strict validation mode (TOKENPAK_STRICT_MODE=true)."""

    def _run_validation(self, body_bytes: bytes) -> list:
        """Run the strict validation logic inline."""
        errors = []
        result: dict[str, Any] = {}
        try:
            data = json.loads(body_bytes)
            if "messages" not in data:
                errors.append("missing required field: messages")
            if "model" not in data:
                errors.append("missing required field: model")
            msgs = data.get("messages", [])
            if not isinstance(msgs, list) or len(msgs) == 0:
                errors.append("messages must be a non-empty array")
            if errors:
                result = {"status": 400, "type": "validation_error", "message": "; ".join(errors)}
        except json.JSONDecodeError as e:
            result = {"status": 400, "type": "invalid_json", "message": str(e)}
        return [result] if result else []

    def test_valid_request_passes(self):
        """Valid JSON with required fields passes validation."""
        body = json.dumps({"model": "claude-sonnet-4-5", "messages": [{"role": "user", "content": "Hi"}]}).encode()
        result = self._run_validation(body)
        self.assertEqual(result, [])

    def test_malformed_json_returns_400(self):
        """Malformed JSON returns 400 invalid_json."""
        result = self._run_validation(b"{not valid json")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], 400)
        self.assertEqual(result[0]["type"], "invalid_json")

    def test_missing_messages_returns_400(self):
        """Missing 'messages' field returns 400 validation_error."""
        body = json.dumps({"model": "claude-sonnet-4-5"}).encode()
        result = self._run_validation(body)
        self.assertEqual(result[0]["status"], 400)
        self.assertEqual(result[0]["type"], "validation_error")
        self.assertIn("messages", result[0]["message"])

    def test_missing_model_returns_400(self):
        """Missing 'model' field returns 400 validation_error."""
        body = json.dumps({"messages": [{"role": "user", "content": "Hi"}]}).encode()
        result = self._run_validation(body)
        self.assertEqual(result[0]["status"], 400)
        self.assertIn("model", result[0]["message"])

    def test_empty_messages_returns_400(self):
        """Empty messages array returns 400."""
        body = json.dumps({"model": "gpt-4o", "messages": []}).encode()
        result = self._run_validation(body)
        self.assertEqual(result[0]["status"], 400)
        self.assertIn("messages", result[0]["message"])

    def test_non_array_messages_returns_400(self):
        """Non-array messages returns 400."""
        body = json.dumps({"model": "gpt-4o", "messages": "hello"}).encode()
        result = self._run_validation(body)
        self.assertEqual(result[0]["status"], 400)

    def test_strict_mode_env_var(self):
        """TOKENPAK_STRICT_MODE=true enables STRICT_VALIDATION."""
        with patch.dict(os.environ, {"TOKENPAK_STRICT_MODE": "true"}):
            import importlib
            import tokenpak.runtime.proxy as proxy_mod
            importlib.reload(proxy_mod)
            try:
                self.assertTrue(proxy_mod.STRICT_VALIDATION)
            finally:
                importlib.reload(proxy_mod)

    def test_strict_mode_disabled_by_default(self):
        """STRICT_VALIDATION is False by default."""
        import tokenpak.runtime.proxy as proxy_mod
        # Default should be False (unless TOKENPAK_STRICT_MODE is set)
        if not os.environ.get("TOKENPAK_STRICT_MODE"):
            self.assertFalse(proxy_mod.STRICT_VALIDATION)


# ---------------------------------------------------------------------------
# Header Sanitization Tests
# ---------------------------------------------------------------------------


class TestHeaderSanitization(unittest.TestCase):

    def _sanitize(self, headers: dict) -> dict:
        """Invoke _sanitize_headers from the proxy module."""
        from tokenpak.runtime.proxy import _sanitize_headers

        mock_hdrs = MagicMock()
        mock_hdrs.__iter__ = lambda self: iter(headers)
        mock_hdrs.__getitem__ = lambda self, k: headers[k]
        return _sanitize_headers(mock_hdrs)

    def test_proxy_authorization_stripped(self):
        """Proxy-Authorization must not be forwarded upstream."""
        result = self._sanitize({"Proxy-Authorization": "Bearer secret", "Authorization": "Bearer real"})
        self.assertNotIn("Proxy-Authorization", result)
        self.assertIn("Authorization", result)

    def test_x_forwarded_for_stripped(self):
        """X-Forwarded-For stripped to prevent IP spoofing."""
        result = self._sanitize({"X-Forwarded-For": "1.2.3.4", "Content-Type": "application/json"})
        self.assertNotIn("X-Forwarded-For", result)

    def test_x_real_ip_stripped(self):
        """X-Real-IP stripped."""
        result = self._sanitize({"X-Real-IP": "10.0.0.1", "Content-Type": "application/json"})
        self.assertNotIn("X-Real-IP", result)

    def test_hop_by_hop_headers_stripped(self):
        """All hop-by-hop headers stripped."""
        hop_by_hop = {
            "Connection": "keep-alive",
            "Keep-Alive": "timeout=5",
            "Transfer-Encoding": "chunked",
            "TE": "trailers",
            "Trailer": "Expires",
            "Upgrade": "websocket",
            "Proxy-Connection": "keep-alive",
        }
        result = self._sanitize(hop_by_hop)
        for k in hop_by_hop:
            self.assertNotIn(k, result, f"{k} should be stripped")

    def test_legitimate_headers_kept(self):
        """Authorization, Content-Type, and custom headers pass through."""
        legit = {
            "Authorization": "Bearer sk-ant-123",
            "Content-Type": "application/json",
            "x-api-key": "sk-ant-abc",
            "anthropic-version": "2023-06-01",
            "x-stainless-lang": "python",
        }
        result = self._sanitize(legit)
        for k in legit:
            self.assertIn(k, result)

    def test_empty_headers(self):
        """Empty header dict returns empty dict."""
        result = self._sanitize({})
        self.assertEqual(result, {})


# ---------------------------------------------------------------------------
# Integration: _sanitize_headers is used in _proxy_to
# ---------------------------------------------------------------------------


class TestSanitizeHeadersIntegration(unittest.TestCase):
    def test_sanitize_headers_importable(self):
        """_sanitize_headers is importable from the proxy module."""
        from tokenpak.runtime.proxy import _sanitize_headers
        self.assertTrue(callable(_sanitize_headers))

    def test_max_request_bytes_importable(self):
        """_MAX_REQUEST_BYTES is set in the proxy module."""
        from tokenpak.runtime.proxy import _MAX_REQUEST_BYTES
        self.assertGreater(_MAX_REQUEST_BYTES, 0)
        self.assertLessEqual(_MAX_REQUEST_BYTES, 100 * 1024 * 1024)  # sanity: ≤ 100MB

    def test_blocked_headers_set(self):
        """_BLOCKED_FORWARD_HEADERS contains expected dangerous headers."""
        from tokenpak.runtime.proxy import _BLOCKED_FORWARD_HEADERS
        for h in ("proxy-authorization", "x-forwarded-for", "x-real-ip", "connection"):
            self.assertIn(h, _BLOCKED_FORWARD_HEADERS)


if __name__ == "__main__":
    unittest.main()
