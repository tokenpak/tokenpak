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

import pytest

from tokenpak.proxy.streaming import StreamHandler, extract_sse_tokens


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
    chunk2 = b'data: [DONE]'  # no trailing newline

    handler = StreamHandler()
    handler.process_chunk(chunk1)
    handler.process_chunk(chunk2)

    buf = handler.get_buffer()
    # The [DONE] fragment must appear in the buffer after flush
    assert b"[DONE]" in buf
    usage = handler.extract_usage()
    assert usage["output_tokens"] == 7
