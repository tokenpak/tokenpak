"""Tests for the OpenAI telemetry adapter."""

import pytest
from tokenpak.telemetry.adapters.openai import OpenAIAdapter, _is_codex, _FINISH_REASON_MAP
from tokenpak.telemetry.canonical import (
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Confidence,
    UsageSource,
)


@pytest.fixture
def adapter():
    return OpenAIAdapter()


# ---------------------------------------------------------------------------
# _is_codex helper
# ---------------------------------------------------------------------------

class TestIsCodex:
    def test_codex_in_model_name(self):
        assert _is_codex({"model": "codex-001"}) is True

    def test_codex_case_insensitive(self):
        assert _is_codex({"model": "CODEX-davinci-002"}) is True

    def test_reasoning_key_present(self):
        assert _is_codex({"model": "gpt-4o", "reasoning": {}}) is True

    def test_regular_model_not_codex(self):
        assert _is_codex({"model": "gpt-4o"}) is False

    def test_empty_payload(self):
        assert _is_codex({}) is False


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------

class TestOpenAIDetect:
    def test_chat_completion_object(self, adapter):
        name, score = adapter.detect({"choices": [], "object": "chat.completion"})
        assert name == "openai"
        assert score == 1.0

    def test_choices_only(self, adapter):
        name, score = adapter.detect({"choices": []})
        assert name == "openai"
        assert score == 0.8

    def test_responses_api_with_object(self, adapter):
        name, score = adapter.detect({"output": [], "object": "response"})
        assert name == "openai"
        assert score == 1.0

    def test_responses_api_without_object(self, adapter):
        name, score = adapter.detect({"output": [{"type": "message"}]})
        assert name == "openai"
        assert score == 0.7

    def test_negative_candidates_signal(self, adapter):
        name, score = adapter.detect({"candidates": [], "choices": []})
        assert name == "openai"
        assert score == 0.0

    def test_negative_stop_reason_signal(self, adapter):
        name, score = adapter.detect({"stop_reason": "end_turn"})
        assert name == "openai"
        assert score == 0.0

    def test_gpt_model_prefix(self, adapter):
        name, score = adapter.detect({"model": "gpt-4o"})
        assert name == "openai"
        assert score == 0.5

    def test_empty_payload(self, adapter):
        name, score = adapter.detect({})
        assert name == "openai"
        assert score == 0.0

    def test_output_must_be_list(self, adapter):
        # output is a string, not a list — should not match
        name, score = adapter.detect({"output": "text"})
        assert score == 0.0


# ---------------------------------------------------------------------------
# to_canonical_request()
# ---------------------------------------------------------------------------

class TestOpenAIToCanonicalRequest:
    def test_basic_chat_request(self, adapter):
        raw = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
            "temperature": 0.7,
        }
        req = adapter.to_canonical_request(raw)
        assert isinstance(req, CanonicalRequest)
        assert req.provider == "openai"
        assert req.model == "gpt-4o"
        assert req.messages == [{"role": "user", "content": "Hello"}]
        assert req.params.get("temperature") == 0.7

    def test_tools_preserved(self, adapter):
        raw = {
            "model": "gpt-4o",
            "messages": [],
            "tools": [{"type": "function", "function": {"name": "search"}}],
        }
        req = adapter.to_canonical_request(raw)
        assert len(req.tools) == 1
        assert req.tools[0]["function"]["name"] == "search"

    def test_legacy_functions_merged_into_tools(self, adapter):
        raw = {
            "model": "gpt-4o",
            "messages": [],
            "functions": [{"name": "get_weather", "parameters": {}}],
        }
        req = adapter.to_canonical_request(raw)
        assert len(req.tools) == 1
        assert req.tools[0]["type"] == "function"
        assert req.tools[0]["function"]["name"] == "get_weather"

    def test_codex_flag_added_to_params(self, adapter):
        raw = {"model": "codex-001", "messages": []}
        req = adapter.to_canonical_request(raw)
        assert req.params.get("_tokenpak_codex") is True

    def test_non_codex_no_flag(self, adapter):
        raw = {"model": "gpt-4o", "messages": []}
        req = adapter.to_canonical_request(raw)
        assert "_tokenpak_codex" not in req.params

    def test_model_excluded_from_params(self, adapter):
        raw = {"model": "gpt-4o", "messages": [], "max_tokens": 100}
        req = adapter.to_canonical_request(raw)
        assert "model" not in req.params
        assert req.params["max_tokens"] == 100


