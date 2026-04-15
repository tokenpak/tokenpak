"""Integration tests for multi-provider TokenPak proxy adapter flows.

Covers:
- Request normalization + response/shape denormalization roundtrip
- Compression savings on provider payload content
- No semantic loss on preserved key phrase
- Error handling on malformed payloads
"""

from __future__ import annotations


import pytest
pytest.importorskip("tokenpak.pack", reason="module not available in current build")
import json
from pathlib import Path

import pytest

from tokenpak.pack import ContextPack, PackBlock
from tokenpak.proxy.adapters import (
    AnthropicAdapter,
    GoogleGenerativeAIAdapter,
    OpenAIChatAdapter,
    OpenAIResponsesAdapter,
    PassthroughAdapter,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _pack_for_savings(text: str) -> tuple[str, int, int]:
    pack = ContextPack(budget=48)
    pack.add(
        PackBlock(
            id="provider-prompt",
            type="conversation",
            content=text,
            priority="high",
            max_tokens=48,
        )
    )
    compiled = pack.compile()
    return compiled.text, compiled.report.input_tokens, compiled.report.output_tokens


class TestAnthropicProviderIntegration:
    def test_request_normalization_and_roundtrip(self):
        adapter = AnthropicAdapter()
        request = _load("anthropic_messages_request.json")

        canonical = adapter.normalize(json.dumps(request).encode("utf-8"))
        restored = json.loads(adapter.denormalize(canonical))

        assert canonical.model == request["model"]
        assert canonical.messages[0]["role"] == "user"
        assert restored["model"] == request["model"]
        assert restored["messages"][0]["content"] == request["messages"][0]["content"]

    def test_compression_savings_and_semantics(self):
        adapter = AnthropicAdapter()
        request = _load("anthropic_messages_request.json")
        canonical = adapter.normalize(json.dumps(request).encode("utf-8"))
        prompt_text = (
            f"{canonical.messages[0]['content']} "
            "CRITICAL_FACT: project codenamed ORBIT. "
            "Repeat this detail for compression testing. " * 10
        )

        compressed, before, after = _pack_for_savings(prompt_text)

        assert after < before
        assert "CRITICAL_FACT: project codenamed ORBIT" in compressed


class TestOpenAIProviderIntegration:
    def test_request_normalization_and_roundtrip(self):
        adapter = OpenAIChatAdapter()
        request = _load("openai_chat_request.json")

        canonical = adapter.normalize(json.dumps(request).encode("utf-8"))
        restored = json.loads(adapter.denormalize(canonical))

        assert canonical.model == request["model"]
        assert canonical.messages[0]["role"] == "user"
        assert restored["messages"][0]["role"] in {"system", "user"}
        assert request["messages"][-1]["content"] in json.dumps(restored)

    def test_compression_savings_and_semantics(self):
        adapter = OpenAIChatAdapter()
        request = _load("openai_chat_request.json")
        canonical = adapter.normalize(json.dumps(request).encode("utf-8"))
        prompt_text = (
            f"{canonical.messages[-1]['content']} "
            "CRITICAL_FACT: project codenamed ORBIT. "
            "Repeat this detail for compression testing. " * 10
        )

        compressed, before, after = _pack_for_savings(prompt_text)

        assert after < before
        assert "CRITICAL_FACT: project codenamed ORBIT" in compressed


class TestGoogleProviderIntegration:
    def test_request_normalization_and_roundtrip(self):
        adapter = GoogleGenerativeAIAdapter()
        request = _load("google_generate_request.json")

        canonical = adapter.normalize(json.dumps(request).encode("utf-8"))
        restored = json.loads(adapter.denormalize(canonical))

        assert canonical.model == request["model"]
        assert canonical.messages[0]["role"] == "user"
        assert restored["systemInstruction"]["parts"][0]["text"] == request["systemInstruction"]["parts"][0]["text"]
        assert restored["contents"][0]["parts"][0]["text"].startswith("CRITICAL_FACT")

    def test_compression_savings_and_semantics(self):
        adapter = GoogleGenerativeAIAdapter()
        request = _load("google_generate_request.json")
        canonical = adapter.normalize(json.dumps(request).encode("utf-8"))
        prompt_text = canonical.messages[0]["content"][0]["text"]

        compressed, before, after = _pack_for_savings(prompt_text)

        assert after < before
        assert "CRITICAL_FACT: project codenamed ORBIT" in compressed


class TestCodexProviderIntegration:
    def test_request_normalization_and_roundtrip(self):
        adapter = OpenAIResponsesAdapter()
        request = _load("codex_responses_request.json")

        canonical = adapter.normalize(json.dumps(request).encode("utf-8"))
        restored = json.loads(adapter.denormalize(canonical))

        assert canonical.model.startswith("gpt-5")
        assert "codex" in canonical.model
        assert restored["model"] == request["model"]
        assert restored["input"] == request["input"]

    def test_compression_savings_and_semantics(self):
        adapter = OpenAIResponsesAdapter()
        request = _load("codex_responses_request.json")
        canonical = adapter.normalize(json.dumps(request).encode("utf-8"))
        prompt_text = str(canonical.messages[-1]["content"])

        compressed, before, after = _pack_for_savings(prompt_text)

        assert after < before
        assert "CRITICAL_FACT: project codenamed ORBIT" in compressed


@pytest.mark.parametrize(
    "adapter,body",
    [
        (AnthropicAdapter(), b"not-json"),
        (OpenAIChatAdapter(), b"not-json"),
        (GoogleGenerativeAIAdapter(), b"not-json"),
        (OpenAIResponsesAdapter(), b"not-json"),
    ],
)
def test_error_handling_malformed_json(adapter, body):
    with pytest.raises(Exception):
        adapter.normalize(body)


def test_passthrough_handles_malformed_json_without_crashing():
    canonical = PassthroughAdapter().normalize(b"not-json")
    assert canonical.model == "unknown"
    assert canonical.source_format == "passthrough"
