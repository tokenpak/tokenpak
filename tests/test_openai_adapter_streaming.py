"""Tests for OpenAI adapter streaming detection (GAR-A5).

Both OpenAI adapters (chat completions and responses) detect streaming via the
``stream`` boolean field in the request body.  ``normalize()`` reads
``data.get("stream", False)`` and stores it on ``CanonicalRequest.stream``.
"""

from __future__ import annotations

import json

import pytest

from tokenpak.proxy.adapters import OpenAIChatAdapter, OpenAIResponsesAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _chat_body(extra: dict | None = None) -> bytes:
    """Return a minimal valid OpenAI Chat Completions request body."""
    payload: dict = {
        "model": "gpt-4",
        "messages": [{"role": "user", "content": "hello"}],
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload).encode()


def _responses_body(extra: dict | None = None) -> bytes:
    """Return a minimal valid OpenAI Responses API request body."""
    payload: dict = {
        "model": "gpt-4o",
        "input": "hello",
    }
    if extra:
        payload.update(extra)
    return json.dumps(payload).encode()


# ---------------------------------------------------------------------------
# OpenAIChatAdapter — stream: true → CanonicalRequest.stream is True
# ---------------------------------------------------------------------------


class TestChatStreamingFlagEnabled:
    """stream: true in request body must produce canonical.stream == True."""

    def setup_method(self):
        self.adapter = OpenAIChatAdapter()

    def test_stream_true_bool(self):
        """Explicit stream: true (JSON boolean) sets the streaming flag."""
        canonical = self.adapter.normalize(_chat_body({"stream": True}))
        assert canonical.stream is True

    def test_stream_truthy_int(self):
        """stream: 1 (truthy int) sets the streaming flag via bool()."""
        canonical = self.adapter.normalize(_chat_body({"stream": 1}))
        assert canonical.stream is True

    def test_stream_flag_carried_through_denormalize(self):
        """stream: true survives a normalize → denormalize round-trip."""
        body = _chat_body({"stream": True})
        canonical = self.adapter.normalize(body)
        restored = json.loads(self.adapter.denormalize(canonical))
        assert restored["stream"] is True


# ---------------------------------------------------------------------------
# OpenAIChatAdapter — stream: false / absent → CanonicalRequest.stream is False
# ---------------------------------------------------------------------------


class TestChatStreamingFlagDisabled:
    """stream absent or false must produce canonical.stream == False."""

    def setup_method(self):
        self.adapter = OpenAIChatAdapter()

    def test_stream_false_bool(self):
        """Explicit stream: false keeps the streaming flag off."""
        canonical = self.adapter.normalize(_chat_body({"stream": False}))
        assert canonical.stream is False

    def test_stream_field_absent(self):
        """Omitting the stream field defaults the streaming flag to False."""
        canonical = self.adapter.normalize(_chat_body())
        assert canonical.stream is False

    def test_stream_null(self):
        """stream: null (JSON null / Python None) results in False via bool()."""
        canonical = self.adapter.normalize(_chat_body({"stream": None}))
        assert canonical.stream is False

    def test_stream_falsy_int(self):
        """stream: 0 (falsy int) keeps the streaming flag off via bool()."""
        canonical = self.adapter.normalize(_chat_body({"stream": 0}))
        assert canonical.stream is False


# ---------------------------------------------------------------------------
# OpenAIChatAdapter — edge cases
# ---------------------------------------------------------------------------


class TestChatStreamingEdgeCases:
    """Malformed and boundary inputs behave predictably."""

    def setup_method(self):
        self.adapter = OpenAIChatAdapter()

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
        canonical = self.adapter.normalize(_chat_body({"stream": True}))
        assert "stream" not in canonical.raw_extra


# ---------------------------------------------------------------------------
# OpenAIChatAdapter — SSE format
# ---------------------------------------------------------------------------


class TestChatSSEFormat:
    """get_sse_format() declares the correct SSE format string."""

    def setup_method(self):
        self.adapter = OpenAIChatAdapter()

    def test_sse_format_is_openai_sse(self):
        """get_sse_format() returns 'openai-sse'."""
        assert self.adapter.get_sse_format() == "openai-sse"


# ---------------------------------------------------------------------------
# OpenAIResponsesAdapter — stream: true → CanonicalRequest.stream is True
# ---------------------------------------------------------------------------


class TestResponsesStreamingFlagEnabled:
    """stream: true in Responses API request body sets canonical.stream == True."""

    def setup_method(self):
        self.adapter = OpenAIResponsesAdapter()

    def test_stream_true_bool(self):
        """Explicit stream: true (JSON boolean) sets the streaming flag."""
        canonical = self.adapter.normalize(_responses_body({"stream": True}))
        assert canonical.stream is True

    def test_stream_truthy_int(self):
        """stream: 1 (truthy int) sets the streaming flag via bool()."""
        canonical = self.adapter.normalize(_responses_body({"stream": 1}))
        assert canonical.stream is True

    def test_stream_flag_carried_through_denormalize(self):
        """stream: true survives a normalize → denormalize round-trip."""
        body = _responses_body({"stream": True})
        canonical = self.adapter.normalize(body)
        restored = json.loads(self.adapter.denormalize(canonical))
        assert restored["stream"] is True


# ---------------------------------------------------------------------------
# OpenAIResponsesAdapter — stream: false / absent → CanonicalRequest.stream is False
# ---------------------------------------------------------------------------


class TestResponsesStreamingFlagDisabled:
    """stream absent or false must produce canonical.stream == False."""

    def setup_method(self):
        self.adapter = OpenAIResponsesAdapter()

    def test_stream_false_bool(self):
        """Explicit stream: false keeps the streaming flag off."""
        canonical = self.adapter.normalize(_responses_body({"stream": False}))
        assert canonical.stream is False

    def test_stream_field_absent(self):
        """Omitting the stream field defaults the streaming flag to False."""
        canonical = self.adapter.normalize(_responses_body())
        assert canonical.stream is False

    def test_stream_null(self):
        """stream: null (JSON null / Python None) results in False via bool()."""
        canonical = self.adapter.normalize(_responses_body({"stream": None}))
        assert canonical.stream is False

    def test_stream_falsy_int(self):
        """stream: 0 (falsy int) keeps the streaming flag off via bool()."""
        canonical = self.adapter.normalize(_responses_body({"stream": 0}))
        assert canonical.stream is False


# ---------------------------------------------------------------------------
# OpenAIResponsesAdapter — edge cases
# ---------------------------------------------------------------------------


class TestResponsesStreamingEdgeCases:
    """Malformed and boundary inputs behave predictably."""

    def setup_method(self):
        self.adapter = OpenAIResponsesAdapter()

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
        canonical = self.adapter.normalize(_responses_body({"stream": True}))
        assert "stream" not in canonical.raw_extra


# ---------------------------------------------------------------------------
# OpenAIResponsesAdapter — SSE format
# ---------------------------------------------------------------------------


class TestResponsesSSEFormat:
    """get_sse_format() declares the correct SSE format string."""

    def setup_method(self):
        self.adapter = OpenAIResponsesAdapter()

    def test_sse_format_is_openai_responses_sse(self):
        """get_sse_format() returns 'openai-responses-sse'."""
        assert self.adapter.get_sse_format() == "openai-responses-sse"
