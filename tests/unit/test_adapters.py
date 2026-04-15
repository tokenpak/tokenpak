"""
Unit tests for tokenpak.adapters — unified SDK adapter layer.

Tests are fully isolated (no network, no proxy).  HTTP calls are mocked
via unittest.mock so these tests run in any environment.

Coverage:
  - TokenPakAdapter base: __init__ validation, call() pipeline
  - AnthropicAdapter: prepare_request, extract_tokens, error cases
  - OpenAIAdapter: prepare_request, extract_tokens, functions→tools, errors
  - LangChainAdapter: role normalisation, provider routing
  - LiteLLMAdapter: model prefix parsing, provider resolution
  - Exception hierarchy: all four exception types
"""

from __future__ import annotations


import pytest
pytest.importorskip("tokenpak.adapters.anthropic", reason="module not available in current build")
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.adapters.base import (
    TokenPakAdapter,
    TokenPakAdapterError,
    TokenPakAuthError,
    TokenPakConfigError,
    TokenPakTimeoutError,
)
from tokenpak.adapters.anthropic import AnthropicAdapter
from tokenpak.adapters.openai import OpenAIAdapter
from tokenpak.adapters.langchain import LangChainAdapter, _normalise_messages
from tokenpak.adapters.litellm import LiteLLMAdapter, _resolve_provider


# ─── Shared helpers ────────────────────────────────────────────────────────

def _make_mock_response(body: dict, status_code: int = 200) -> MagicMock:
    """Return a mock requests.Response object."""
    mock_resp = MagicMock()
    mock_resp.ok = (status_code < 400)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = body
    mock_resp.text = json.dumps(body)
    return mock_resp


ANTHROPIC_SUCCESS_RESPONSE = {
    "id": "msg_01abc",
    "type": "message",
    "role": "assistant",
    "content": [{"type": "text", "text": "Hello!"}],
    "stop_reason": "end_turn",
    "usage": {
        "input_tokens": 15,
        "output_tokens": 5,
        "cache_read_input_tokens": 10,
        "cache_creation_input_tokens": 3,
    },
}

OPENAI_SUCCESS_RESPONSE = {
    "id": "chatcmpl-abc",
    "object": "chat.completion",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "Hello!"},
            "finish_reason": "stop",
        }
    ],
    "usage": {
        "prompt_tokens": 12,
        "completion_tokens": 4,
        "prompt_tokens_details": {"cached_tokens": 8},
    },
}


# ═══════════════════════════════════════════════════════════════════════════
# Exception hierarchy
# ═══════════════════════════════════════════════════════════════════════════

class TestExceptionHierarchy:
    def test_timeout_is_adapter_error(self):
        err = TokenPakTimeoutError("timed out")
        assert isinstance(err, TokenPakAdapterError)

    def test_auth_is_adapter_error(self):
        err = TokenPakAuthError("unauthorized", status_code=401)
        assert isinstance(err, TokenPakAdapterError)
        assert err.status_code == 401

    def test_config_is_adapter_error(self):
        err = TokenPakConfigError("missing field")
        assert isinstance(err, TokenPakAdapterError)

    def test_adapter_error_raw_field(self):
        raw = {"error": "server error"}
        err = TokenPakAdapterError("server error", status_code=500, raw=raw)
        assert err.raw == raw
        assert err.status_code == 500


# ═══════════════════════════════════════════════════════════════════════════
# TokenPakAdapter base
# ═══════════════════════════════════════════════════════════════════════════

class TestTokenPakAdapterBase:
    def test_empty_base_url_raises(self):
        with pytest.raises(TokenPakConfigError, match="base_url"):
            AnthropicAdapter(base_url="", api_key="sk-test")

    def test_empty_api_key_raises(self):
        with pytest.raises(TokenPakConfigError, match="api_key"):
            AnthropicAdapter(base_url="http://localhost:8767", api_key="")

    def test_trailing_slash_stripped(self):
        adapter = AnthropicAdapter(
            base_url="http://localhost:8767/",
            api_key="sk-test",
        )
        assert adapter.base_url == "http://localhost:8767"

    def test_default_timeout(self):
        adapter = OpenAIAdapter(base_url="http://localhost:8767", api_key="sk-test")
        assert adapter.timeout_s == 120.0

    def test_custom_timeout(self):
        adapter = OpenAIAdapter(
            base_url="http://localhost:8767",
            api_key="sk-test",
            timeout_s=30.0,
        )
        assert adapter.timeout_s == 30.0

    def test_repr(self):
        adapter = OpenAIAdapter(base_url="http://localhost:8767", api_key="sk-test")
        r = repr(adapter)
        assert "OpenAIAdapter" in r
        assert "openai" in r


