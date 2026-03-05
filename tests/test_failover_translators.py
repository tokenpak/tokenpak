"""
Tests for Failover Translators (F.2 — full coverage)

Covers:
  - Response translation: all 6 directions (anthropic↔openai↔google)
  - Streaming chunk translation: anthropic→openai, openai→anthropic, google→anthropic
  - Tool/function call schema preservation in requests and responses
  - Graceful handling of unsupported features (thinking blocks, unknown parts)
  - Round-trip fidelity for requests (already covered by test_failover_provider_detection)
"""

from __future__ import annotations

import json
from typing import Any, Dict, List

import pytest

from tokenpak.agent.proxy.providers.translator import translate_request, translate_response
from tokenpak.agent.proxy.providers.stream_translator import (
    StreamingTranslator,
    _sse_done,
    _parse_sse_line,
)


# ---------------------------------------------------------------------------
# Fixtures — sample payloads
# ---------------------------------------------------------------------------

def _ant_response(
    text: str = "Hello!",
    stop_reason: str = "end_turn",
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> Dict[str, Any]:
    return {
        "id": "msg_abc123",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


def _oai_response(
    text: str = "Hello!",
    finish_reason: str = "stop",
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
) -> Dict[str, Any]:
    return {
        "id": "chatcmpl-xyz",
        "object": "chat.completion",
        "model": "gpt-4o",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": text},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _google_response(
    text: str = "Hello!",
    finish_reason: str = "STOP",
    prompt_count: int = 10,
    candidates_count: int = 5,
) -> Dict[str, Any]:
    return {
        "candidates": [
            {
                "content": {"role": "model", "parts": [{"text": text}]},
                "finishReason": finish_reason,
                "index": 0,
            }
        ],
        "usageMetadata": {
            "promptTokenCount": prompt_count,
            "candidatesTokenCount": candidates_count,
            "totalTokenCount": prompt_count + candidates_count,
        },
        "modelVersion": "gemini-1.5-pro",
    }


# ---------------------------------------------------------------------------
# Response translation: anthropic → openai
# ---------------------------------------------------------------------------

class TestAnthropicToOpenAIResponse:
    def test_text_content(self):
        out = translate_response(_ant_response("Hi there"), "anthropic", "openai")
        assert out["object"] == "chat.completion"
        assert out["choices"][0]["message"]["content"] == "Hi there"
        assert out["choices"][0]["finish_reason"] == "stop"

    def test_usage_mapping(self):
        out = translate_response(_ant_response(input_tokens=20, output_tokens=8), "anthropic", "openai")
        assert out["usage"]["prompt_tokens"] == 20
        assert out["usage"]["completion_tokens"] == 8
        assert out["usage"]["total_tokens"] == 28

    def test_max_tokens_stop_reason(self):
        out = translate_response(_ant_response(stop_reason="max_tokens"), "anthropic", "openai")
        assert out["choices"][0]["finish_reason"] == "length"

    def test_tool_use_stop_reason(self):
        ant = {
            "id": "msg_t1",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_01",
                    "name": "get_weather",
                    "input": {"city": "London"},
                }
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        out = translate_response(ant, "anthropic", "openai")
        assert out["choices"][0]["finish_reason"] == "tool_calls"
        tc = out["choices"][0]["message"]["tool_calls"][0]
        assert tc["function"]["name"] == "get_weather"
        assert json.loads(tc["function"]["arguments"]) == {"city": "London"}

    def test_thinking_blocks_graceful(self):
        """Thinking blocks should be skipped gracefully."""
        ant = {
            "id": "msg_t2",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": [
                {"type": "thinking", "thinking": "Let me reason..."},
                {"type": "text", "text": "Final answer"},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        # Thinking blocks are unknown type — they get skipped in response translation
        out = translate_response(ant, "anthropic", "openai")
        # Should still produce valid output (thinking block is ignored)
        assert out["object"] == "chat.completion"


# ---------------------------------------------------------------------------
# Response translation: openai → anthropic
# ---------------------------------------------------------------------------

class TestOpenAIToAnthropicResponse:
    def test_text_content(self):
        out = translate_response(_oai_response("Hello back"), "openai", "anthropic")
        assert out["type"] == "message"
        assert out["content"][0]["type"] == "text"
        assert out["content"][0]["text"] == "Hello back"

    def test_usage_mapping(self):
        out = translate_response(_oai_response(prompt_tokens=15, completion_tokens=7), "openai", "anthropic")
        assert out["usage"]["input_tokens"] == 15
        assert out["usage"]["output_tokens"] == 7

    def test_length_finish_reason(self):
        out = translate_response(_oai_response(finish_reason="length"), "openai", "anthropic")
        assert out["stop_reason"] == "max_tokens"

    def test_tool_calls(self):
        oai = {
            "id": "chatcmpl-t1",
            "object": "chat.completion",
            "model": "gpt-4o",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call_abc",
                                "type": "function",
                                "function": {
                                    "name": "search",
                                    "arguments": '{"query": "test"}',
                                },
                            }
                        ],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        out = translate_response(oai, "openai", "anthropic")
        assert out["stop_reason"] == "tool_use"
        tool_block = next(b for b in out["content"] if b["type"] == "tool_use")
        assert tool_block["name"] == "search"
        assert tool_block["input"] == {"query": "test"}

    def test_roundtrip_text(self):
        """Translating anthropic→openai→anthropic preserves text."""
        original = _ant_response("Round trip text")
        mid = translate_response(original, "anthropic", "openai")
        restored = translate_response(mid, "openai", "anthropic")
        assert restored["content"][0]["text"] == "Round trip text"

    def test_same_provider_passthrough(self):
        resp = _oai_response("Same provider")
        out = translate_response(resp, "openai", "openai")
        assert out["choices"][0]["message"]["content"] == "Same provider"


# ---------------------------------------------------------------------------
# Response translation: google → anthropic
# ---------------------------------------------------------------------------

class TestGoogleToAnthropicResponse:
    def test_text_content(self):
        out = translate_response(_google_response("Gemini says hi"), "google", "anthropic")
        assert out["type"] == "message"
        assert out["content"][0]["type"] == "text"
        assert out["content"][0]["text"] == "Gemini says hi"

    def test_usage_mapping(self):
        out = translate_response(_google_response(prompt_count=12, candidates_count=6), "google", "anthropic")
        assert out["usage"]["input_tokens"] == 12
        assert out["usage"]["output_tokens"] == 6

    def test_stop_reason_max_tokens(self):
        out = translate_response(_google_response(finish_reason="MAX_TOKENS"), "google", "anthropic")
        assert out["stop_reason"] == "max_tokens"

    def test_function_call_content(self):
        g_resp = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {"functionCall": {"name": "get_time", "args": {"tz": "UTC"}}}
                        ],
                    },
                    "finishReason": "TOOL_CODE",
                    "index": 0,
                }
            ],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3},
        }
        out = translate_response(g_resp, "google", "anthropic")
        assert out["stop_reason"] == "tool_use"
        tool_block = out["content"][0]
        assert tool_block["type"] == "tool_use"
        assert tool_block["name"] == "get_time"
        assert tool_block["input"] == {"tz": "UTC"}

    def test_thought_parts_skipped(self):
        """Google 'thought' parts should be gracefully skipped."""
        g_resp = {
            "candidates": [
                {
                    "content": {
                        "role": "model",
                        "parts": [
                            {"thought": True, "text": "Internal reasoning..."},
                            {"text": "Final answer"},
                        ],
                    },
                    "finishReason": "STOP",
                    "index": 0,
                }
            ],
            "usageMetadata": {},
        }
        out = translate_response(g_resp, "google", "anthropic")
        # thought part skipped; only text part appears
        texts = [b["text"] for b in out["content"] if b["type"] == "text"]
        assert "Final answer" in texts
        assert not any("Internal reasoning" in t for t in texts)

    def test_empty_candidates(self):
        """Empty candidates array handled gracefully."""
        g_resp = {"candidates": [], "usageMetadata": {}}
        out = translate_response(g_resp, "google", "anthropic")
        assert out["type"] == "message"
        assert out["content"] == []


