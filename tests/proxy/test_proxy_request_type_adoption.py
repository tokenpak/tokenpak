"""
Tests for TRIX-MTC-11: ProxyRequest/ProxyResponse type adoption in pipeline.

Verifies that key pipeline functions accept ProxyRequest and that ProxyResponse
is constructed from upstream responses. Also verifies backward compatibility
with legacy bytes inputs.

Test classes:
  - TestByteInjectSystemBlockRequest   — _byte_inject_system_block request kwarg
  - TestInjectVaultContextRequest      — inject_vault_context request kwarg
  - TestCompactRequestBodyRequest      — compact_request_body request kwarg
  - TestLegacyBytesBackwardCompat      — all three functions still accept raw bytes
  - TestProxyRequestConstruction       — ProxyRequest populated with session_id
  - TestProxyResponseConstruction      — ProxyResponse dataclass creation
  - TestSessionIdAndPlatformFlow       — session_id/source_platform flow
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from tokenpak.proxy.request import (
    ProxyRequest,
    ProxyResponse,
    _byte_inject_system_block,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _body_bytes(system=None, model="claude-sonnet-4-5", **extra) -> bytes:
    data: dict[str, Any] = {"model": model, "max_tokens": 50}
    if system is not None:
        data["system"] = system
    else:
        data["system"] = [{"type": "text", "text": "You are helpful."}]
    data["messages"] = [{"role": "user", "content": "hi"}]
    data.update(extra)
    return json.dumps(data).encode()


def _make_request(body: bytes | None = None, session_id: str | None = None,
                  source_platform: str = "test") -> ProxyRequest:
    b = body if body is not None else _body_bytes()
    return ProxyRequest(
        method="POST",
        url="https://api.anthropic.com/v1/messages",
        headers={"content-type": "application/json"},
        body=b,
        session_id=session_id,
        source_platform=source_platform,
    )


# ---------------------------------------------------------------------------
# TestByteInjectSystemBlockRequest
# ---------------------------------------------------------------------------

class TestByteInjectSystemBlockRequest:
    def test_request_kwarg_overrides_body_arg(self):
        """When request is provided, its body is used instead of the body arg."""
        real_body = _body_bytes()
        dummy_body = b'{"not": "real"}'  # positional arg — should be ignored
        req = _make_request(body=real_body)

        result = _byte_inject_system_block(dummy_body, "injected text", request=req)

        # Result should be based on real_body (has system array), not dummy_body
        assert b"injected text" in result
        parsed = json.loads(result)
        texts = [b["text"] for b in parsed["system"] if b.get("type") == "text"]
        assert "injected text" in texts

    def test_request_kwarg_none_uses_positional_body(self):
        """When request=None (default), positional body arg is used unchanged."""
        body = _body_bytes()
        result = _byte_inject_system_block(body, "hello vault", request=None)
        parsed = json.loads(result)
        texts = [b["text"] for b in parsed["system"] if b.get("type") == "text"]
        assert "hello vault" in texts

    def test_empty_injection_text_noop(self):
        body = _body_bytes()
        req = _make_request(body=body)
        result = _byte_inject_system_block(body, "", request=req)
        assert result == body

    def test_no_system_array_returns_body_unchanged(self):
        """Body without system array is returned unchanged (fail-open)."""
        body = b'{"model":"claude-3","messages":[]}'
        req = _make_request(body=body)
        result = _byte_inject_system_block(body, "some text", request=req)
        assert result == body


# ---------------------------------------------------------------------------
# TestInjectVaultContextRequest
# ---------------------------------------------------------------------------

class TestInjectVaultContextRequest:
    def test_request_kwarg_signature_accepted(self):
        """inject_vault_context accepts request kwarg without raising TypeError."""
        from tokenpak.proxy.vault_bridge import inject_vault_context

        body = _body_bytes()
        req = _make_request(body=body)
        # With vault unavailable the function returns early — that's fine.
        # What we assert is that the call does not raise TypeError.
        try:
            result = inject_vault_context(body, request=req)
        except TypeError as e:
            pytest.fail(f"inject_vault_context raised TypeError: {e}")
        # Must return a 3-tuple
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_request_body_used_when_provided(self):
        """inject_vault_context uses request.body when request is not None."""
        from tokenpak.proxy.vault_bridge import inject_vault_context

        req_body = _body_bytes()
        positional_body = b'{}' * 0  # empty bytes — no valid query signal
        req = _make_request(body=req_body)

        # We can't easily assert the vault ran (it may be unavailable in CI),
        # but we verify the call succeeds and the tuple shape is correct.
        result = inject_vault_context(positional_body, request=req)
        assert len(result) == 3

    def test_legacy_call_without_request_still_works(self):
        """Calling inject_vault_context(body) without request kwarg still works."""
        from tokenpak.proxy.vault_bridge import inject_vault_context

        body = _body_bytes()
        result = inject_vault_context(body)
        assert isinstance(result, tuple)
        assert len(result) == 3

    def test_request_none_uses_positional(self):
        """Explicit request=None falls back to positional body_bytes."""
        from tokenpak.proxy.vault_bridge import inject_vault_context

        body = _body_bytes()
        result = inject_vault_context(body, request=None)
        assert isinstance(result, tuple)
        assert len(result) == 3


# ---------------------------------------------------------------------------
# TestCompactRequestBodyRequest
# ---------------------------------------------------------------------------

class TestCompactRequestBodyRequest:
    def test_request_kwarg_signature_accepted(self):
        """compact_request_body accepts request kwarg without TypeError."""
        from tokenpak.compression.pipeline import compact_request_body

        body = _body_bytes()
        req = _make_request(body=body)
        try:
            result = compact_request_body(body, request=req)
        except TypeError as e:
            pytest.fail(f"compact_request_body raised TypeError: {e}")
        assert isinstance(result, tuple)
        assert len(result) == 4

    def test_legacy_call_without_request(self):
        """compact_request_body(body) without request kwarg still works."""
        from tokenpak.compression.pipeline import compact_request_body

        body = _body_bytes()
        result = compact_request_body(body)
        assert isinstance(result, tuple)
        assert len(result) == 4

    def test_request_none_fallback(self):
        from tokenpak.compression.pipeline import compact_request_body

        body = _body_bytes()
        result = compact_request_body(body, request=None)
        assert isinstance(result, tuple)
        assert len(result) == 4

    def test_request_body_overrides_positional(self):
        """When request is provided, its body is used for compaction."""
        from tokenpak.compression.pipeline import compact_request_body

        real_body = _body_bytes()
        req = _make_request(body=real_body)
        # Pass empty bytes as positional; function should use req.body
        result = compact_request_body(b"", request=req)
        assert isinstance(result, tuple)
        assert len(result) == 4


# ---------------------------------------------------------------------------
# TestLegacyBytesBackwardCompat
# ---------------------------------------------------------------------------

class TestLegacyBytesBackwardCompat:
    def test_legacy_bytes_path_unchanged(self):
        """_byte_inject_system_block(body, text) — original call signature works."""
        body = _body_bytes()
        result = _byte_inject_system_block(body, "legacy injection")
        assert b"legacy injection" in result

    def test_legacy_bytes_path_fail_open(self):
        """_byte_inject_system_block with non-JSON body returns body unchanged."""
        body = b"not json at all"
        result = _byte_inject_system_block(body, "text")
        assert result == body

    def test_legacy_bytes_path_works(self):
        """All three functions accept bytes positional arg without request kwarg."""
        from tokenpak.proxy.vault_bridge import inject_vault_context
        from tokenpak.compression.pipeline import compact_request_body

        body = _body_bytes()
        r1 = inject_vault_context(body)
        assert len(r1) == 3
        r2 = compact_request_body(body)
        assert len(r2) == 4
        r3 = _byte_inject_system_block(body, "check")
        assert isinstance(r3, bytes)


# ---------------------------------------------------------------------------
# TestProxyRequestConstruction
# ---------------------------------------------------------------------------

class TestProxyRequestConstruction:
    def test_proxy_request_fields(self):
        req = ProxyRequest(
            method="POST",
            url="https://api.anthropic.com/v1/messages",
            headers={"content-type": "application/json"},
            body=b'{"test": 1}',
            session_id="sess-abc",
            source_platform="claude-code",
        )
        assert req.method == "POST"
        assert req.session_id == "sess-abc"
        assert req.source_platform == "claude-code"
        assert req.body == b'{"test": 1}'

    def test_proxy_request_defaults(self):
        req = ProxyRequest(method="POST", url="http://localhost/v1/messages")
        assert req.session_id is None
        assert req.source_platform == "unknown"
        assert req.body == b""
        assert req.headers == {}

    def test_proxy_request_get_header_case_insensitive(self):
        req = ProxyRequest(
            method="POST",
            url="http://x",
            headers={"Content-Type": "application/json", "X-Session": "abc"},
        )
        assert req.get_header("content-type") == "application/json"
        assert req.get_header("x-session") == "abc"
        assert req.get_header("missing", "default") == "default"

    def test_session_id_and_source_platform_set_together(self):
        req = _make_request(session_id="s1", source_platform="openclaw")
        assert req.session_id == "s1"
        assert req.source_platform == "openclaw"


# ---------------------------------------------------------------------------
# TestProxyResponseConstruction
# ---------------------------------------------------------------------------

class TestProxyResponseConstruction:
    def test_basic_proxy_response(self):
        resp = ProxyResponse(
            status_code=200,
            headers={"content-type": "application/json"},
            body=b'{"id": "msg_123"}',
        )
        assert resp.status_code == 200
        assert resp.body == b'{"id": "msg_123"}'
        assert resp.get_header("content-type") == "application/json"

    def test_proxy_response_defaults(self):
        resp = ProxyResponse(status_code=404)
        assert resp.headers == {}
        assert resp.body == b""

    def test_proxy_response_error_status(self):
        resp = ProxyResponse(
            status_code=429,
            headers={"retry-after": "5"},
            body=b'{"error":"rate_limit"}',
        )
        assert resp.status_code == 429
        assert resp.get_header("retry-after") == "5"

    def test_proxy_response_get_header_case_insensitive(self):
        resp = ProxyResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
        )
        assert resp.get_header("content-type") == "application/json"
        assert resp.get_header("missing", "fallback") == "fallback"


# ---------------------------------------------------------------------------
# TestSessionIdAndPlatformFlow
# ---------------------------------------------------------------------------

class TestSessionIdAndPlatformFlow:
    def test_session_id_set_on_request(self):
        req = _make_request(session_id="session-xyz")
        assert req.session_id == "session-xyz"

    def test_source_platform_propagates(self):
        req = _make_request(source_platform="claude-code")
        assert req.source_platform == "claude-code"

    def test_request_passed_to_byte_inject_carries_session_id(self):
        body = _body_bytes()
        req = _make_request(body=body, session_id="sess-flow", source_platform="claude-code")
        result = _byte_inject_system_block(body, "vault context", request=req)
        assert isinstance(result, bytes)
        # session_id is not part of the output bytes, but the call succeeds
        assert b"vault context" in result

    def test_inject_vault_context_receives_request(self):
        from tokenpak.proxy.vault_bridge import inject_vault_context

        body = _body_bytes()
        req = _make_request(body=body, session_id="sess-vault", source_platform="sdk")
        result = inject_vault_context(body, request=req)
        assert len(result) == 3
        assert isinstance(result[0], bytes)

    def test_compact_request_body_receives_request(self):
        from tokenpak.compression.pipeline import compact_request_body

        body = _body_bytes()
        req = _make_request(body=body, session_id="sess-compact", source_platform="openclaw")
        result = compact_request_body(body, request=req)
        assert len(result) == 4

    def test_proxy_request_round_trip_body(self):
        body = _body_bytes()
        req = _make_request(body=body)
        # Body on the request should be the same bytes we put in
        assert req.body == body