# ═══════════════════════════════════════════════════════════════════════════
# AnthropicAdapter
# ═══════════════════════════════════════════════════════════════════════════

class TestAnthropicAdapterPrepareRequest:
    def setup_method(self):
        self.adapter = AnthropicAdapter(
            base_url="http://localhost:8767", api_key="sk-ant-test"
        )

    def test_valid_request(self):
        req = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hello"}],
        }
        prepared = self.adapter.prepare_request(req)
        assert prepared["model"] == "claude-3-5-sonnet-20241022"
        assert prepared["stream"] is False

    def test_missing_model_raises(self):
        with pytest.raises(TokenPakConfigError, match="model"):
            self.adapter.prepare_request({
                "max_tokens": 100,
                "messages": [{"role": "user", "content": "x"}],
            })

    def test_missing_max_tokens_raises(self):
        with pytest.raises(TokenPakConfigError, match="max_tokens"):
            self.adapter.prepare_request({
                "model": "claude-3-5-sonnet-20241022",
                "messages": [{"role": "user", "content": "x"}],
            })

    def test_empty_messages_raises(self):
        with pytest.raises(TokenPakConfigError, match="non-empty"):
            self.adapter.prepare_request({
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 100,
                "messages": [],
            })

    def test_invalid_message_structure_raises(self):
        with pytest.raises(TokenPakConfigError, match="role"):
            self.adapter.prepare_request({
                "model": "claude-3-5-sonnet-20241022",
                "max_tokens": 100,
                "messages": [{"content": "no role"}],
            })

    def test_stream_default_false(self):
        req = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Hi"}],
        }
        prepared = self.adapter.prepare_request(req)
        assert prepared["stream"] is False

    def test_stream_explicit_true_preserved(self):
        req = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": True,
        }
        prepared = self.adapter.prepare_request(req)
        assert prepared["stream"] is True


class TestAnthropicAdapterExtractTokens:
    def setup_method(self):
        self.adapter = AnthropicAdapter(
            base_url="http://localhost:8767", api_key="sk-ant-test"
        )

    def test_full_usage_block(self):
        tokens = self.adapter.extract_tokens(ANTHROPIC_SUCCESS_RESPONSE)
        assert tokens["input_tokens"] == 15
        assert tokens["output_tokens"] == 5
        assert tokens["cache_read"] == 10
        assert tokens["cache_write"] == 3
        assert tokens["total"] == 20

    def test_missing_usage_returns_zeros(self):
        tokens = self.adapter.extract_tokens({"content": []})
        assert tokens == {
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read": 0,
            "cache_write": 0,
            "total": 0,
        }

    def test_partial_usage_block(self):
        response = {"usage": {"input_tokens": 5}}
        tokens = self.adapter.extract_tokens(response)
        assert tokens["input_tokens"] == 5
        assert tokens["output_tokens"] == 0
        assert tokens["total"] == 5


class TestAnthropicAdapterSend:
    def setup_method(self):
        self.adapter = AnthropicAdapter(
            base_url="http://localhost:8767", api_key="sk-ant-test"
        )
        self.prepared = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Hi"}],
            "stream": False,
        }

    @patch("tokenpak.adapters.anthropic._requests")
    def test_successful_send(self, mock_requests):
        mock_requests.post.return_value = _make_mock_response(ANTHROPIC_SUCCESS_RESPONSE)
        mock_requests.exceptions.Timeout = Exception
        mock_requests.exceptions.RequestException = Exception
        result = self.adapter.send(self.prepared)
        assert result["type"] == "message"

    @patch("tokenpak.adapters.anthropic._requests")
    def test_timeout_raises_tokenpak_timeout(self, mock_requests):
        import requests as real_requests
        mock_requests.exceptions.Timeout = real_requests.exceptions.Timeout
        mock_requests.exceptions.RequestException = real_requests.exceptions.RequestException
        mock_requests.post.side_effect = real_requests.exceptions.Timeout("timed out")
        with pytest.raises(TokenPakTimeoutError):
            self.adapter.send(self.prepared)

    @patch("tokenpak.adapters.anthropic._requests")
    def test_401_raises_auth_error(self, mock_requests):
        mock_requests.post.return_value = _make_mock_response({"error": "unauthorized"}, 401)
        mock_requests.exceptions.Timeout = Exception
        mock_requests.exceptions.RequestException = Exception
        with pytest.raises(TokenPakAuthError):
            self.adapter.send(self.prepared)

    @patch("tokenpak.adapters.anthropic._requests")
    def test_500_raises_adapter_error(self, mock_requests):
        mock_requests.post.return_value = _make_mock_response({"error": "server error"}, 500)
        mock_requests.exceptions.Timeout = Exception
        mock_requests.exceptions.RequestException = Exception
        with pytest.raises(TokenPakAdapterError):
            self.adapter.send(self.prepared)


