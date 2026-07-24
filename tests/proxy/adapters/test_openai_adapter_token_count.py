"""Unit tests for OpenAI chat adapter input/total token count parsing (GAR-A3).

Covers:
- extract_input_tokens: prompt_tokens from usage field
- extract_total_tokens: total_tokens from usage field
- Heuristic fallback when usage field is absent
"""

import json
import pathlib

import pytest

from tokenpak.proxy.adapters.openai_chat_adapter import OpenAIChatAdapter

FIXTURES = pathlib.Path(__file__).parent.parent.parent / "fixtures"


@pytest.fixture
def adapter():
    return OpenAIChatAdapter()


# ── Real fixture ──────────────────────────────────────────────────────────────


class TestOpenAITokenCountFromFixture:
    """Parse token counts from the real openai_chat_response.json fixture."""

    def test_input_tokens_from_fixture(self, adapter):
        body = (FIXTURES / "openai_chat_response.json").read_bytes()
        # Fixture has prompt_tokens: 31
        assert adapter.extract_input_tokens(body) == 31

    def test_total_tokens_from_fixture(self, adapter):
        body = (FIXTURES / "openai_chat_response.json").read_bytes()
        # Fixture has total_tokens: 45
        assert adapter.extract_total_tokens(body) == 45


# ── extract_input_tokens ──────────────────────────────────────────────────────


class TestOpenAIExtractInputTokens:
    def test_prompt_tokens_parsed(self, adapter):
        body = json.dumps(
            {"usage": {"prompt_tokens": 100, "completion_tokens": 42, "total_tokens": 142}}
        ).encode()
        assert adapter.extract_input_tokens(body) == 100

    def test_prompt_tokens_without_total(self, adapter):
        body = json.dumps({"usage": {"prompt_tokens": 55}}).encode()
        assert adapter.extract_input_tokens(body) == 55

    def test_no_usage_returns_zero(self, adapter):
        """Absent usage field returns 0; caller falls back to heuristic."""
        body = json.dumps({"choices": []}).encode()
        assert adapter.extract_input_tokens(body) == 0

    def test_usage_missing_prompt_tokens_returns_zero(self, adapter):
        body = json.dumps({"usage": {"completion_tokens": 42}}).encode()
        assert adapter.extract_input_tokens(body) == 0

    def test_invalid_body_returns_zero(self, adapter):
        assert adapter.extract_input_tokens(b"not-json") == 0


# ── extract_total_tokens ──────────────────────────────────────────────────────


class TestOpenAIExtractTotalTokens:
    def test_total_tokens_parsed(self, adapter):
        body = json.dumps(
            {"usage": {"prompt_tokens": 100, "completion_tokens": 42, "total_tokens": 142}}
        ).encode()
        assert adapter.extract_total_tokens(body) == 142

    def test_total_tokens_without_other_fields(self, adapter):
        body = json.dumps({"usage": {"total_tokens": 200}}).encode()
        assert adapter.extract_total_tokens(body) == 200

    def test_no_usage_returns_zero(self, adapter):
        body = json.dumps({"choices": []}).encode()
        assert adapter.extract_total_tokens(body) == 0

    def test_invalid_body_returns_zero(self, adapter):
        assert adapter.extract_total_tokens(b"not-json") == 0


# ── Heuristic fallback ────────────────────────────────────────────────────────


class TestOpenAIHeuristicFallback:
    def test_zero_input_tokens_signals_use_heuristic(self, adapter):
        """When usage absent, extract_input_tokens returns 0 (heuristic signal)."""
        response_body = json.dumps({"choices": []}).encode()
        assert adapter.extract_input_tokens(response_body) == 0

        # extract_request_tokens (4 chars/token heuristic) still produces a count
        request_body = json.dumps(
            {"model": "gpt-4o", "messages": [{"role": "user", "content": "Test prompt"}]}
        ).encode()
        _, heuristic_tokens = adapter.extract_request_tokens(request_body)
        assert heuristic_tokens > 0

    def test_heuristic_not_used_when_usage_present(self, adapter):
        """When usage present, extract_input_tokens returns exact count, not heuristic."""
        body = json.dumps({"usage": {"prompt_tokens": 999, "total_tokens": 1000}}).encode()
        assert adapter.extract_input_tokens(body) == 999