# ---------------------------------------------------------------------------
# Response translation: anthropic → google
# ---------------------------------------------------------------------------

class TestAnthropicToGoogleResponse:
    def test_text_content(self):
        out = translate_response(_ant_response("Hi Google"), "anthropic", "google")
        assert "candidates" in out
        parts = out["candidates"][0]["content"]["parts"]
        assert parts[0]["text"] == "Hi Google"

    def test_finish_reason_mapping(self):
        out = translate_response(_ant_response(stop_reason="max_tokens"), "anthropic", "google")
        assert out["candidates"][0]["finishReason"] == "MAX_TOKENS"

    def test_tool_use_in_response(self):
        ant = {
            "id": "msg_t3",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": [
                {"type": "tool_use", "id": "toolu_02", "name": "lookup", "input": {"id": 42}}
            ],
            "stop_reason": "tool_use",
            "usage": {"input_tokens": 5, "output_tokens": 3},
        }
        out = translate_response(ant, "anthropic", "google")
        parts = out["candidates"][0]["content"]["parts"]
        assert parts[0]["functionCall"]["name"] == "lookup"
        assert parts[0]["functionCall"]["args"] == {"id": 42}
        assert out["candidates"][0]["finishReason"] == "TOOL_CODE"

    def test_thinking_block_skipped(self):
        """Thinking blocks should be skipped (Google doesn't support them)."""
        ant = {
            "id": "msg_think",
            "type": "message",
            "role": "assistant",
            "model": "claude-sonnet-4-5",
            "content": [
                {"type": "thinking", "thinking": "Private thoughts..."},
                {"type": "text", "text": "Public answer"},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 8},
        }
        out = translate_response(ant, "anthropic", "google")
        parts = out["candidates"][0]["content"]["parts"]
        texts = [p["text"] for p in parts if "text" in p]
        assert "Public answer" in texts
        # Thinking content must NOT appear
        assert not any("Private thoughts" in t for t in texts)

    def test_usage_mapping(self):
        out = translate_response(_ant_response(input_tokens=20, output_tokens=8), "anthropic", "google")
        meta = out["usageMetadata"]
        assert meta["promptTokenCount"] == 20
        assert meta["candidatesTokenCount"] == 8
        assert meta["totalTokenCount"] == 28

    def test_roundtrip_google(self):
        """google→anthropic→google preserves text."""
        original = _google_response("Roundtrip via google")
        mid = translate_response(original, "google", "anthropic")
        restored = translate_response(mid, "anthropic", "google")
        parts = restored["candidates"][0]["content"]["parts"]
        assert parts[0]["text"] == "Roundtrip via google"