class TestAnthropicAdapterParseResponse:
    def setup_method(self):
        self.adapter = AnthropicAdapter(
            base_url="http://localhost:8767", api_key="sk-ant-test"
        )

    def test_valid_response_passthrough(self):
        result = self.adapter.parse_response(ANTHROPIC_SUCCESS_RESPONSE)
        assert result == ANTHROPIC_SUCCESS_RESPONSE

    def test_error_block_raises(self):
        response = {"error": {"type": "invalid_request_error", "message": "Bad request"}}
        with pytest.raises(TokenPakAdapterError, match="invalid_request_error"):
            self.adapter.parse_response(response)

    def test_string_error_raises(self):
        response = {"error": "Something went wrong"}
        with pytest.raises(TokenPakAdapterError):
            self.adapter.parse_response(response)


# ═══════════════════════════════════════════════════════════════════════════
# OpenAIAdapter
# ═══════════════════════════════════════════════════════════════════════════

class TestOpenAIAdapterPrepareRequest:
    def setup_method(self):
        self.adapter = OpenAIAdapter(
            base_url="http://localhost:8767", api_key="sk-test"
        )

    def test_valid_request(self):
        req = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        prepared = self.adapter.prepare_request(req)
        assert prepared["model"] == "gpt-4o"
        assert prepared["stream"] is False

    def test_missing_model_raises(self):
        with pytest.raises(TokenPakConfigError, match="model"):
            self.adapter.prepare_request({
                "messages": [{"role": "user", "content": "x"}],
            })

    def test_missing_messages_raises(self):
        with pytest.raises(TokenPakConfigError, match="messages"):
            self.adapter.prepare_request({"model": "gpt-4o"})

    def test_functions_promoted_to_tools(self):
        req = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "x"}],
            "functions": [{"name": "get_weather", "parameters": {}}],
        }
        prepared = self.adapter.prepare_request(req)
        assert "functions" not in prepared
        assert len(prepared["tools"]) == 1
        assert prepared["tools"][0]["type"] == "function"
        assert prepared["tools"][0]["function"]["name"] == "get_weather"

    def test_functions_discarded_when_tools_present(self):
        req = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "x"}],
            "tools": [{"type": "function", "function": {"name": "foo"}}],
            "functions": [{"name": "bar"}],
        }
        prepared = self.adapter.prepare_request(req)
        assert "functions" not in prepared
        assert len(prepared["tools"]) == 1
        assert prepared["tools"][0]["function"]["name"] == "foo"

    def test_missing_role_in_message_raises(self):
        with pytest.raises(TokenPakConfigError, match="role"):
            self.adapter.prepare_request({
                "model": "gpt-4o",
                "messages": [{"content": "no role"}],
            })


class TestOpenAIAdapterExtractTokens:
    def setup_method(self):
        self.adapter = OpenAIAdapter(
            base_url="http://localhost:8767", api_key="sk-test"
        )

    def test_full_usage_block(self):
        tokens = self.adapter.extract_tokens(OPENAI_SUCCESS_RESPONSE)
        assert tokens["input_tokens"] == 12
        assert tokens["output_tokens"] == 4
        assert tokens["cache_read"] == 8
        assert tokens["cache_write"] == 0  # OpenAI doesn't expose this
        assert tokens["total"] == 16

    def test_missing_usage_returns_zeros(self):
        tokens = self.adapter.extract_tokens({"choices": []})
        assert tokens["total"] == 0

    def test_no_cached_tokens_detail(self):
        response = {
            "usage": {"prompt_tokens": 10, "completion_tokens": 5}
        }
        tokens = self.adapter.extract_tokens(response)
        assert tokens["cache_read"] == 0
        assert tokens["total"] == 15


