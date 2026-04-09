"""
Tests for tokenpak/agent/proxy/providers/stream_translator.py

Coverage targets:
- _parse_sse_line() — SSE line parsing
- _sse_line() — SSE line formatting
- _sse_done() — SSE terminator
- _AnthropicToOpenAIStream — Anthropic → OpenAI translation
- _OpenAIToAnthropicStream — OpenAI → Anthropic translation
- _GoogleToAnthropicStream — Google → Anthropic translation
- StreamingTranslator — main facade
"""

import json
import pytest

# Import from the build location since source doesn't have it
import sys
sys.path.insert(0, "/home/cali/tokenpak/build/lib")

from tokenpak.agent.proxy.providers.stream_translator import (
    _parse_sse_line,
    _sse_line,
    _sse_done,
    _AnthropicToOpenAIStream,
    _OpenAIToAnthropicStream,
    _GoogleToAnthropicStream,
    StreamingTranslator,
)


# ---------------------------------------------------------------------------
# Tests for _parse_sse_line
# ---------------------------------------------------------------------------


class TestParseSSELine:
    """Tests for _parse_sse_line() helper."""

    def test_valid_json_data(self):
        """Valid JSON after 'data: ' is parsed correctly."""
        result = _parse_sse_line('data: {"type": "message_start"}')
        assert result == {"type": "message_start"}

    def test_valid_json_with_whitespace(self):
        """Whitespace around line is stripped."""
        result = _parse_sse_line('  data: {"key": "value"}  \n')
        assert result == {"key": "value"}

    def test_done_sentinel_returns_none(self):
        """[DONE] sentinel returns None."""
        result = _parse_sse_line("data: [DONE]")
        assert result is None

    def test_non_data_line_returns_none(self):
        """Lines not starting with 'data: ' return None."""
        assert _parse_sse_line("event: message") is None
        assert _parse_sse_line(": keepalive") is None
        assert _parse_sse_line("") is None

    def test_malformed_json_returns_none(self):
        """Malformed JSON returns None."""
        result = _parse_sse_line("data: {not valid json}")
        assert result is None

    def test_empty_data_returns_none(self):
        """Empty data after prefix returns None."""
        result = _parse_sse_line("data: ")
        assert result is None


# ---------------------------------------------------------------------------
# Tests for _sse_line and _sse_done
# ---------------------------------------------------------------------------


class TestSSEFormatters:
    """Tests for _sse_line() and _sse_done() helpers."""

    def test_sse_line_formats_dict(self):
        """_sse_line formats dict as 'data: {...}' string."""
        result = _sse_line({"type": "test"})
        assert result == 'data: {"type": "test"}'

    def test_sse_line_unicode(self):
        """_sse_line handles unicode characters."""
        result = _sse_line({"text": "café ☕"})
        assert "café ☕" in result
        assert result.startswith("data: ")

    def test_sse_done_constant(self):
        """_sse_done returns correct terminator."""
        assert _sse_done() == "data: [DONE]"


# ---------------------------------------------------------------------------
# Tests for _AnthropicToOpenAIStream
# ---------------------------------------------------------------------------