# ---------------------------------------------------------------------------
# Response translation: google → openai (via chain)
# ---------------------------------------------------------------------------

class TestGoogleToOpenAIResponse:
    def test_text_content(self):
        out = translate_response(_google_response("From Google to OAI"), "google", "openai")
        assert out["object"] == "chat.completion"
        assert out["choices"][0]["message"]["content"] == "From Google to OAI"

    def test_finish_reason(self):
        out = translate_response(_google_response(finish_reason="MAX_TOKENS"), "google", "openai")
        assert out["choices"][0]["finish_reason"] == "length"


# ---------------------------------------------------------------------------
# Response translation: openai → google (via chain)
# ---------------------------------------------------------------------------

class TestOpenAIToGoogleResponse:
    def test_text_content(self):
        out = translate_response(_oai_response("From OAI to Google"), "openai", "google")
        parts = out["candidates"][0]["content"]["parts"]
        assert parts[0]["text"] == "From OAI to Google"

    def test_finish_reason(self):
        out = translate_response(_oai_response(finish_reason="length"), "openai", "google")
        assert out["candidates"][0]["finishReason"] == "MAX_TOKENS"


# ---------------------------------------------------------------------------
# Streaming: Anthropic → OpenAI
# ---------------------------------------------------------------------------

def _ant_stream_events(text: str = "Hello world", stop_reason: str = "end_turn") -> List[str]:
    return [
        f'data: {{"type":"message_start","message":{{"id":"msg_1","model":"claude-sonnet-4-5","role":"assistant","content":[],"stop_reason":null,"usage":{{"input_tokens":10,"output_tokens":0}}}}}}',
        f'data: {{"type":"content_block_start","index":0,"content_block":{{"type":"text","text":""}}}}',
        f'data: {{"type":"content_block_delta","index":0,"delta":{{"type":"text_delta","text":"{text}"}}}}',
        f'data: {{"type":"content_block_stop","index":0}}',
        f'data: {{"type":"message_delta","delta":{{"stop_reason":"{stop_reason}","stop_sequence":null}},"usage":{{"output_tokens":5}}}}',
        f'data: {{"type":"message_stop"}}',
        "data: [DONE]",
    ]


