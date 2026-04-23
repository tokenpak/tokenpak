# SPDX-License-Identifier: Apache-2.0
"""I6 — SSE frame ordering.

Every complete SSE frame the proxy forwards to the client MUST be
notified to the conformance observer exactly once, in the same order
the bytes were received. No reordering, no drops, no synthesis.

This file exercises the parser + observer contract via a pure-Python
driver that mimics the proxy chokepoint loop — no HTTP stack needed.
The chokepoint in ``proxy/server.py`` is wired identically; SC+1 Layer-C
integration tests prove it runs live.

Phase TIP-SC+2 / SC2p-02 (2026-04-24).
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest

from tokenpak.services.diagnostics import conformance as _conf
from tokenpak.services.diagnostics.conformance import parse_sse_frames


# --------------------------------------------------------------------------- #
# Chokepoint-mimicking driver
# --------------------------------------------------------------------------- #
def _drive(chunks: list[bytes], route_class: str = "claude-code-cli") -> None:
    """Replicate the ``proxy/server.py`` streaming chokepoint behavior.

    For each chunk: accumulate into the observer buffer, parse complete
    frames, notify each frame. Exactly mirrors the production loop so
    test + production stay aligned.
    """
    buf = b""
    for chunk in chunks:
        if not chunk:
            continue
        buf += chunk
        frames, buf = parse_sse_frames(buf)
        for event_type, raw in frames:
            _conf.notify_stream_event(route_class, event_type, raw)


class _Recorder:
    """Observer stub that records every on_stream_event call in order."""

    def __init__(self) -> None:
        self.stream_events: list[tuple[str, str, bytes]] = []
        self.telemetry_rows: list[dict] = []
        self.response_headers: list[tuple[dict, str]] = []

    # Observer protocol surface: only what this test cares about.
    def on_stream_event(self, route_class: str, event_type: str, frame: bytes) -> None:
        self.stream_events.append((route_class, event_type, frame))

    def on_telemetry_row(self, row) -> None:
        self.telemetry_rows.append(dict(row))

    def on_response_headers(self, headers, direction: str) -> None:
        self.response_headers.append((dict(headers), direction))

    def on_companion_journal_row(self, row) -> None: ...
    def on_capability_published(self, profile, caps) -> None: ...
    def on_outbound_request(self, route_class, target_url, method, headers, body) -> None: ...


@pytest.fixture
def recorder() -> Iterator[_Recorder]:
    rec = _Recorder()
    uninstall = _conf.install(rec)
    try:
        yield rec
    finally:
        uninstall()


# --------------------------------------------------------------------------- #
# Parser unit tests (I6 primitives)
# --------------------------------------------------------------------------- #
@pytest.mark.conformance
def test_parser_single_complete_frame():
    buf = b"event: message_start\ndata: {\"type\":\"message_start\"}\n\n"
    frames, remainder = parse_sse_frames(buf)
    assert len(frames) == 1
    etype, raw = frames[0]
    assert etype == "message_start"
    assert raw == buf
    assert remainder == b""


@pytest.mark.conformance
def test_parser_multiple_frames_in_one_buffer_preserve_order():
    buf = (
        b"event: message_start\ndata: {}\n\n"
        b"event: content_block_delta\ndata: {\"delta\":\"hi\"}\n\n"
        b"event: message_stop\ndata: {}\n\n"
    )
    frames, remainder = parse_sse_frames(buf)
    assert [e for e, _ in frames] == ["message_start", "content_block_delta", "message_stop"]
    assert remainder == b""


@pytest.mark.conformance
def test_parser_partial_frame_returned_as_remainder():
    buf = b"event: message_start\ndata: {}\n\nevent: content_block_delta\ndata: {"
    frames, remainder = parse_sse_frames(buf)
    assert len(frames) == 1
    assert frames[0][0] == "message_start"
    assert remainder.startswith(b"event: content_block_delta")


@pytest.mark.conformance
def test_parser_default_event_type_is_message_per_eventsource_spec():
    # No `event:` line → HTML5 EventSource default is "message".
    buf = b"data: {\"raw\":\"no-event-field\"}\n\n"
    frames, _ = parse_sse_frames(buf)
    assert frames[0][0] == "message"


@pytest.mark.conformance
def test_parser_tolerates_crlf_terminator():
    buf = b"event: ping\r\ndata: {}\r\n\r\n"
    frames, remainder = parse_sse_frames(buf)
    assert frames[0][0] == "ping"
    assert frames[0][1] == buf
    assert remainder == b""


# --------------------------------------------------------------------------- #
# Observer contract — ordering preserved across chunk boundaries
# --------------------------------------------------------------------------- #
@pytest.mark.conformance
def test_observer_receives_frames_in_receipt_order(recorder):
    # Canonical Anthropic message sequence.
    _drive([
        b"event: message_start\ndata: {}\n\n",
        b"event: content_block_start\ndata: {}\n\n",
        b"event: content_block_delta\ndata: {\"delta\":\"h\"}\n\n",
        b"event: content_block_delta\ndata: {\"delta\":\"i\"}\n\n",
        b"event: content_block_stop\ndata: {}\n\n",
        b"event: message_delta\ndata: {\"usage\":{\"output_tokens\":2}}\n\n",
        b"event: message_stop\ndata: {}\n\n",
    ])
    observed = [e for _, e, _ in recorder.stream_events]
    assert observed == [
        "message_start",
        "content_block_start",
        "content_block_delta",
        "content_block_delta",
        "content_block_stop",
        "message_delta",
        "message_stop",
    ]


@pytest.mark.conformance
def test_observer_byte_identity_frame_by_frame(recorder):
    # Each frame the observer sees must be byte-identical to what the
    # client would receive on the wire. Guards against any sneaky
    # reformatting between parse and notify.
    raw_frames = [
        b"event: message_start\ndata: {\"a\":1}\n\n",
        b"event: content_block_delta\ndata: {\"delta\":\"byte-identity\"}\n\n",
        b"event: message_stop\ndata: {}\n\n",
    ]
    _drive([b"".join(raw_frames)])
    got = [raw for _, _, raw in recorder.stream_events]
    assert got == raw_frames


@pytest.mark.conformance
def test_observer_handles_frames_split_across_chunks(recorder):
    # Upstream is allowed to split an SSE frame across multiple chunks.
    # The chokepoint MUST wait for the terminator before notifying.
    full = b"event: message_start\ndata: {\"hello\":\"world\"}\n\n"
    # Split mid-data, mid-terminator.
    _drive([full[:20], full[20:45], full[45:]])
    assert len(recorder.stream_events) == 1
    _, etype, raw = recorder.stream_events[0]
    assert etype == "message_start"
    assert raw == full


@pytest.mark.conformance
def test_observer_route_class_propagated(recorder):
    _drive([b"event: ping\ndata: {}\n\n"], route_class="claude-code-tui")
    rc, etype, _ = recorder.stream_events[0]
    assert rc == "claude-code-tui"
    assert etype == "ping"