class TestAnthropicToOpenAIStream:
    """Tests for _AnthropicToOpenAIStream translation class."""

    def test_message_start_emits_role_chunk(self):
        """message_start event emits OpenAI role chunk."""
        stream = _AnthropicToOpenAIStream(stream_id="test-id")
        event = {
            "type": "message_start",
            "message": {"model": "claude-3-5-sonnet-20241022"}
        }
        result = stream.translate(event)
        assert result is not None
        assert "data: " in result
        parsed = json.loads(result[6:])
        assert parsed["id"] == "test-id"
        assert parsed["model"] == "claude-3-5-sonnet-20241022"
        assert parsed["choices"][0]["delta"]["role"] == "assistant"

    def test_content_block_start_text_returns_none(self):
        """content_block_start for text type returns None."""
        stream = _AnthropicToOpenAIStream()
        event = {
            "type": "content_block_start",
            "content_block": {"type": "text", "text": ""}
        }
        result = stream.translate(event)
        assert result is None

    def test_content_block_start_tool_use(self):
        """content_block_start for tool_use emits tool_calls delta."""
        stream = _AnthropicToOpenAIStream()
        event = {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "id": "toolu_123",
                "name": "get_weather"
            }
        }
        result = stream.translate(event)
        assert result is not None
        parsed = json.loads(result[6:])
        tool_calls = parsed["choices"][0]["delta"]["tool_calls"]
        assert len(tool_calls) == 1
        assert tool_calls[0]["id"] == "toolu_123"
        assert tool_calls[0]["function"]["name"] == "get_weather"

    def test_content_block_delta_text(self):
        """content_block_delta with text_delta emits content."""
        stream = _AnthropicToOpenAIStream()
        event = {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Hello"}
        }
        result = stream.translate(event)
        assert result is not None
        parsed = json.loads(result[6:])
        assert parsed["choices"][0]["delta"]["content"] == "Hello"

    def test_content_block_delta_input_json(self):
        """content_block_delta with input_json_delta emits tool arguments."""
        stream = _AnthropicToOpenAIStream()
        # First start a tool block
        stream.translate({
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "id": "t1", "name": "fn"}
        })
        event = {
            "type": "content_block_delta",
            "delta": {"type": "input_json_delta", "partial_json": '{"loc":'}
        }
        result = stream.translate(event)
        assert result is not None
        parsed = json.loads(result[6:])
        assert parsed["choices"][0]["delta"]["tool_calls"][0]["function"]["arguments"] == '{"loc":'

    def test_message_delta_stop_reason(self):
        """message_delta with stop_reason emits finish_reason."""
        stream = _AnthropicToOpenAIStream()
        event = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"}
        }
        result = stream.translate(event)
        assert result is not None
        parsed = json.loads(result[6:])
        assert parsed["choices"][0]["finish_reason"] == "stop"

    def test_message_delta_tool_use_stop(self):
        """tool_use stop_reason maps to 'tool_calls'."""
        stream = _AnthropicToOpenAIStream()
        event = {
            "type": "message_delta",
            "delta": {"stop_reason": "tool_use"}
        }
        result = stream.translate(event)
        parsed = json.loads(result[6:])
        assert parsed["choices"][0]["finish_reason"] == "tool_calls"

    def test_ignored_events(self):
        """content_block_stop, message_stop, ping return None."""
        stream = _AnthropicToOpenAIStream()
        assert stream.translate({"type": "content_block_stop"}) is None
        assert stream.translate({"type": "message_stop"}) is None
        assert stream.translate({"type": "ping"}) is None


# ---------------------------------------------------------------------------
# Tests for _OpenAIToAnthropicStream
# ---------------------------------------------------------------------------


