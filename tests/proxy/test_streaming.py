"""
tests/proxy/test_streaming.py

Regression test for TRIX-MTC-07 Fix #3:
  StreamHandler must correctly assemble SSE messages that span multiple chunks.

Without the fix, a data: line split across two process_chunk() calls would
never be parsed because extract_sse_tokens() would see a truncated first half
and a dangling second half.
"""

from __future__ import annotations

import json

from tokenpak.proxy.streaming import StreamHandler

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_sse_event(event_type: str, payload: dict) -> bytes:
    data = json.dumps({"type": event_type, **payload})
    return f"data: {data}\n\n".encode()


# ---------------------------------------------------------------------------
# Regression test
# ---------------------------------------------------------------------------


def test_streaming_handler_cross_chunk_message_complete():
    """
    A 'data: {...}' SSE line split across two process_chunk() calls must be
    reassembled into a single parseable event in the buffer.

    Scenario:
      chunk 1: b'data: {"type": "message_delta", "usage": {"output_tok'
      chunk 2: b'ens": 42}}\n\n'

    Before the fix: extract_usage() would return output_tokens=0 because the
    partial JSON line failed to parse.
    After the fix:  extract_usage() returns output_tokens=42.
    """
    full_line = b'data: {"type": "message_delta", "usage": {"output_tokens": 42}}\n\n'
    split_at = len(full_line) // 2

    chunk1 = full_line[:split_at]
    chunk2 = full_line[split_at:]

    handler = StreamHandler()
    handler.process_chunk(chunk1)
    handler.process_chunk(chunk2)

    usage = handler.extract_usage()
    assert usage["output_tokens"] == 42, (
        f"Expected 42 output_tokens after cross-chunk assembly, got {usage}"
    )


def test_streaming_handler_multiple_events_across_chunks():
    """
    Multiple SSE events split arbitrarily across many chunks must all be
    captured in the final buffer.
    """
    msg_start = b'data: {"type": "message_start", "message": {"usage": {"cache_read_input_tokens": 10, "cache_creation_input_tokens": 5}}}\n\n'
    msg_delta = b'data: {"type": "message_delta", "usage": {"output_tokens": 100}}\n\n'
    full = msg_start + msg_delta

    handler = StreamHandler()
    # Feed in single-byte chunks to stress-test the line buffer.
    for i in range(len(full)):
        handler.process_chunk(full[i : i + 1])

    usage = handler.extract_usage()
    assert usage["output_tokens"] == 100
    assert usage["cache_read_input_tokens"] == 10
    assert usage["cache_creation_input_tokens"] == 5


def test_streaming_handler_partial_final_line_flushed_by_get_buffer():
    """
    A partial line with no trailing newline (stream ended mid-line) must be
    flushed into the buffer when get_buffer() is called.
    """
    # A complete event line followed by a truncated second line (no newline)
    chunk1 = b'data: {"type": "message_delta", "usage": {"output_tokens": 7}}\n'
    chunk2 = b"data: [DONE]"  # no trailing newline

    handler = StreamHandler()
    handler.process_chunk(chunk1)
    handler.process_chunk(chunk2)

    buf = handler.get_buffer()
    # The [DONE] fragment must appear in the buffer after flush
    assert b"[DONE]" in buf
    usage = handler.extract_usage()
    assert usage["output_tokens"] == 7


# ---------------------------------------------------------------------------
# stop_reason extraction (response-path observation, read-only)
# ---------------------------------------------------------------------------


def test_extract_sse_stop_reason_from_message_delta():
    from tokenpak.proxy.streaming import _extract_sse_stop_reason

    sse = (
        _make_sse_event("message_start", {"message": {"usage": {"input_tokens": 3}}})
        + _make_sse_event(
            "message_delta",
            {
                "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                "usage": {"output_tokens": 9},
            },
        )
        + _make_sse_event("message_stop", {})
        + b"data: [DONE]\n\n"
    )
    assert _extract_sse_stop_reason(sse) == "end_turn"


def test_extract_sse_stop_reason_refusal_distinguishable():
    """A refusal streamed over HTTP 200 must surface its stop_reason."""
    from tokenpak.proxy.streaming import _extract_sse_stop_reason

    sse = _make_sse_event(
        "message_delta",
        {"delta": {"stop_reason": "refusal", "stop_sequence": None}, "usage": {"output_tokens": 1}},
    )
    assert _extract_sse_stop_reason(sse) == "refusal"


def test_extract_sse_stop_reason_absent_or_malformed_is_empty():
    from tokenpak.proxy.streaming import _extract_sse_stop_reason

    # No message_delta at all (e.g. errored/truncated stream).
    sse = _make_sse_event("message_start", {"message": {}})
    assert _extract_sse_stop_reason(sse) == ""
    # message_delta without a stop_reason.
    sse = _make_sse_event("message_delta", {"usage": {"output_tokens": 2}})
    assert _extract_sse_stop_reason(sse) == ""
    # Garbage bytes never raise.
    assert _extract_sse_stop_reason(b"data: {not json\n\n") == ""
    assert _extract_sse_stop_reason(b"") == ""


def test_extract_response_stop_reason_non_streaming():
    from tokenpak.proxy.server import _extract_response_stop_reason

    ok = json.dumps({"stop_reason": "end_turn", "usage": {"output_tokens": 4}})
    assert _extract_response_stop_reason(ok.encode()) == "end_turn"
    refusal = json.dumps({"stop_reason": "refusal", "usage": {"output_tokens": 0}})
    assert _extract_response_stop_reason(refusal.encode()) == "refusal"
    # Absent / null / unparseable -> '' (never fabricated).
    assert _extract_response_stop_reason(b'{"usage": {"output_tokens": 4}}') == ""
    assert _extract_response_stop_reason(b'{"stop_reason": null}') == ""
    assert _extract_response_stop_reason(b"not json") == ""
