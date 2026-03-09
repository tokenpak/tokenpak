"""Tests for the Anthropic telemetry adapter."""

import pytest
from tokenpak.telemetry.adapters.anthropic import AnthropicAdapter, _STOP_REASON_MAP
from tokenpak.telemetry.canonical import (
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Confidence,
    UsageSource,
)


@pytest.fixture
def adapter():
    return AnthropicAdapter()


# ---------------------------------------------------------------------------
# detect()
# ---------------------------------------------------------------------------

class TestAnthropicDetect:
    def test_anthropic_version_key(self, adapter):
        name, score = adapter.detect({"anthropic-version": "2023-06-01"})
        assert name == "anthropic"
        assert score >= 0.7

    def test_type_message(self, adapter):
        name, score = adapter.detect({"type": "message", "content": []})
        assert score >= 0.5

    def test_stop_reason_key(self, adapter):
        name, score = adapter.detect({"stop_reason": "end_turn"})
        assert score >= 0.4

    def test_content_list_with_type(self, adapter):
        raw = {
            "type": "message",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "Hello"}],
        }
        name, score = adapter.detect(raw)
        assert score == 1.0

    def test_all_signals_combined(self, adapter):
        raw = {
            "anthropic-version": "2023-06-01",
            "type": "message",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "Hi"}],
        }
        name, score = adapter.detect(raw)
        assert score == 1.0

    def test_negative_choices_key(self, adapter):
        name, score = adapter.detect({"choices": [], "stop_reason": "end_turn"})
        assert score == 0.0

    def test_negative_candidates_key(self, adapter):
        name, score = adapter.detect({"candidates": [], "stop_reason": "end_turn"})
        assert score == 0.0

    def test_empty_payload(self, adapter):
        name, score = adapter.detect({})
        assert score == 0.0


# ---------------------------------------------------------------------------
# to_canonical_request()
# ---------------------------------------------------------------------------

class TestAnthropicToCanonicalRequest:
    def test_basic_request(self, adapter):
        raw = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Hello"}],
            "max_tokens": 1024,
        }
        req = adapter.to_canonical_request(raw)
        assert isinstance(req, CanonicalRequest)
        assert req.provider == "anthropic"
        assert req.model == "claude-3-5-sonnet-20241022"

    def test_system_string_injected_as_first_message(self, adapter):
        raw = {
            "model": "claude-3-5-sonnet-20241022",
            "system": "You are a helpful assistant.",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        req = adapter.to_canonical_request(raw)
        assert req.messages[0]["role"] == "system"
        assert req.messages[0]["content"] == "You are a helpful assistant."
        assert req.messages[1]["role"] == "user"

    def test_system_list_injected(self, adapter):
        raw = {
            "model": "claude-3-5-sonnet-20241022",
            "system": [{"type": "text", "text": "You are helpful."}],
            "messages": [],
        }
        req = adapter.to_canonical_request(raw)
        assert req.messages[0]["role"] == "system"
        assert isinstance(req.messages[0]["content"], list)

    def test_no_system_no_injection(self, adapter):
        raw = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [{"role": "user", "content": "Hi"}],
        }
        req = adapter.to_canonical_request(raw)
        assert req.messages[0]["role"] == "user"

    def test_tools_preserved(self, adapter):
        raw = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [],
            "tools": [{"name": "search", "input_schema": {}}],
        }
        req = adapter.to_canonical_request(raw)
        assert len(req.tools) == 1

    def test_model_excluded_from_params(self, adapter):
        raw = {
            "model": "claude-3-5-sonnet-20241022",
            "messages": [],
            "temperature": 0.5,
        }
        req = adapter.to_canonical_request(raw)
        assert "model" not in req.params
        assert req.params["temperature"] == 0.5

    def test_raw_preserved(self, adapter):
        raw = {"model": "claude-3-5-haiku-20241022", "messages": []}
        req = adapter.to_canonical_request(raw)
        assert req.raw is raw


# ---------------------------------------------------------------------------
# to_canonical_response()
# ---------------------------------------------------------------------------

