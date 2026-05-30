"""Regression tests for cross-provider stream-translator fidelity.

Verifies that the Anthropic <-> OpenAI SSE event translator preserves
event-level units that agent parsers depend on:

- Anthropic content_block_delta (text_delta) -> OpenAI delta.content
- Anthropic tool_use start -> OpenAI tool_call start
- Anthropic input_json_delta -> OpenAI tool_calls argument delta (1:1)
- Anthropic message_delta stop_reason -> OpenAI finish_reason mapping
- Skip-events (content_block_stop, message_stop, ping) return None

These tests guard against silent regressions in the translator that
would break Claude Code <-> OpenAI compat routes for tool-heavy agents.
"""

from __future__ import annotations

import json

import pytest

from tokenpak.proxy.providers.stream_translator import _AnthropicToOpenAIStream


def _data_line_to_dict(line: str) -> dict:
    assert line.startswith("data: ")
    return json.loads(line[len("data: ") :])


def _delta(translator_output: str) -> dict:
    obj = _data_line_to_dict(translator_output)
    return obj["choices"][0]["delta"]


def test_text_delta_preserved():
    t = _AnthropicToOpenAIStream()
    t.translate({"type": "message_start", "message": {"model": "claude-sonnet"}})
    out = t.translate(
        {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "hi"}}
    )
    assert out is not None
    assert _delta(out) == {"content": "hi"}


def test_text_delta_order_preserved():
    t = _AnthropicToOpenAIStream()
    t.translate({"type": "message_start", "message": {"model": "claude-sonnet"}})
    outputs = []
    for text in ["one ", "two ", "three"]:
        out = t.translate(
            {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": text},
            }
        )
        outputs.append(_delta(out)["content"])
    assert outputs == ["one ", "two ", "three"]


def test_tool_call_start_preserved():
    t = _AnthropicToOpenAIStream()
    t.translate({"type": "message_start", "message": {"model": "claude-sonnet"}})
    out = t.translate(
        {
            "type": "content_block_start",
            "content_block": {
                "type": "tool_use",
                "id": "toolu_abc",
                "name": "search",
            },
        }
    )
    assert out is not None
    delta = _delta(out)
    assert "tool_calls" in delta
    tc = delta["tool_calls"][0]
    assert tc["id"] == "toolu_abc"
    assert tc["function"]["name"] == "search"


def test_tool_call_arg_delta_count_parity():
    """Every input_json_delta from Anthropic must yield exactly one
    OpenAI tool_calls argument delta. No merging."""
    t = _AnthropicToOpenAIStream()
    t.translate({"type": "message_start", "message": {"model": "claude-sonnet"}})
    t.translate(
        {
            "type": "content_block_start",
            "content_block": {"type": "tool_use", "id": "toolu_1", "name": "f"},
        }
    )
    json_chunks = ['{"q', '": "', "hello", '"}']
    out_count = 0
    for chunk in json_chunks:
        out = t.translate(
            {
                "type": "content_block_delta",
                "delta": {"type": "input_json_delta", "partial_json": chunk},
            }
        )
        assert out is not None
        out_count += 1
        delta = _delta(out)
        assert delta["tool_calls"][0]["function"]["arguments"] == chunk
    assert out_count == len(json_chunks)


@pytest.mark.parametrize(
    "stop_reason,expected_finish",
    [
        ("end_turn", "stop"),
        ("tool_use", "tool_calls"),
        ("max_tokens", "length"),
        ("stop_sequence", "stop"),
    ],
)
def test_message_delta_stop_reason_maps(stop_reason, expected_finish):
    t = _AnthropicToOpenAIStream()
    t.translate({"type": "message_start", "message": {"model": "claude-sonnet"}})
    out = t.translate({"type": "message_delta", "delta": {"stop_reason": stop_reason}})
    obj = _data_line_to_dict(out)
    assert obj["choices"][0]["finish_reason"] == expected_finish


@pytest.mark.parametrize("etype", ["content_block_stop", "message_stop", "ping"])
def test_skip_events_return_none(etype):
    t = _AnthropicToOpenAIStream()
    out = t.translate({"type": etype})
    assert out is None


def test_unknown_event_type_returns_none():
    t = _AnthropicToOpenAIStream()
    out = t.translate({"type": "future_event_not_yet_invented"})
    assert out is None