class TestOpenAIToAnthropicStream:
    """Tests for _OpenAIToAnthropicStream translation class."""

    def test_first_chunk_emits_message_start(self):
        """First chunk emits message_start event."""
        stream = _OpenAIToAnthropicStream(message_id="msg_test")
        chunk = {
            "model": "gpt-4",
            "choices": [{"delta": {"role": "assistant"}}]
        }
        result = stream.translate(chunk)
        assert len(result) == 1
        parsed = json.loads(result[0][6:])
        assert parsed["type"] == "message_start"
        assert parsed["message"]["id"] == "msg_test"

    def test_text_delta_opens_block_and_emits(self):
        """Text delta opens content_block and emits text_delta."""
        stream = _OpenAIToAnthropicStream()
        # First trigger started state
        stream.translate({"model": "gpt-4", "choices": [{"delta": {"role": "assistant"}}]})
        # Now text delta
        chunk = {
            "choices": [{"delta": {"content": "Hi"}}]
        }
        result = stream.translate(chunk)
        # Should have content_block_start + content_block_delta
        assert len(result) == 2
        block_start = json.loads(result[0][6:])
        block_delta = json.loads(result[1][6:])
        assert block_start["type"] == "content_block_start"
        assert block_delta["type"] == "content_block_delta"
        assert block_delta["delta"]["text"] == "Hi"

    def test_tool_call_delta(self):
        """Tool call delta emits tool_use blocks."""
        stream = _OpenAIToAnthropicStream()
        stream.translate({"model": "gpt-4", "choices": [{"delta": {}}]})
        chunk = {
            "choices": [{
                "delta": {
                    "tool_calls": [{
                        "index": 0,
                        "id": "call_abc",
                        "function": {"name": "search", "arguments": ""}
                    }]
                }
            }]
        }
        result = stream.translate(chunk)
        assert len(result) == 1
        parsed = json.loads(result[0][6:])
        assert parsed["type"] == "content_block_start"
        assert parsed["content_block"]["type"] == "tool_use"
        assert parsed["content_block"]["name"] == "search"

    def test_finish_reason_emits_stop_events(self):
        """finish_reason emits content_block_stop and message_stop."""
        stream = _OpenAIToAnthropicStream()
        stream.translate({"model": "gpt-4", "choices": [{"delta": {}}]})
        stream.translate({"choices": [{"delta": {"content": "X"}}]})
        chunk = {"choices": [{"delta": {}, "finish_reason": "stop"}]}
        result = stream.translate(chunk)
        # Should have block_stop + message_delta + message_stop
        types = [json.loads(r[6:])["type"] for r in result]
        assert "content_block_stop" in types
        assert "message_delta" in types
        assert "message_stop" in types


# ---------------------------------------------------------------------------
# Tests for _GoogleToAnthropicStream
# ---------------------------------------------------------------------------


class TestGoogleToAnthropicStream:
    """Tests for _GoogleToAnthropicStream translation class."""

    def test_first_chunk_emits_message_start(self):
        """First chunk emits message_start."""
        stream = _GoogleToAnthropicStream(message_id="msg_g")
        chunk = {
            "modelVersion": "gemini-1.5-pro",
            "candidates": [{"content": {"parts": []}}]
        }
        result = stream.translate(chunk)
        assert len(result) >= 1
        parsed = json.loads(result[0][6:])
        assert parsed["type"] == "message_start"
        assert parsed["message"]["model"] == "gemini-1.5-pro"

    def test_text_diff_emission(self):
        """Diff-based text emission works correctly."""
        stream = _GoogleToAnthropicStream()
        # First chunk
        chunk1 = {
            "candidates": [{"content": {"parts": [{"text": "Hello"}]}}]
        }
        result1 = stream.translate(chunk1)
        # Second chunk with more text
        chunk2 = {
            "candidates": [{"content": {"parts": [{"text": "Hello, world"}]}}]
        }
        result2 = stream.translate(chunk2)
        # Should emit ", world" as the delta
        delta_lines = [r for r in result2 if "text_delta" in r]
        assert len(delta_lines) == 1
        parsed = json.loads(delta_lines[0][6:])
        assert parsed["delta"]["text"] == ", world"

    def test_function_call_in_parts(self):
        """functionCall in parts emits tool_use block."""
        stream = _GoogleToAnthropicStream()
        chunk = {
            "candidates": [{
                "content": {
                    "parts": [{
                        "functionCall": {
                            "name": "get_time",
                            "args": {"tz": "UTC"}
                        }
                    }]
                }
            }]
        }
        result = stream.translate(chunk)
        # Should have tool_use content_block_start and stop
        starts = [r for r in result if "content_block_start" in r and "tool_use" in r]
        assert len(starts) == 1

    def test_finish_reason_emits_stop(self):
        """STOP finish reason emits message_stop."""
        stream = _GoogleToAnthropicStream()
        stream.translate({"candidates": [{"content": {"parts": []}}]})
        stream.translate({"candidates": [{"content": {"parts": [{"text": "X"}]}}]})
        chunk = {
            "candidates": [{
                "content": {"parts": [{"text": "X"}]},
                "finishReason": "STOP"
            }],
            "usageMetadata": {"candidatesTokenCount": 10}
        }
        result = stream.translate(chunk)
        types = [json.loads(r[6:])["type"] for r in result]
        assert "message_delta" in types
        assert "message_stop" in types