class TestAnthropicToOpenAIStreaming:
    def _translate(self, events: List[str]) -> List[Dict[str, Any]]:
        t = StreamingTranslator("anthropic", "openai")
        out = []
        for ev in events:
            for line in t.translate_chunk(ev):
                if line != "data: [DONE]":
                    parsed = _parse_sse_line(line)
                    if parsed:
                        out.append(parsed)
        return out

    def test_text_streaming(self):
        chunks = self._translate(_ant_stream_events("test text"))
        # Find text delta chunk
        text_chunks = [
            c for c in chunks
            if c.get("choices", [{}])[0].get("delta", {}).get("content")
        ]
        assert any("test text" in c["choices"][0]["delta"]["content"] for c in text_chunks)

    def test_finish_reason_emitted(self):
        chunks = self._translate(_ant_stream_events())
        finish_chunks = [
            c for c in chunks
            if c.get("choices", [{}])[0].get("finish_reason") is not None
        ]
        assert len(finish_chunks) == 1
        assert finish_chunks[0]["choices"][0]["finish_reason"] == "stop"

    def test_max_tokens_finish_reason(self):
        chunks = self._translate(_ant_stream_events(stop_reason="max_tokens"))
        finish_chunks = [
            c for c in chunks
            if c.get("choices", [{}])[0].get("finish_reason") is not None
        ]
        assert finish_chunks[0]["choices"][0]["finish_reason"] == "length"

    def test_tool_use_streaming(self):
        events = [
            'data: {"type":"message_start","message":{"id":"msg_t","model":"claude-sonnet-4-5","role":"assistant","content":[],"stop_reason":null,"usage":{"input_tokens":10,"output_tokens":0}}}',
            'data: {"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_1","name":"search","input":{}}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"q\":"}}',
            'data: {"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"\"test\"}"}}',
            'data: {"type":"content_block_stop","index":0}',
            'data: {"type":"message_delta","delta":{"stop_reason":"tool_use","stop_sequence":null},"usage":{"output_tokens":5}}',
            'data: {"type":"message_stop"}',
            "data: [DONE]",
        ]
        chunks = self._translate(events)
        # Should have tool_calls in at least one delta
        tool_chunks = [
            c for c in chunks
            if c.get("choices", [{}])[0].get("delta", {}).get("tool_calls")
        ]
        assert len(tool_chunks) > 0
        first_tc = tool_chunks[0]["choices"][0]["delta"]["tool_calls"][0]
        assert first_tc["function"]["name"] == "search"

    def test_done_passthrough(self):
        t = StreamingTranslator("anthropic", "openai")
        result = t.translate_chunk("data: [DONE]")
        assert result == [_sse_done()]

    def test_ping_events_ignored(self):
        t = StreamingTranslator("anthropic", "openai")
        result = t.translate_chunk('data: {"type":"ping"}')
        assert result == []


# ---------------------------------------------------------------------------
# Streaming: OpenAI → Anthropic
# ---------------------------------------------------------------------------

def _oai_stream_events(text: str = "Hello back") -> List[str]:
    return [
        f'data: {{"id":"chatcmpl-1","object":"chat.completion.chunk","model":"gpt-4o","choices":[{{"index":0,"delta":{{"role":"assistant","content":""}},"finish_reason":null}}]}}',
        f'data: {{"id":"chatcmpl-1","object":"chat.completion.chunk","model":"gpt-4o","choices":[{{"index":0,"delta":{{"content":"{text}"}},"finish_reason":null}}]}}',
        f'data: {{"id":"chatcmpl-1","object":"chat.completion.chunk","model":"gpt-4o","choices":[{{"index":0,"delta":{{}},"finish_reason":"stop"}}]}}',
        "data: [DONE]",
    ]


class TestOpenAIToAnthropicStreaming:
    def _translate(self, events: List[str]) -> List[Dict[str, Any]]:
        t = StreamingTranslator("openai", "anthropic")
        out = []
        for ev in events:
            for line in t.translate_chunk(ev):
                if line != "data: [DONE]":
                    parsed = _parse_sse_line(line)
                    if parsed:
                        out.append(parsed)
        return out

    def test_message_start_emitted(self):
        chunks = self._translate(_oai_stream_events())
        starts = [c for c in chunks if c.get("type") == "message_start"]
        assert len(starts) == 1
        assert starts[0]["message"]["role"] == "assistant"

    def test_text_delta_emitted(self):
        chunks = self._translate(_oai_stream_events("test content"))
        deltas = [
            c for c in chunks
            if c.get("type") == "content_block_delta"
            and c.get("delta", {}).get("type") == "text_delta"
        ]
        assert any("test content" in d["delta"]["text"] for d in deltas)

    def test_message_stop_emitted(self):
        chunks = self._translate(_oai_stream_events())
        stops = [c for c in chunks if c.get("type") == "message_stop"]
        assert len(stops) == 1

    def test_message_delta_stop_reason(self):
        chunks = self._translate(_oai_stream_events())
        deltas = [c for c in chunks if c.get("type") == "message_delta"]
        assert len(deltas) == 1
        assert deltas[0]["delta"]["stop_reason"] == "end_turn"

    def test_tool_calls_translated(self):
        events = [
            'data: {"id":"c1","object":"chat.completion.chunk","model":"gpt-4o","choices":[{"index":0,"delta":{"role":"assistant"},"finish_reason":null}]}',
            'data: {"id":"c1","object":"chat.completion.chunk","model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"lookup","arguments":""}}]},"finish_reason":null}]}',
            'data: {"id":"c1","object":"chat.completion.chunk","model":"gpt-4o","choices":[{"index":0,"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\\"id\\":1}"}}]},"finish_reason":null}]}',
            'data: {"id":"c1","object":"chat.completion.chunk","model":"gpt-4o","choices":[{"index":0,"delta":{},"finish_reason":"tool_calls"}]}',
            "data: [DONE]",
        ]
        chunks = self._translate(events)
        tool_starts = [
            c for c in chunks
            if c.get("type") == "content_block_start"
            and c.get("content_block", {}).get("type") == "tool_use"
        ]
        assert len(tool_starts) == 1
        assert tool_starts[0]["content_block"]["name"] == "lookup"

        message_deltas = [c for c in chunks if c.get("type") == "message_delta"]
        assert message_deltas[0]["delta"]["stop_reason"] == "tool_use"