class TestOpenAIAdapterParseResponse:
    def setup_method(self):
        self.adapter = OpenAIAdapter(
            base_url="http://localhost:8767", api_key="sk-test"
        )

    def test_valid_response_passthrough(self):
        result = self.adapter.parse_response(OPENAI_SUCCESS_RESPONSE)
        assert result == OPENAI_SUCCESS_RESPONSE

    def test_error_block_raises(self):
        response = {"error": {"type": "invalid_request_error", "message": "Bad"}}
        with pytest.raises(TokenPakAdapterError, match="invalid_request_error"):
            self.adapter.parse_response(response)


# ═══════════════════════════════════════════════════════════════════════════
# LangChainAdapter
# ═══════════════════════════════════════════════════════════════════════════

class TestNormaliseMessages:
    def test_human_to_user(self):
        msgs = [{"role": "human", "content": "hi"}]
        result = _normalise_messages(msgs)
        assert result[0]["role"] == "user"

    def test_ai_to_assistant(self):
        msgs = [{"role": "ai", "content": "hello"}]
        result = _normalise_messages(msgs)
        assert result[0]["role"] == "assistant"

    def test_system_unchanged(self):
        msgs = [{"role": "system", "content": "instructions"}]
        result = _normalise_messages(msgs)
        assert result[0]["role"] == "system"

    def test_unknown_role_passthrough(self):
        msgs = [{"role": "oracle", "content": "mystic"}]
        result = _normalise_messages(msgs)
        assert result[0]["role"] == "oracle"

    def test_preserves_content(self):
        msgs = [{"role": "human", "content": "test"}]
        result = _normalise_messages(msgs)
        assert result[0]["content"] == "test"

    def test_does_not_mutate_input(self):
        original = [{"role": "human", "content": "x"}]
        _normalise_messages(original)
        assert original[0]["role"] == "human"  # unchanged


class TestLangChainAdapterPrepareRequest:
    def setup_method(self):
        self.adapter = LangChainAdapter(
            base_url="http://localhost:8767", api_key="sk-test"
        )

    def test_human_role_normalised(self):
        req = {
            "model": "gpt-4o",
            "provider": "openai",
            "messages": [{"role": "human", "content": "Hello"}],
        }
        prepared = self.adapter.prepare_request(req)
        assert prepared["messages"][0]["role"] == "user"

    def test_provider_field_stripped(self):
        req = {
            "model": "gpt-4o",
            "provider": "openai",
            "messages": [{"role": "user", "content": "Hello"}],
        }
        prepared = self.adapter.prepare_request(req)
        assert "provider" not in prepared

    def test_missing_messages_raises(self):
        with pytest.raises(TokenPakConfigError, match="messages"):
            self.adapter.prepare_request({"model": "gpt-4o", "provider": "openai"})

    def test_unknown_provider_raises(self):
        req = {
            "model": "llama-3",
            "provider": "meta",
            "messages": [{"role": "user", "content": "hi"}],
        }
        with pytest.raises(TokenPakConfigError, match="unknown provider"):
            self.adapter.prepare_request(req)

    def test_anthropic_provider_routing(self):
        req = {
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 100,
            "provider": "anthropic",
            "messages": [{"role": "human", "content": "Hello"}],
        }
        # Should not raise — routes to AnthropicAdapter which requires max_tokens
        prepared = self.adapter.prepare_request(req)
        assert prepared["model"] == "claude-3-5-sonnet-20241022"


class TestLangChainAdapterTokenExtraction:
    def setup_method(self):
        self.adapter = LangChainAdapter(
            base_url="http://localhost:8767", api_key="sk-test"
        )

    def test_extracts_from_anthropic_response(self):
        tokens = self.adapter.extract_tokens(ANTHROPIC_SUCCESS_RESPONSE)
        assert tokens["input_tokens"] == 15
        assert tokens["output_tokens"] == 5

    def test_extracts_from_openai_response(self):
        tokens = self.adapter.extract_tokens(OPENAI_SUCCESS_RESPONSE)
        assert tokens["input_tokens"] == 12
        assert tokens["output_tokens"] == 4


# ═══════════════════════════════════════════════════════════════════════════
# LiteLLMAdapter
# ═══════════════════════════════════════════════════════════════════════════

