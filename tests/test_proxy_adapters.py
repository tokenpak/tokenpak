"""Tests for provider-agnostic proxy adapters."""

from __future__ import annotations

import json

from tokenpak.proxy.adapters import (
    AdapterRegistry,
    AnthropicAdapter,
    GoogleGenerativeAIAdapter,
    OpenAIChatAdapter,
    OpenAIResponsesAdapter,
    PassthroughAdapter,
    build_default_registry,
)


def _to_bytes(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


class TestAdapterRegistry:
    def test_priority_detection(self):
        reg = AdapterRegistry()
        reg.register(PassthroughAdapter(), priority=0)
        reg.register(OpenAIChatAdapter(), priority=200)
        reg.register(AnthropicAdapter(), priority=300)

        adapter = reg.detect("/v1/messages", {"x-api-key": "sk-ant"}, b"{}")
        assert adapter.source_format == "anthropic-messages"

    def test_default_registry_detects_all_known_formats(self):
        reg = build_default_registry()

        assert reg.detect("/v1/messages", {"x-api-key": "k"}, b"{}").source_format == "anthropic-messages"
        assert reg.detect("/v1/chat/completions", {"Authorization": "Bearer x"}, b"{}").source_format == "openai-chat"
        assert reg.detect("/v1/responses", {"Authorization": "Bearer x"}, b"{}").source_format == "openai-responses"
        assert reg.detect("/v1beta/models/gemini-2-flash:generateContent", {"x-goog-api-key": "k"}, b"{}").source_format == "google-generative-ai"
        assert reg.detect("/custom/provider", {}, b"{}").source_format == "passthrough"


class TestAnthropicAdapter:
    def setup_method(self):
        self.adapter = AnthropicAdapter()

    def test_round_trip_preserves_cache_control(self):
        body = _to_bytes(
            {
                "model": "claude-sonnet-4-6",
                "system": [
                    {"type": "text", "text": "You are helpful."},
                    {
                        "type": "text",
                        "text": "Stable context",
                        "cache_control": {"type": "ephemeral"},
                    },
                ],
                "messages": [{"role": "user", "content": "Summarize this."}],
                "max_tokens": 128,
                "stream": True,
            }
        )

        canonical = self.adapter.normalize(body)
        restored = json.loads(self.adapter.denormalize(canonical))

        assert restored["system"][1]["cache_control"]["type"] == "ephemeral"
        assert restored["model"] == "claude-sonnet-4-6"
        assert restored["stream"] is True

    def test_inject_system_context_uses_ephemeral_cache_control(self):
        body = _to_bytes(
            {
                "model": "claude-sonnet-4-6",
                "system": "base system",
                "messages": [{"role": "user", "content": "hello"}],
            }
        )

        updated = json.loads(self.adapter.inject_system_context(body, "vault context"))
        assert isinstance(updated["system"], list)
        assert updated["system"][-1]["cache_control"]["type"] == "ephemeral"


class TestOpenAIChatAdapter:
    def setup_method(self):
        self.adapter = OpenAIChatAdapter()

    def test_round_trip_system_message_mapping(self):
        body = _to_bytes(
            {
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "Follow policy."},
                    {"role": "user", "content": "Write a haiku"},
                ],
                "temperature": 0.2,
                "stream": False,
            }
        )

        canonical = self.adapter.normalize(body)
        assert canonical.system == "Follow policy."
        assert canonical.messages[0]["role"] == "user"

        restored = json.loads(self.adapter.denormalize(canonical))
        assert restored["messages"][0]["role"] == "system"
        assert restored["messages"][0]["content"] == "Follow policy."

    def test_extract_response_tokens(self):
        resp = _to_bytes({"usage": {"prompt_tokens": 12, "completion_tokens": 34}})
        assert self.adapter.extract_response_tokens(resp) == 34


class TestOpenAIResponsesAdapter:
    def setup_method(self):
        self.adapter = OpenAIResponsesAdapter()

    def test_round_trip_input_string(self):
        body = _to_bytes(
            {
                "model": "gpt-5.3-codex",
                "instructions": "You are Codex.",
                "input": "Refactor this function",
                "stream": True,
            }
        )

        canonical = self.adapter.normalize(body)
        assert canonical.system == "You are Codex."
        assert canonical.messages[-1]["content"] == "Refactor this function"

        restored = json.loads(self.adapter.denormalize(canonical))
        assert isinstance(restored["input"], str)
        assert restored["input"] == "Refactor this function"

    def test_round_trip_input_content_array(self):
        body = _to_bytes(
            {
                "model": "gpt-5.2-codex",
                "instructions": "Assist with coding.",
                "input": [
                    {"type": "input_text", "text": "Review this diff"},
                    {"type": "input_text", "text": "Focus on bugs"},
                ],
            }
        )

        canonical = self.adapter.normalize(body)
        restored = json.loads(self.adapter.denormalize(canonical))

        assert isinstance(restored["input"], list)
        assert restored["input"][0]["type"] == "input_text"

    def test_round_trip_input_message_array(self):
        body = _to_bytes(
            {
                "model": "gpt-5.1-codex-mini",
                "instructions": "Be concise.",
                "input": [
                    {"role": "user", "content": "Need test plan"},
                    {"role": "assistant", "content": "Sure"},
                    {"role": "user", "content": "Make it robust"},
                ],
            }
        )

        canonical = self.adapter.normalize(body)
        assert canonical.messages[-1]["content"] == "Make it robust"
        assert self.adapter.extract_query_signal(body) == "Make it robust"

        restored = json.loads(self.adapter.denormalize(canonical))
        assert isinstance(restored["input"], list)
        assert restored["input"][0]["role"] == "user"


class TestGoogleAdapter:
    def setup_method(self):
        self.adapter = GoogleGenerativeAIAdapter()

    def test_detect_with_v1beta_and_header(self):
        assert self.adapter.detect("/v1beta/models/gemini-2-flash:generateContent", {}, None)
        assert self.adapter.detect("/anything", {"x-goog-api-key": "AIza"}, None)

    def test_round_trip_system_instruction_and_contents(self):
        body = _to_bytes(
            {
                "model": "gemini-2-flash",
                "systemInstruction": {"parts": [{"text": "You are precise."}]},
                "contents": [
                    {"role": "user", "parts": [{"text": "Hello"}]},
                    {"role": "model", "parts": [{"text": "Hi"}]},
                ],
                "generationConfig": {"temperature": 0.1},
            }
        )

        canonical = self.adapter.normalize(body)
        assert canonical.messages[0]["role"] == "user"
        assert canonical.system[0]["text"] == "You are precise."

        restored = json.loads(self.adapter.denormalize(canonical))
        assert restored["systemInstruction"]["parts"][0]["text"] == "You are precise."
        assert restored["contents"][0]["role"] == "user"

    def test_extract_response_tokens(self):
        resp = _to_bytes({"usageMetadata": {"promptTokenCount": 42, "candidatesTokenCount": 17}})
        assert self.adapter.extract_response_tokens(resp) == 17


class TestPassthroughAdapter:
    def setup_method(self):
        self.adapter = PassthroughAdapter()

    def test_round_trip_identity_payload(self):
        body = _to_bytes(
            {
                "model": "custom-model",
                "prompt": "hello",
                "custom": {"a": 1, "b": [1, 2, 3]},
            }
        )
        canonical = self.adapter.normalize(body)
        restored = json.loads(self.adapter.denormalize(canonical))
        assert restored["custom"]["a"] == 1

    def test_inject_system_context_is_noop(self):
        body = _to_bytes({"prompt": "hello"})
        assert self.adapter.inject_system_context(body, "context") == body