# ---------------------------------------------------------------------------
# Tests for StreamingTranslator facade
# ---------------------------------------------------------------------------


class TestStreamingTranslator:
    """Tests for StreamingTranslator main facade."""

    def test_passthrough_same_provider(self):
        """Same source/target passes through unchanged."""
        t = StreamingTranslator("anthropic", "anthropic")
        result = t.translate_chunk('data: {"type": "ping"}')
        assert result == ['data: {"type": "ping"}']

    def test_anthropic_to_openai(self):
        """anthropic→openai translation works."""
        t = StreamingTranslator("anthropic", "openai")
        result = t.translate_chunk('data: {"type": "message_start", "message": {"model": "claude"}}')
        assert len(result) == 1
        assert "chat.completion.chunk" in result[0]

    def test_openai_to_anthropic(self):
        """openai→anthropic translation works."""
        t = StreamingTranslator("openai", "anthropic")
        result = t.translate_chunk('data: {"model": "gpt-4", "choices": [{"delta": {}}]}')
        assert len(result) >= 1
        assert "message_start" in result[0]

    def test_google_to_anthropic(self):
        """google→anthropic translation works."""
        t = StreamingTranslator("google", "anthropic")
        result = t.translate_chunk('data: {"candidates": [{"content": {"parts": []}}]}')
        assert len(result) >= 1
        assert "message_start" in result[0]

    def test_unsupported_pair_raises(self):
        """Unsupported translation pair raises ValueError."""
        with pytest.raises(ValueError, match="No direct streaming translator"):
            StreamingTranslator("google", "openai")

    def test_done_sentinel_translation(self):
        """[DONE] sentinel is translated correctly."""
        t = StreamingTranslator("anthropic", "openai")
        result = t.translate_chunk("data: [DONE]")
        assert result == ["data: [DONE]"]

    def test_empty_line_returns_empty(self):
        """Empty line returns empty list."""
        t = StreamingTranslator("anthropic", "openai")
        result = t.translate_chunk("")
        assert result == []

    def test_translate_stream_iterator(self):
        """translate_stream yields translated lines."""
        t = StreamingTranslator("anthropic", "openai")
        lines = [
            'data: {"type": "message_start", "message": {"model": "claude"}}',
            'data: {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Hi"}}',
            "data: [DONE]"
        ]
        results = list(t.translate_stream(iter(lines)))
        assert len(results) >= 2  # message_start + text + done
        assert "data: [DONE]" in results


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case tests for robustness."""

    def test_malformed_sse_line_returns_empty(self):
        """Malformed SSE line returns empty list."""
        t = StreamingTranslator("anthropic", "openai")
        result = t.translate_chunk("not a valid sse line")
        assert result == []

    def test_unknown_anthropic_event_type(self):
        """Unknown Anthropic event type returns None."""
        stream = _AnthropicToOpenAIStream()
        result = stream.translate({"type": "unknown_event"})
        assert result is None

    def test_empty_choices_returns_empty(self):
        """OpenAI chunk with empty choices returns empty."""
        stream = _OpenAIToAnthropicStream()
        stream._started = True  # bypass message_start
        result = stream.translate({"choices": []})
        assert result == []

    def test_google_no_candidates_returns_empty(self):
        """Google chunk with no candidates returns message_start only."""
        stream = _GoogleToAnthropicStream()
        result = stream.translate({"modelVersion": "gemini"})
        # Should just emit message_start
        assert len(result) == 1
        assert "message_start" in result[0]
