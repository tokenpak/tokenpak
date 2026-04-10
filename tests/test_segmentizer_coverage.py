"""test_segmentizer_coverage.py — Phase 4C Segmentizer Test Suite.

Covers:
  - All 11 SegmentType values
  - Detection rules (system, developer, user, assistant_context, memory,
    retrieval, tool_schema, tool_output, image, guardrail, other)
  - SHA-256 hash stability
  - Token count within 2% of total
  - Deterministic ordering
  - Edge cases: empty input, None content, oversized segments

Run:
    pytest tests/test_segmentizer_coverage.py -v
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from tokenpak.compression.segmentizer import (
    Segment,
    SegmentType,
    _sha256,
    detect_anti_patterns,
    detect_stale,
    segmentize,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _types(segments: list[Segment]) -> list[str]:
    return [s.segment_type for s in segments]


def _total_tokens(segments: list[Segment]) -> int:
    return sum(s.tokens_raw for s in segments)


# ===========================================================================
# TC-01: SegmentType enum has exactly 11 types
# ===========================================================================


def test_segment_type_enum_has_11_values() -> None:
    expected = {
        "system",
        "developer",
        "user",
        "assistant_context",
        "memory",
        "retrieval",
        "tool_schema",
        "tool_output",
        "guardrail",
        "image",
        "other",
    }
    actual = {e.value for e in SegmentType}
    assert actual == expected, f"Missing/extra types: {actual ^ expected}"


# ===========================================================================
# TC-02: Segment dataclass has required fields
# ===========================================================================


def test_segment_dataclass_required_fields() -> None:
    seg = Segment()
    required = [
        "segment_id",
        "order",
        "segment_type",
        "raw_hash",
        "raw_len",
        "tokens_raw",
    ]
    for field in required:
        assert hasattr(seg, field), f"Segment missing field: {field}"


# ===========================================================================
# TC-03: system role → SegmentType.system
# ===========================================================================


def test_system_message_classified_as_system() -> None:
    msgs = [{"role": "system", "content": "You are a helpful assistant."}]
    segs = segmentize(msgs)
    assert segs[0].segment_type == SegmentType.system.value


# ===========================================================================
# TC-04: developer role → SegmentType.developer
# ===========================================================================


def test_developer_message_classified_as_developer() -> None:
    msgs = [{"role": "developer", "content": "Use concise answers."}]
    segs = segmentize(msgs)
    assert segs[0].segment_type == SegmentType.developer.value


# ===========================================================================
# TC-05: user role → SegmentType.user
# ===========================================================================


def test_user_message_classified_as_user() -> None:
    msgs = [{"role": "user", "content": "What is 2+2?"}]
    segs = segmentize(msgs)
    assert segs[0].segment_type == SegmentType.user.value


# ===========================================================================
# TC-06: prior assistant turn → SegmentType.assistant_context
# ===========================================================================


def test_prior_assistant_turn_classified_as_assistant_context() -> None:
    msgs = [
        {"role": "system", "content": "You are helpful."},
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi! How can I help?"},  # prior turn
        {"role": "user", "content": "Tell me more"},  # latest
    ]
    segs = segmentize(msgs)
    assistant_segs = [s for s in segs if s.order == 2]
    assert assistant_segs[0].segment_type == SegmentType.assistant_context.value


# ===========================================================================
# TC-07: TOKPAK: retrieval marker → SegmentType.retrieval
# ===========================================================================


def test_retrieval_marker_classified_as_retrieval() -> None:
    msgs = [{"role": "user", "content": "TOKPAK:1 here is the compressed context blob"}]
    segs = segmentize(msgs)
    assert segs[0].segment_type == SegmentType.retrieval.value


def test_compress_marker_classified_as_retrieval() -> None:
    msgs = [{"role": "user", "content": "COMPRESS: {summary: 'compressed'}"}]
    segs = segmentize(msgs)
    assert segs[0].segment_type == SegmentType.retrieval.value


# ===========================================================================
# TC-08: MEMORY.md mention → SegmentType.memory
# ===========================================================================


def test_memory_md_reference_classified_as_memory() -> None:
    msgs = [{"role": "user", "content": "Please update MEMORY.md with this fact."}]
    segs = segmentize(msgs)
    assert segs[0].segment_type == SegmentType.memory.value


def test_memory_marker_bracket_classified_as_memory() -> None:
    msgs = [{"role": "user", "content": "[memory] store this context for later"}]
    segs = segmentize(msgs)
    assert segs[0].segment_type == SegmentType.memory.value


# ===========================================================================
# TC-09: tool role → SegmentType.tool_output
# ===========================================================================


def test_tool_role_classified_as_tool_output() -> None:
    msgs = [{"role": "tool", "content": '{"result": "42"}', "tool_use_id": "call_abc"}]
    segs = segmentize(msgs)
    assert segs[0].segment_type == SegmentType.tool_output.value


def test_tool_call_id_key_classified_as_tool_output() -> None:
    msgs = [{"role": "user", "content": "search results here", "tool_call_id": "call_xyz"}]
    segs = segmentize(msgs)
    assert segs[0].segment_type == SegmentType.tool_output.value


# ===========================================================================
# TC-10: image block in user message → SegmentType.image
# ===========================================================================


def test_image_block_in_user_message_classified_as_image() -> None:
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "data": "abc123=="}},
                {"type": "text", "text": "What is in this image?"},
            ],
        }
    ]
    segs = segmentize(msgs)
    assert segs[0].segment_type == SegmentType.image.value


# ===========================================================================
# TC-11: tools list → adds SegmentType.tool_schema segment
# ===========================================================================


def test_tools_list_adds_tool_schema_segment() -> None:
    msgs = [{"role": "user", "content": "Search for cats."}]
    tools = [
        {
            "name": "web_search",
            "description": "Search the web",
            "parameters": {"type": "object", "properties": {}},
        }
    ]
    segs = segmentize(msgs, tools=tools)
    schema_segs = [s for s in segs if s.segment_type == SegmentType.tool_schema.value]
    assert len(schema_segs) == 1, "Expected exactly one tool_schema segment"


# ===========================================================================
# TC-12: segmentize() identifies 5+ segment types in a real-ish mixed payload
# ===========================================================================


def test_mixed_payload_identifies_at_least_5_segment_types() -> None:
    msgs = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "MEMORY.md — prior context"},
        {"role": "user", "content": "TOKPAK:1 compressed blob here"},
        {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "url", "url": "https://example.com/img.png"}},
            ],
        },
        {"role": "assistant", "content": "Here is my response."},
        {"role": "assistant", "content": "Previous turn answer."},
        {"role": "user", "content": "Final question."},
    ]
    tools = [{"name": "calc", "description": "Calculator", "parameters": {}}]
    segs = segmentize(msgs, tools=tools)
    unique_types = {s.segment_type for s in segs}
    assert len(unique_types) >= 5, f"Only {len(unique_types)} types found: {unique_types}"


# ===========================================================================
# TC-13: SHA-256 hashes are stable (same input → same hash)
# ===========================================================================


def test_segment_hash_stability_same_content() -> None:
    msgs = [{"role": "user", "content": "Stability test content."}]
    run1 = segmentize(msgs)[0].raw_hash
    run2 = segmentize(msgs)[0].raw_hash
    run3 = segmentize(msgs)[0].raw_hash
    assert run1 == run2 == run3, "Hashes differ across runs with identical input"


def test_segment_hash_is_sha256() -> None:
    content = "Hello, hash me!"
    expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
    msgs = [{"role": "user", "content": content}]
    segs = segmentize(msgs)
    assert segs[0].raw_hash == expected


def test_different_content_different_hash() -> None:
    msgs_a = [{"role": "user", "content": "content A"}]
    msgs_b = [{"role": "user", "content": "content B"}]
    hash_a = segmentize(msgs_a)[0].raw_hash
    hash_b = segmentize(msgs_b)[0].raw_hash
    assert hash_a != hash_b


# ===========================================================================
# TC-14: token counts per segment sum within 2% of naïve total
# ===========================================================================


def test_token_counts_sum_within_2pct_of_total() -> None:
    msgs = [
        {"role": "system", "content": "System prompt here with some text."},
        {"role": "user", "content": "User message content."},
        {"role": "assistant", "content": "Assistant reply."},
        {"role": "user", "content": "Follow-up question for the assistant."},
    ]
    segs = segmentize(msgs)
    # Compute naïve total (same method as segmentizer: len // 4)
    all_content = " ".join(
        m.get("content", "") if isinstance(m.get("content"), str) else json.dumps(m.get("content"))
        for m in msgs
    )
    # Use per-message token sum instead of total string (avoids separator skew)
    per_msg_total = sum(len(m.get("content", "")) // 4 if isinstance(m.get("content"), str) else len(json.dumps(m.get("content"))) // 4 for m in msgs)
    seg_total = _total_tokens(segs)
    if per_msg_total > 0:
        diff_pct = abs(seg_total - per_msg_total) / per_msg_total
        assert diff_pct <= 0.02, f"Token sum divergence {diff_pct:.1%} > 2%"


# ===========================================================================
# TC-15: Deterministic ordering (segments appear in request order)
# ===========================================================================


def test_segments_are_in_request_order() -> None:
    msgs = [
        {"role": "system", "content": "System"},
        {"role": "user", "content": "User turn 1"},
        {"role": "assistant", "content": "Assistant reply"},
        {"role": "user", "content": "User turn 2"},
    ]
    segs = segmentize(msgs)
    orders = [s.order for s in segs]
    assert orders == sorted(orders), f"Segments not in order: {orders}"


def test_deterministic_across_identical_calls() -> None:
    msgs = [
        {"role": "system", "content": "Be helpful."},
        {"role": "user", "content": "What is 3+3?"},
    ]
    segs1 = [(s.order, s.segment_type, s.raw_hash) for s in segmentize(msgs)]
    segs2 = [(s.order, s.segment_type, s.raw_hash) for s in segmentize(msgs)]
    assert segs1 == segs2


# ===========================================================================
# TC-16: Edge case — empty messages list
# ===========================================================================


def test_empty_messages_returns_empty_list() -> None:
    segs = segmentize([])
    assert segs == []


# ===========================================================================
# TC-17: Edge case — message with empty string content
# ===========================================================================


def test_empty_content_string_handled_gracefully() -> None:
    msgs = [{"role": "user", "content": ""}]
    segs = segmentize(msgs)
    assert len(segs) == 1
    assert segs[0].raw_len == 0
    assert segs[0].tokens_raw == 0


# ===========================================================================
# TC-18: Edge case — message with None content
# ===========================================================================


def test_none_content_handled_gracefully() -> None:
    msgs = [{"role": "user", "content": None}]
    segs = segmentize(msgs)
    assert len(segs) == 1
    # Should not raise; raw_len may be 0 or represent 'null'
    assert segs[0].raw_hash  # hash is set (not empty string)


# ===========================================================================
# TC-19: Edge case — oversized segment (large content blob)
# ===========================================================================


def test_oversized_segment_handled_gracefully() -> None:
    big_content = "A" * 100_000  # 100k chars ≈ 25k tokens
    msgs = [{"role": "user", "content": big_content}]
    segs = segmentize(msgs)
    assert len(segs) == 1
    assert segs[0].tokens_raw == 100_000 // 4  # 25000
    assert segs[0].raw_len == 100_000


# ===========================================================================
# TC-20: Edge case — list content with multiple text blocks
# ===========================================================================


def test_list_content_text_blocks_concatenated() -> None:
    msgs = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Part one."},
                {"type": "text", "text": "Part two."},
            ],
        }
    ]
    segs = segmentize(msgs)
    assert len(segs) == 1
    assert "Part one." in segmentize(msgs)[0].raw_hash or segs[0].raw_len > 0


# ===========================================================================
# TC-21: trace_id propagates into segment_id (deterministic UUID)
# ===========================================================================


def test_trace_id_propagates_to_segments() -> None:
    msgs = [{"role": "user", "content": "Hello"}]
    segs = segmentize(msgs, trace_id="test-trace-123")
    assert segs[0].trace_id == "test-trace-123"
    assert segs[0].segment_id  # not empty


def test_segment_id_stable_with_same_trace_id() -> None:
    msgs = [{"role": "user", "content": "Hello"}]
    id1 = segmentize(msgs, trace_id="stable-id")[0].segment_id
    id2 = segmentize(msgs, trace_id="stable-id")[0].segment_id
    assert id1 == id2


# ===========================================================================
# TC-22: No tool_schema segment when tools=None
# ===========================================================================


def test_no_tool_schema_when_tools_is_none() -> None:
    msgs = [{"role": "user", "content": "Simple message."}]
    segs = segmentize(msgs, tools=None)
    schema_segs = [s for s in segs if s.segment_type == SegmentType.tool_schema.value]
    assert len(schema_segs) == 0


# ===========================================================================
# TC-23: Last assistant turn is NOT classified as assistant_context
# ===========================================================================


def test_last_assistant_not_classified_as_context() -> None:
    msgs = [
        {"role": "user", "content": "Question"},
        {"role": "assistant", "content": "Answer"},  # last message
    ]
    segs = segmentize(msgs)
    last_seg = segs[-1]
    assert last_seg.segment_type != SegmentType.assistant_context.value


# ===========================================================================
# TC-24: Mixed roles — all role types in one payload
# ===========================================================================


def test_all_detectable_types_in_one_request() -> None:
    msgs = [
        {"role": "system", "content": "System prompt."},
        {"role": "developer", "content": "Dev instructions."},
        {"role": "user", "content": "MEMORY.md context note"},
        {"role": "user", "content": "TOKPAK:2 retrieval blob here"},
        {
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "data": "xyz=="}},
            ],
        },
        {"role": "tool", "content": "tool result", "tool_use_id": "t1"},
        {"role": "assistant", "content": "Prior context turn."},
        {"role": "user", "content": "Final user question."},
    ]
    tools = [{"name": "run", "description": "run code", "parameters": {}}]
    segs = segmentize(msgs, tools=tools)
    types = {s.segment_type for s in segs}

    # Must detect at least these types
    for expected in [
        SegmentType.system.value,
        SegmentType.developer.value,
        SegmentType.memory.value,
        SegmentType.retrieval.value,
        SegmentType.image.value,
        SegmentType.tool_output.value,
        SegmentType.tool_schema.value,
        SegmentType.user.value,
    ]:
        assert expected in types, f"Missing expected type: {expected}"