# ---------------------------------------------------------------------------
# to_canonical_response()
# ---------------------------------------------------------------------------

class TestOpenAIToCanonicalResponse:
    def test_chat_completion_response(self, adapter):
        raw = {
            "object": "chat.completion",
            "choices": [{"message": {"content": "Hello!"}, "finish_reason": "stop"}],
        }
        resp = adapter.to_canonical_response(raw)
        assert isinstance(resp, CanonicalResponse)
        assert resp.output == "Hello!"
        assert resp.finish_reason == "stop"
        assert resp.error is None

    def test_finish_reason_length_mapped(self, adapter):
        raw = {
            "choices": [{"message": {"content": "..."}, "finish_reason": "length"}]
        }
        resp = adapter.to_canonical_response(raw)
        assert resp.finish_reason == "max_tokens"

    def test_finish_reason_tool_calls(self, adapter):
        raw = {
            "choices": [{"message": {"tool_calls": []}, "finish_reason": "tool_calls"}]
        }
        resp = adapter.to_canonical_response(raw)
        assert resp.finish_reason == "tool_use"

    def test_error_response(self, adapter):
        raw = {"error": {"message": "Invalid API key", "type": "auth_error"}}
        resp = adapter.to_canonical_response(raw)
        assert resp.finish_reason == "error"
        assert "Invalid API key" in resp.error

    def test_error_string(self, adapter):
        raw = {"error": "Something went wrong"}
        resp = adapter.to_canonical_response(raw)
        assert resp.error == "Something went wrong"

    def test_responses_api_output(self, adapter):
        raw = {
            "object": "response",
            "output": [{"type": "message", "content": "Hi"}],
            "status": "completed",
        }
        resp = adapter.to_canonical_response(raw)
        assert resp.finish_reason == "stop"
        assert resp.output == [{"type": "message", "content": "Hi"}]

    def test_empty_choices(self, adapter):
        raw = {"choices": []}
        resp = adapter.to_canonical_response(raw)
        assert resp.output is None

    def test_tool_calls_in_output(self, adapter):
        raw = {
            "choices": [
                {"message": {"tool_calls": [{"id": "call1"}]}, "finish_reason": "tool_calls"}
            ]
        }
        resp = adapter.to_canonical_response(raw)
        assert resp.output == [{"id": "call1"}]


# ---------------------------------------------------------------------------
# extract_usage()
# ---------------------------------------------------------------------------

class TestOpenAIExtractUsage:
    def test_basic_usage(self, adapter):
        raw = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
            }
        }
        usage = adapter.extract_usage(raw)
        assert usage.input_billed == 100
        assert usage.output_billed == 50
        assert usage.cache_read == 0
        assert usage.cache_write == 0
        assert usage.confidence == Confidence.HIGH
        assert usage.usage_source == UsageSource.PROVIDER_REPORTED

    def test_cached_tokens(self, adapter):
        raw = {
            "usage": {
                "prompt_tokens": 200,
                "completion_tokens": 80,
                "prompt_tokens_details": {"cached_tokens": 150},
            }
        }
        usage = adapter.extract_usage(raw)
        assert usage.cache_read == 150
        assert usage.cache_write == 0

    def test_missing_usage(self, adapter):
        usage = adapter.extract_usage({})
        assert usage.confidence == Confidence.LOW
        assert usage.usage_source == UsageSource.UNKNOWN
        assert usage.input_billed == 0
        assert usage.output_billed == 0

    def test_empty_usage_block(self, adapter):
        usage = adapter.extract_usage({"usage": {}})
        assert usage.confidence == Confidence.LOW

    def test_zero_cached_tokens(self, adapter):
        raw = {
            "usage": {
                "prompt_tokens": 50,
                "completion_tokens": 30,
                "prompt_tokens_details": {"cached_tokens": 0},
            }
        }
        usage = adapter.extract_usage(raw)
        assert usage.cache_read == 0

    def test_input_est_equals_input_billed(self, adapter):
        raw = {"usage": {"prompt_tokens": 75, "completion_tokens": 25}}
        usage = adapter.extract_usage(raw)
        assert usage.input_est == usage.input_billed
        assert usage.output_est == usage.output_billed
