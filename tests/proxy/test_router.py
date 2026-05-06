"""
tests/proxy/test_router.py

Regression test for TRIX-MTC-07 Fix #5:
  ProviderRouter.route() must reject requests where the Content-Length header
  does not match the actual body size, raising ValueError.

Before the fix, a mismatched Content-Length was silently ignored, which could
cause truncated bodies to be routed and forwarded to upstream providers.
"""

from __future__ import annotations

import json

import pytest

from tokenpak.proxy.router import ProviderRouter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _json_body(model: str = "claude-sonnet-4-5") -> bytes:
    return json.dumps({"model": model, "messages": []}).encode()


def _headers_with_content_length(body: bytes, delta: int = 0) -> dict:
    """Return headers where Content-Length is len(body) + delta."""
    return {
        "Content-Type": "application/json",
        "x-api-key": "test-key",
        "Content-Length": str(len(body) + delta),
    }


# ---------------------------------------------------------------------------
# Regression tests
# ---------------------------------------------------------------------------

def test_router_rejects_content_length_too_large():
    """
    Content-Length header larger than actual body must raise ValueError.
    """
    router = ProviderRouter()
    body = _json_body()
    headers = _headers_with_content_length(body, delta=10)  # declares 10 extra bytes

    with pytest.raises(ValueError, match="Content-Length mismatch"):
        router.route("/v1/messages", headers, body)


def test_router_rejects_content_length_too_small():
    """
    Content-Length header smaller than actual body must raise ValueError.
    """
    router = ProviderRouter()
    body = _json_body()
    headers = _headers_with_content_length(body, delta=-5)  # declares 5 fewer bytes

    with pytest.raises(ValueError, match="Content-Length mismatch"):
        router.route("/v1/messages", headers, body)


def test_router_rejects_invalid_content_length_value():
    """
    Non-numeric Content-Length must raise ValueError.
    """
    router = ProviderRouter()
    body = _json_body()
    headers = {"Content-Type": "application/json", "Content-Length": "banana"}

    with pytest.raises(ValueError, match="Invalid Content-Length"):
        router.route("/v1/messages", headers, body)


def test_router_accepts_correct_content_length():
    """
    Matching Content-Length must not raise; routing proceeds normally.
    """
    router = ProviderRouter()
    body = _json_body()
    headers = _headers_with_content_length(body, delta=0)

    result = router.route("/v1/messages", headers, body)
    assert result.provider == "anthropic"


def test_router_accepts_missing_content_length():
    """
    Absent Content-Length header is allowed (streaming or chunked-encoded requests).
    """
    router = ProviderRouter()
    body = _json_body()
    headers = {"Content-Type": "application/json", "x-api-key": "test"}

    result = router.route("/v1/messages", headers, body)
    assert result.provider == "anthropic"


def test_router_detects_deepseek_host():
    router = ProviderRouter()
    body = _json_body("deepseek-v4-flash")

    result = router.route("https://api.deepseek.com/v1/chat/completions", {}, body)

    assert result.provider == "deepseek"
    assert result.base_url == "https://api.deepseek.com"
    assert result.full_url == "https://api.deepseek.com/v1/chat/completions"


def test_router_detects_deepseek_model_for_reverse_proxy():
    router = ProviderRouter()
    body = _json_body("deepseek-v4-pro")
    headers = {"Content-Type": "application/json", "Content-Length": str(len(body))}

    result = router.route("/v1/chat/completions", headers, body)

    assert result.provider == "deepseek"
    assert result.full_url == "https://api.deepseek.com/v1/chat/completions"
