"""Unit tests for OpenAI Chat proxy adapter token extraction.

Covers:
- extract_input_tokens: prompt_tokens from usage field (GAR-A3)
- extract_total_tokens: total_tokens from usage field (GAR-A3)
- extract_response_tokens: completion_tokens from usage field
- Heuristic fallback via extract_request_tokens when usage absent
"""

import json

import pytest

from tokenpak.proxy.adapters.openai_chat_adapter import OpenAIChatAdapter


@pytest.fixture
def adapter():
    return OpenAIChatAdapter()


def _response(prompt_tokens=None, completion_tokens=None, total_tokens=None):
    usage = {}
    if prompt_tokens is not None:
        usage["prompt_tokens"] = prompt_tokens
    if completion_tokens is not None:
        usage["completion_tokens"] = completion_tokens
    if total_tokens is not None:
        usage["total_tokens"] = total_tokens
    resp = {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [{"message": {"role": "assistant", "content": "Hello"}}],
    }
    if usage:
        resp["usage"] = usage
    return json.dumps(resp).encode()


# ── extract_input_tokens (GAR-A3) ────────────────────────────────────────────


class TestOpenAIExtractInputTokens:
    def test_prompt_tokens_from_usage(self, adapter):
        body = _response(prompt_tokens=100, completion_tokens=42, total_tokens=142)
        assert adapter.extract_input_tokens(body) == 100

    def test_prompt_tokens_without_other_fields(self, adapter):
        body = _response(prompt_tokens=55)
        assert adapter.extract_input_tokens(body) == 55

    def test_no_usage_field_returns_zero(self, adapter):
        """When usage absent, returns 0; caller falls back to heuristic."""
        body = json.dumps({"id": "chatcmpl-test", "choices": []}).encode()
        assert adapter.extract_input_tokens(body) == 0

    def test_usage_present_but_no_prompt_tokens_returns_zero(self, adapter):
        body = _response(completion_tokens=42)  # usage present but no prompt_tokens
        assert adapter.extract_input_tokens(body) == 0

    def test_invalid_body_returns_zero(self, adapter):
        assert adapter.extract_input_tokens(b"not-json") == 0


# ── extract_total_tokens (GAR-A3) ────────────────────────────────────────────


class TestOpenAIExtractTotalTokens:
    def test_total_tokens_from_usage(self, adapter):
        body = _response(prompt_tokens=100, completion_tokens=42, total_tokens=142)
        assert adapter.extract_total_tokens(body) == 142

    def test_total_tokens_without_other_fields(self, adapter):
        body = _response(total_tokens=200)
        assert adapter.extract_total_tokens(body) == 200

    def test_no_usage_field_returns_zero(self, adapter):
        body = json.dumps({"id": "chatcmpl-test", "choices": []}).encode()
        assert adapter.extract_total_tokens(body) == 0

    def test_invalid_body_returns_zero(self, adapter):
        assert adapter.extract_total_tokens(b"not-json") == 0


# ── extract_response_tokens (completion_tokens) ──────────────────────────────


class TestOpenAIExtractResponseTokens:
    def test_completion_tokens_from_usage(self, adapter):
        body = _response(prompt_tokens=100, completion_tokens=42, total_tokens=142)
        assert adapter.extract_response_tokens(body) == 42

    def test_completion_tokens_only(self, adapter):
        body = _response(completion_tokens=7)
        assert adapter.extract_response_tokens(body) == 7

    def test_no_usage_returns_zero(self, adapter):
        body = json.dumps({"choices": []}).encode()
        assert adapter.extract_response_tokens(body) == 0

    def test_invalid_body_returns_zero(self, adapter):
        assert adapter.extract_response_tokens(b"not-json") == 0


# ── Heuristic fallback ───────────────────────────────────────────────────────


class TestOpenAIHeuristicFallback:
    def test_extract_request_tokens_heuristic_still_works(self, adapter):
        """extract_request_tokens (4 chars/token) still functions when usage absent."""
        request_body = json.dumps(
            {
                "model": "gpt-4",
                "messages": [{"role": "user", "content": "Hello, world!"}],
            }
        ).encode()
        model, tokens = adapter.extract_request_tokens(request_body)
        assert model == "gpt-4"
        assert tokens > 0  # "Hello, world!" = 13 chars → 3 tokens by heuristic

    def test_input_tokens_zero_signals_use_heuristic(self, adapter):
        """When usage absent, extract_input_tokens returns 0 (heuristic signal)."""
        response_body = json.dumps({"choices": []}).encode()
        assert adapter.extract_input_tokens(response_body) == 0

        request_body = json.dumps(
            {"model": "gpt-4", "messages": [{"role": "user", "content": "Test prompt"}]}
        ).encode()
        _, heuristic_tokens = adapter.extract_request_tokens(request_body)
        assert heuristic_tokens > 0