class TestAnthropicToCanonicalResponse:
    def test_text_response(self, adapter):
        raw = {
            "type": "message",
            "content": [{"type": "text", "text": "Hello!"}],
            "stop_reason": "end_turn",
        }
        resp = adapter.to_canonical_response(raw)
        assert isinstance(resp, CanonicalResponse)
        assert resp.finish_reason == "stop"
        assert resp.output == [{"type": "text", "text": "Hello!"}]
        assert resp.error is None

    def test_stop_reason_max_tokens(self, adapter):
        raw = {
            "content": [{"type": "text", "text": "..."}],
            "stop_reason": "max_tokens",
        }
        resp = adapter.to_canonical_response(raw)
        assert resp.finish_reason == "max_tokens"

    def test_stop_reason_tool_use(self, adapter):
        raw = {
            "content": [{"type": "tool_use", "name": "search"}],
            "stop_reason": "tool_use",
        }
        resp = adapter.to_canonical_response(raw)
        assert resp.finish_reason == "tool_use"

    def test_error_dict_response(self, adapter):
        raw = {"error": {"type": "overloaded_error", "message": "Overloaded"}}
        resp = adapter.to_canonical_response(raw)
        assert "Overloaded" in resp.error

    def test_error_string_response(self, adapter):
        raw = {"error": "Bad request"}
        resp = adapter.to_canonical_response(raw)
        assert resp.error == "Bad request"

    def test_unknown_stop_reason(self, adapter):
        raw = {"content": [], "stop_reason": "some_new_reason"}
        resp = adapter.to_canonical_response(raw)
        assert resp.finish_reason == "some_new_reason"

    def test_no_stop_reason_defaults_unknown(self, adapter):
        raw = {"content": []}
        resp = adapter.to_canonical_response(raw)
        assert resp.finish_reason == "unknown"


# ---------------------------------------------------------------------------
# extract_usage()
# ---------------------------------------------------------------------------

class TestAnthropicExtractUsage:
    def test_basic_usage(self, adapter):
        raw = {
            "usage": {
                "input_tokens": 150,
                "output_tokens": 75,
            }
        }
        usage = adapter.extract_usage(raw)
        assert usage.input_billed == 150
        assert usage.output_billed == 75
        assert usage.cache_read == 0
        assert usage.cache_write == 0
        assert usage.confidence == Confidence.HIGH
        assert usage.usage_source == UsageSource.PROVIDER_REPORTED

    def test_cache_read_tokens(self, adapter):
        raw = {
            "usage": {
                "input_tokens": 200,
                "output_tokens": 50,
                "cache_read_input_tokens": 180,
            }
        }
        usage = adapter.extract_usage(raw)
        assert usage.cache_read == 180

    def test_cache_write_tokens(self, adapter):
        raw = {
            "usage": {
                "input_tokens": 200,
                "output_tokens": 50,
                "cache_creation_input_tokens": 200,
            }
        }
        usage = adapter.extract_usage(raw)
        assert usage.cache_write == 200

    def test_full_cache_usage(self, adapter):
        raw = {
            "usage": {
                "input_tokens": 300,
                "output_tokens": 100,
                "cache_read_input_tokens": 250,
                "cache_creation_input_tokens": 50,
            }
        }
        usage = adapter.extract_usage(raw)
        assert usage.cache_read == 250
        assert usage.cache_write == 50

    def test_missing_usage_block(self, adapter):
        usage = adapter.extract_usage({})
        assert usage.confidence == Confidence.LOW
        assert usage.usage_source == UsageSource.UNKNOWN

    def test_empty_usage_block(self, adapter):
        usage = adapter.extract_usage({"usage": {}})
        assert usage.confidence == Confidence.LOW

    def test_input_est_matches_billed(self, adapter):
        raw = {"usage": {"input_tokens": 100, "output_tokens": 40}}
        usage = adapter.extract_usage(raw)
        assert usage.input_est == 100
        assert usage.output_est == 40