# ---------------------------------------------------------------------------
# Streaming: Google → Anthropic
# ---------------------------------------------------------------------------

class TestGoogleToAnthropicStreaming:
    def _translate(self, events: List[str]) -> List[Dict[str, Any]]:
        t = StreamingTranslator("google", "anthropic")
        out = []
        for ev in events:
            for line in t.translate_chunk(ev):
                if line != "data: [DONE]":
                    parsed = _parse_sse_line(line)
                    if parsed:
                        out.append(parsed)
        return out

    def test_text_streaming(self):
        events = [
            'data: {"candidates":[{"content":{"role":"model","parts":[{"text":"Hel"}]},"finishReason":"FINISH_REASON_UNSPECIFIED","index":0}],"usageMetadata":{}}',
            'data: {"candidates":[{"content":{"role":"model","parts":[{"text":"Hello there"}]},"finishReason":"STOP","index":0}],"usageMetadata":{"promptTokenCount":5,"candidatesTokenCount":3}}',
            "data: [DONE]",
        ]
        chunks = self._translate(events)
        deltas = [
            c for c in chunks
            if c.get("type") == "content_block_delta"
        ]
        assert len(deltas) > 0

    def test_message_start_emitted(self):
        events = [
            'data: {"candidates":[{"content":{"role":"model","parts":[{"text":"Hi"}]},"finishReason":"STOP","index":0}],"usageMetadata":{}}',
        ]
        chunks = self._translate(events)
        starts = [c for c in chunks if c.get("type") == "message_start"]
        assert len(starts) == 1


# ---------------------------------------------------------------------------
# Streaming: passthrough (same provider)
# ---------------------------------------------------------------------------

class TestStreamingPassthrough:
    def test_same_provider_passthrough(self):
        t = StreamingTranslator("anthropic", "anthropic")
        line = 'data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"hi"}}'
        result = t.translate_chunk(line)
        assert result == [line]

    def test_unsupported_pair_raises(self):
        with pytest.raises(ValueError, match="No direct streaming translator"):
            StreamingTranslator("google", "openai")


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_translate_response_unknown_pair(self):
        with pytest.raises(ValueError, match="No response translator"):
            translate_response({}, "anthropic", "ollama")

    def test_translate_response_same_provider(self):
        resp = _ant_response("Same")
        out = translate_response(resp, "anthropic", "anthropic")
        assert out["content"][0]["text"] == "Same"

    def test_streaming_ignores_malformed_json(self):
        t = StreamingTranslator("openai", "anthropic")
        result = t.translate_chunk("data: {not valid json}")
        assert result == []

    def test_streaming_ignores_non_data_lines(self):
        t = StreamingTranslator("anthropic", "openai")
        for line in ["event: message", "id: 12345", ": heartbeat", ""]:
            result = t.translate_chunk(line)
            assert result == []

    def test_google_empty_response_graceful(self):
        out = translate_response({"candidates": [], "usageMetadata": {}}, "google", "anthropic")
        assert out["type"] == "message"
        assert out["content"] == []

    def test_translate_stream_iterator(self):
        t = StreamingTranslator("anthropic", "openai")
        events = iter([
            'data: {"type":"message_start","message":{"id":"m","model":"claude-sonnet-4-5","role":"assistant","content":[],"stop_reason":null,"usage":{"input_tokens":1,"output_tokens":0}}}',
            "data: [DONE]",
        ])
        results = list(t.translate_stream(events))
        assert len(results) >= 1