class TestResolveProvider:
    def test_anthropic_prefix(self):
        key, bare = _resolve_provider("anthropic/claude-3-5-sonnet-20241022")
        assert key == "anthropic"
        assert bare == "claude-3-5-sonnet-20241022"

    def test_openai_prefix(self):
        key, bare = _resolve_provider("openai/gpt-4o")
        assert key == "openai"
        assert bare == "gpt-4o"

    def test_gpt_no_prefix(self):
        key, bare = _resolve_provider("gpt-4o")
        assert key == "openai"
        assert bare == "gpt-4o"

    def test_claude_no_prefix(self):
        key, bare = _resolve_provider("claude-3-5-sonnet-20241022")
        assert key == "anthropic"

    def test_unknown_prefix_defaults_to_openai(self):
        key, bare = _resolve_provider("meta/llama-3")
        assert key == "openai"
        assert bare == "llama-3"

    def test_o1_model(self):
        key, _ = _resolve_provider("o1-preview")
        assert key == "openai"

    def test_o3_model(self):
        key, _ = _resolve_provider("o3-mini")
        assert key == "openai"


class TestLiteLLMAdapterPrepareRequest:
    def setup_method(self):
        self.adapter = LiteLLMAdapter(
            base_url="http://localhost:8767", api_key="sk-test"
        )

    def test_openai_prefix_stripped(self):
        req = {
            "model": "openai/gpt-4o",
            "messages": [{"role": "user", "content": "Hi"}],
        }
        prepared = self.adapter.prepare_request(req)
        assert prepared["model"] == "gpt-4o"

    def test_anthropic_prefix_stripped(self):
        req = {
            "model": "anthropic/claude-3-5-sonnet-20241022",
            "max_tokens": 100,
            "messages": [{"role": "user", "content": "Hi"}],
        }
        prepared = self.adapter.prepare_request(req)
        assert prepared["model"] == "claude-3-5-sonnet-20241022"

    def test_missing_model_raises(self):
        with pytest.raises(TokenPakConfigError, match="model"):
            self.adapter.prepare_request({
                "messages": [{"role": "user", "content": "hi"}]
            })

    def test_missing_messages_raises(self):
        with pytest.raises(TokenPakConfigError, match="messages"):
            self.adapter.prepare_request({"model": "gpt-4o"})

    def test_tokenpak_source_injected(self):
        req = {
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "hi"}],
        }
        prepared = self.adapter.prepare_request(req)
        assert prepared.get("_tokenpak_source") == "litellm"


class TestLiteLLMAdapterTokenExtraction:
    def setup_method(self):
        self.adapter = LiteLLMAdapter(
            base_url="http://localhost:8767", api_key="sk-test"
        )

    def test_extracts_from_anthropic_response(self):
        tokens = self.adapter.extract_tokens(ANTHROPIC_SUCCESS_RESPONSE)
        assert tokens["input_tokens"] == 15

    def test_extracts_from_openai_response(self):
        tokens = self.adapter.extract_tokens(OPENAI_SUCCESS_RESPONSE)
        assert tokens["input_tokens"] == 12


# ═══════════════════════════════════════════════════════════════════════════
# Integration smoke test (no network)
# ═══════════════════════════════════════════════════════════════════════════

class TestAdapterPipeline:
    """Test the full prepare→send→parse→extract pipeline with mocked HTTP."""

    @patch("tokenpak.adapters.openai._requests")
    def test_openai_full_pipeline(self, mock_requests):
        import requests as real_requests
        mock_requests.post.return_value = _make_mock_response(OPENAI_SUCCESS_RESPONSE)
        mock_requests.exceptions.Timeout = real_requests.exceptions.Timeout
        mock_requests.exceptions.RequestException = real_requests.exceptions.RequestException

        adapter = OpenAIAdapter(base_url="http://localhost:8767", api_key="sk-test")
        response = adapter.call({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": "Hello"}],
        })
        tokens = adapter.extract_tokens(response)

        assert response["object"] == "chat.completion"
        assert tokens["input_tokens"] == 12
        assert tokens["output_tokens"] == 4
        assert tokens["total"] == 16

    @patch("tokenpak.adapters.anthropic._requests")
    def test_anthropic_full_pipeline(self, mock_requests):
        import requests as real_requests
        mock_requests.post.return_value = _make_mock_response(ANTHROPIC_SUCCESS_RESPONSE)
        mock_requests.exceptions.Timeout = real_requests.exceptions.Timeout
        mock_requests.exceptions.RequestException = real_requests.exceptions.RequestException

        adapter = AnthropicAdapter(base_url="http://localhost:8767", api_key="sk-ant-test")
        response = adapter.call({
            "model": "claude-3-5-sonnet-20241022",
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": "Hello"}],
        })
        tokens = adapter.extract_tokens(response)

        assert response["type"] == "message"
        assert tokens["input_tokens"] == 15
        assert tokens["cache_read"] == 10
        assert tokens["cache_write"] == 3
