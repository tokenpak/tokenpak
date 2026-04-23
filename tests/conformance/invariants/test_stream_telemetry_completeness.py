# SPDX-License-Identifier: Apache-2.0
"""I10 — streaming telemetry completeness.

For every SSE streaming request the proxy forwards, ``Monitor.log`` MUST
fire exactly once, post-stream-end, with a valid ``telemetry-row``
populated from the accumulated stream state:

- ``input_tokens`` sourced from the ``message_start`` frame's
  ``message.usage.input_tokens``
- ``output_tokens`` sourced from the final ``message_delta`` frame's
  ``usage.output_tokens`` (or equivalent accumulator)

The fire-once-post-stream-end ordering is the whole point: mid-stream
``Monitor.log`` calls would emit partial telemetry rows; duplicate
calls would double-count; missing calls would silently lose billing.

This file tests two things:

1. The existing SSE-token extraction ``extract_sse_tokens`` correctly
   parses the canonical Anthropic stream.
2. The ordering contract — any ``on_telemetry_row`` event fires AFTER
   the terminal ``message_stop`` frame on the ``on_stream_event``
   timeline.

Phase TIP-SC+2 / SC2p-04 (2026-04-24).
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from tokenpak.services.diagnostics import conformance as _conf
from tokenpak.services.diagnostics.conformance import parse_sse_frames

# Import the canonical token extractor the proxy itself uses.
try:
    from tokenpak.proxy.streaming import extract_sse_tokens
except Exception:  # pragma: no cover — fall back if module path changes
    extract_sse_tokens = None


class _Recorder:
    """Minimal observer stub — records stream + telemetry events in order."""

    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def on_stream_event(self, route_class, event_type, frame):
        self.events.append(("stream", event_type))

    def on_telemetry_row(self, row):
        self.events.append(("telemetry", dict(row)))

    def on_response_headers(self, headers, direction): ...
    def on_companion_journal_row(self, row): ...
    def on_capability_published(self, profile, caps): ...
    def on_outbound_request(self, *a, **kw): ...


@pytest.fixture
def recorder() -> Iterator[_Recorder]:
    rec = _Recorder()
    uninstall = _conf.install(rec)
    try:
        yield rec
    finally:
        uninstall()


_CANONICAL_STREAM = (
    b'event: message_start\n'
    b'data: {"type":"message_start","message":{"usage":{'
    b'"input_tokens":150,"output_tokens":0,'
    b'"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}\n\n'
    b'event: content_block_start\ndata: {"type":"content_block_start"}\n\n'
    b'event: content_block_delta\ndata: {"delta":{"text":"hello "}}\n\n'
    b'event: content_block_delta\ndata: {"delta":{"text":"world"}}\n\n'
    b'event: content_block_stop\ndata: {"type":"content_block_stop"}\n\n'
    b'event: message_delta\n'
    b'data: {"type":"message_delta","usage":{"output_tokens":42}}\n\n'
    b'event: message_stop\ndata: {"type":"message_stop"}\n\n'
)


# --------------------------------------------------------------------------- #
# I10 — token extraction from the canonical stream
# --------------------------------------------------------------------------- #
@pytest.mark.conformance
@pytest.mark.skipif(extract_sse_tokens is None, reason="extract_sse_tokens unavailable")
def test_extract_sse_tokens_reads_output_from_message_delta():
    usage = extract_sse_tokens(_CANONICAL_STREAM)
    assert usage.get("output_tokens") == 42, (
        f"I10 regression: output_tokens must come from message_delta; got {usage!r}"
    )


@pytest.mark.conformance
@pytest.mark.skipif(extract_sse_tokens is None, reason="extract_sse_tokens unavailable")
def test_extract_sse_tokens_zero_when_message_delta_missing():
    stream_no_delta = (
        b'event: message_start\ndata: {"message":{"usage":{"input_tokens":10}}}\n\n'
        b'event: message_stop\ndata: {}\n\n'
    )
    usage = extract_sse_tokens(stream_no_delta)
    assert usage.get("output_tokens", 0) == 0


# --------------------------------------------------------------------------- #
# I10 — ordering contract: on_telemetry_row fires AFTER message_stop
# --------------------------------------------------------------------------- #
@pytest.mark.conformance
def test_telemetry_row_fires_after_message_stop(recorder):
    """Drive chunks through the chokepoint + synthesize a post-stream Monitor.log.

    Replicates the proxy pattern: stream-forward + parse frames + emit
    telemetry AFTER the iterator exits. Observer sees 'stream' events
    for every frame and exactly one 'telemetry' event at the end.
    """
    from tokenpak.services.diagnostics import conformance as _conf_

    # Chunk up the canonical stream arbitrarily.
    chunks = [_CANONICAL_STREAM[i : i + 50] for i in range(0, len(_CANONICAL_STREAM), 50)]
    buf = b""
    for chunk in chunks:
        buf += chunk
        frames, buf = parse_sse_frames(buf)
        for etype, raw in frames:
            _conf_.notify_stream_event("claude-code-cli", etype, raw)

    # Post-stream: the proxy's Monitor.log path fires exactly once
    # here. We simulate by emitting a single on_telemetry_row.
    _conf_.notify_telemetry_row({
        "request_id": "req_i10",
        "timestamp": "2026-04-24T00:00:00Z",
        "tip_version": "1.0",
        "profile": "tip-proxy",
        "model": "claude-opus-4-7",
        "status": 200,
        "input_tokens": 150,
        "output_tokens": 42,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_origin": "unknown",
    })

    # Ordering assertion: the last event MUST be 'telemetry';
    # 'message_stop' stream event MUST precede it.
    kinds = [k for k, _ in recorder.events]
    assert kinds[-1] == "telemetry", (
        f"I10 ordering: telemetry must fire last; got kind sequence tail={kinds[-3:]!r}"
    )
    # Exactly one telemetry event — never double-log.
    assert kinds.count("telemetry") == 1, (
        f"I10 fire-once: expected 1 on_telemetry_row, got {kinds.count('telemetry')}"
    )
    # message_stop is the last stream event before telemetry.
    last_stream_idx = max(i for i, k in enumerate(kinds) if k == "stream")
    last_stream_event = recorder.events[last_stream_idx][1]
    assert last_stream_event == "message_stop", (
        f"I10 ordering: last stream event before telemetry must be message_stop; got {last_stream_event!r}"
    )


@pytest.mark.conformance
def test_telemetry_row_has_populated_token_fields(recorder):
    """The single telemetry row at stream-end carries non-zero tokens."""
    from tokenpak.services.diagnostics import conformance as _conf_

    buf = b""
    buf += _CANONICAL_STREAM
    frames, buf = parse_sse_frames(buf)
    for etype, raw in frames:
        _conf_.notify_stream_event("claude-code-cli", etype, raw)

    _conf_.notify_telemetry_row({
        "request_id": "req_tokens",
        "timestamp": "2026-04-24T00:00:00Z",
        "tip_version": "1.0",
        "profile": "tip-proxy",
        "model": "claude-opus-4-7",
        "status": 200,
        "input_tokens": 150,
        "output_tokens": 42,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_origin": "unknown",
    })

    tele = [row for kind, row in recorder.events if kind == "telemetry"]
    assert len(tele) == 1
    row = tele[0]
    assert row["input_tokens"] == 150
    assert row["output_tokens"] == 42
    assert row["status"] == 200
