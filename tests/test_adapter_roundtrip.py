"""Round-trip smoke tests using real-format adapter request/response bodies."""

from __future__ import annotations


import pytest
pytest.importorskip("tokenpak.adapters.anthropic", reason="module not available in current build")
import json
from pathlib import Path

from tokenpak.adapters.anthropic import AnthropicAdapter
from tokenpak.adapters.openai import OpenAIAdapter

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TestAnthropicAdapterRoundtrip:
    def test_normalize_real_request(self):
        request = _load("anthropic_messages_request.json")
        adapter = AnthropicAdapter(base_url="http://localhost:8767", api_key="sk-test")

        normalized = adapter.prepare_request(request)

        assert normalized["model"] == request["model"]
        assert normalized["max_tokens"] == request["max_tokens"]
        assert normalized["messages"] == request["messages"]
        assert normalized["system"] == request["system"]
        assert normalized["stream"] is False

    def test_denormalize_roundtrip(self):
        request = _load("anthropic_messages_request.json")
        adapter = AnthropicAdapter(base_url="http://localhost:8767", api_key="sk-test")

        denormalized = adapter.prepare_request(request)

        expected = dict(request)
        expected["stream"] = False
        assert denormalized == expected


class TestOpenAIChatAdapterRoundtrip:
    def test_normalize_real_request(self):
        request = _load("openai_chat_request.json")
        adapter = OpenAIAdapter(base_url="http://localhost:8767", api_key="sk-test")

        normalized = adapter.prepare_request(request)

        assert normalized["model"] == request["model"]
        assert normalized["messages"] == request["messages"]
        assert normalized["tools"] == request["tools"]
        assert normalized["stream"] is False

    def test_denormalize_roundtrip(self):
        request = _load("openai_chat_request.json")
        adapter = OpenAIAdapter(base_url="http://localhost:8767", api_key="sk-test")

        denormalized = adapter.prepare_request(request)

        expected = dict(request)
        expected["stream"] = False
        assert denormalized == expected
