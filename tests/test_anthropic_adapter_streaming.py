"""Tests for Anthropic adapter streaming detection (GAR-A4).

The Anthropic adapter detects streaming via the ``stream`` boolean field in the
request body.  Unlike the Google adapter (which inspects URL path segments),
streaming for Anthropic is body-driven: ``normalize()`` reads ``data.get("stream",
False)`` and stores it on ``CanonicalRequest.stream``.
"""

from __future__ import annotations

import json

import pytest

from tokenpak.proxy.adapters import AnthropicAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _body(extra: dict | None = None) -> bytes:
    """Return a minimal valid Anthropic messages request body."""
    payload: dict = {
        "model": "claude-3-opus-20240229",
        "messages": [{"role": "user", "content": "hello"}],
        "max_tokens": 64,
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload).encode()


# ---------------------------------------------------------------------------
# stream: true  →  CanonicalRequest.stream is True
# ---------------------------------------------------------------------------

class TestStreamingFlagEnabled:
    """stream: true in request body must produce canonical.stream == True."""

    def setup_method(self):
        self.adapter = AnthropicAdapter()

    def test_stream_true_bool(self):
        """Explicit stream: true (JSON boolean) sets the streaming flag."""
        canonical = self.adapter.normalize(_body({"stream": True}))
        assert canonical.stream is True

    def test_stream_truthy_int(self):
        """stream: 1 (truthy int) sets the streaming flag via bool()."""
        canonical = self.adapter.normalize(_body({"stream": 1}))
        assert canonical.stream is True

    def test_stream_flag_carried_through_denormalize(self):
        """stream: true survives a normalize → denormalize round-trip."""
        body = _body({"stream": True})
        canonical = self.adapter.normalize(body)
        restored = json.loads(self.adapter.denormalize(canonical))
        assert restored["stream"] is True


# ---------------------------------------------------------------------------
# stream: false / absent  →  CanonicalRequest.stream is False
# ---------------------------------------------------------------------------

class TestStreamingFlagDisabled:
    """stream absent or false must produce canonical.stream == False."""

    def setup_method(self):
        self.adapter = AnthropicAdapter()

    def test_stream_false_bool(self):
        """Explicit stream: false keeps the streaming flag off."""
        canonical = self.adapter.normalize(_body({"stream": False}))
        assert canonical.stream is False

    def test_stream_field_absent(self):
        """Omitting the stream field defaults the streaming flag to False."""
        canonical = self.adapter.normalize(_body())
        assert canonical.stream is False

    def test_stream_null(self):
        """stream: null (JSON null / Python None) results in False via bool()."""
        canonical = self.adapter.normalize(_body({"stream": None}))
        assert canonical.stream is False

    def test_stream_falsy_int(self):
        """stream: 0 (falsy int) keeps the streaming flag off via bool()."""
        canonical = self.adapter.normalize(_body({"stream": 0}))
        assert canonical.stream is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestStreamingEdgeCases:
    """Malformed and boundary inputs behave predictably."""

    def setup_method(self):
        self.adapter = AnthropicAdapter()

    def test_empty_body_raises(self):
        """An empty body cannot be JSON-decoded and raises an exception."""
        with pytest.raises(Exception):
            self.adapter.normalize(b"")

    def test_malformed_json_raises(self):
        """Non-JSON bytes raise a JSON decode error in normalize."""
        with pytest.raises(Exception):
            self.adapter.normalize(b"not-json")

    def test_stream_not_consumed_by_raw_extra(self):
        """stream is in the 'consumed' set and must NOT appear in raw_extra."""
        canonical = self.adapter.normalize(_body({"stream": True}))
        assert "stream" not in canonical.raw_extra


# ---------------------------------------------------------------------------
# SSE format (not applicable for incoming-request header detection)
# ---------------------------------------------------------------------------

class TestSSEFormat:
    """get_sse_format() declares the correct Anthropic SSE format string.

    Anthropic does not signal streaming via incoming request headers; the
    stream field is body-driven.  However, the adapter's get_sse_format()
    method declares the SSE format used when *parsing* Anthropic SSE
    responses, so we verify it returns the expected value.
    """

    def setup_method(self):
        self.adapter = AnthropicAdapter()

    def test_sse_format_is_anthropic(self):
        """get_sse_format() returns 'anthropic-sse'."""
        assert self.adapter.get_sse_format() == "anthropic-sse"
