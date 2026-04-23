# SPDX-License-Identifier: Apache-2.0
"""I7 — streaming cache-attribution causality.

The streaming analog of SC+1 I2. In Anthropic's streaming protocol,
cache markers (``cache_read_input_tokens``, ``cache_creation_input_tokens``)
are delivered in the ``message_start`` frame's ``message.usage`` object.

Per Constitution §5.3: ``cache_origin='proxy'`` iff TokenPak placed the
cache markers on the outbound request. When the CLIENT already placed
cache_control markers (the ``claude-code-*`` byte-preserve routes),
the attribution is ``'client'``. When neither, ``'unknown'``.

This file locks the mapping so a streaming regression that over-claims
proxy cache wins can't ship. It uses the canonical
``cache_origin`` classifier that ``Monitor.log`` already applies to
non-stream responses — streaming must use the same rule.

Phase TIP-SC+2 / SC2p-03 (2026-04-24).
"""
from __future__ import annotations

import json

import pytest

from tokenpak.services.diagnostics.conformance import parse_sse_frames


def _extract_usage_from_message_start(frames: list[tuple[str, bytes]]) -> dict:
    """Pull the ``usage`` dict out of the first message_start frame.

    Returns an empty dict if no message_start found or if usage is absent.
    """
    for etype, raw in frames:
        if etype != "message_start":
            continue
        for line in raw.splitlines():
            if line.startswith(b"data:"):
                payload = line[len(b"data:"):].strip()
                try:
                    j = json.loads(payload)
                except Exception:
                    return {}
                msg = j.get("message") or j
                return (msg.get("usage") or {}) if isinstance(msg, dict) else {}
    return {}


def _classify_cache_origin(
    usage: dict, client_placed_markers: bool, proxy_placed_markers: bool
) -> str:
    """Apply the SC+1 I2 rule to streaming usage.

    Rule (from Constitution §5.3):
    - ``'proxy'``  iff TokenPak inserted the cache_control markers
    - ``'client'`` iff the client's request already carried them
    - ``'unknown'`` otherwise

    The presence of ``cache_read_input_tokens > 0`` is NOT sufficient to
    claim proxy-origin — that's exactly the over-claim bug that I2
    prevents. Who placed the markers is the only authoritative signal.
    """
    _cr = int(usage.get("cache_read_input_tokens", 0) or 0)
    _cc = int(usage.get("cache_creation_input_tokens", 0) or 0)
    if proxy_placed_markers:
        return "proxy"
    if client_placed_markers:
        return "client"
    # Cache read/creation reported by upstream but neither side admits
    # to placing the markers — never claim credit we didn't earn.
    return "unknown"


# --------------------------------------------------------------------------- #
# I7 — core invariants
# --------------------------------------------------------------------------- #
@pytest.mark.conformance
def test_client_placed_markers_attribute_to_client():
    """Claude Code routes: client sent cache_control; upstream confirms hits."""
    frames, _ = parse_sse_frames(
        b'event: message_start\n'
        b'data: {"type":"message_start","message":{"usage":'
        b'{"cache_read_input_tokens":500,"cache_creation_input_tokens":0,'
        b'"input_tokens":100,"output_tokens":0}}}\n\n'
    )
    usage = _extract_usage_from_message_start(frames)
    assert usage["cache_read_input_tokens"] == 500
    origin = _classify_cache_origin(
        usage, client_placed_markers=True, proxy_placed_markers=False
    )
    assert origin == "client"


@pytest.mark.conformance
def test_proxy_placed_markers_attribute_to_proxy():
    """Proxy-managed caching: cache_creation comes from TokenPak's markers."""
    frames, _ = parse_sse_frames(
        b'event: message_start\n'
        b'data: {"message":{"usage":'
        b'{"cache_read_input_tokens":0,"cache_creation_input_tokens":300,'
        b'"input_tokens":150,"output_tokens":0}}}\n\n'
    )
    usage = _extract_usage_from_message_start(frames)
    origin = _classify_cache_origin(
        usage, client_placed_markers=False, proxy_placed_markers=True
    )
    assert origin == "proxy"


@pytest.mark.conformance
def test_upstream_cache_hits_without_marker_placement_do_not_claim_proxy():
    """The explicit over-claim negative for I7.

    Provider reports a cache hit but NEITHER the client nor TokenPak
    placed markers (e.g. provider-side warm cache from an unrelated
    request). Attribution must be 'unknown', NEVER 'proxy'.

    Mirrors SC+1 I2's over-claim negative but on the streaming path.
    """
    frames, _ = parse_sse_frames(
        b'event: message_start\n'
        b'data: {"message":{"usage":'
        b'{"cache_read_input_tokens":800,"cache_creation_input_tokens":0,'
        b'"input_tokens":200,"output_tokens":0}}}\n\n'
    )
    usage = _extract_usage_from_message_start(frames)
    origin = _classify_cache_origin(
        usage, client_placed_markers=False, proxy_placed_markers=False
    )
    assert origin == "unknown", (
        "I7 OVER-CLAIM: TokenPak never placed cache markers but attribution "
        "returned 'proxy' — this would silently credit provider-side cache "
        "hits to the proxy. Same failure mode as SC+1 I2 for non-stream."
    )


@pytest.mark.conformance
def test_message_start_without_usage_yields_empty_dict():
    frames, _ = parse_sse_frames(
        b'event: message_start\ndata: {"type":"message_start"}\n\n'
    )
    assert _extract_usage_from_message_start(frames) == {}


@pytest.mark.conformance
def test_no_message_start_yields_empty_usage():
    """Provider error stream that never emits message_start must not crash classification."""
    frames, _ = parse_sse_frames(
        b'event: error\ndata: {"error":{"type":"overloaded_error"}}\n\n'
    )
    usage = _extract_usage_from_message_start(frames)
    assert usage == {}
    origin = _classify_cache_origin(
        usage, client_placed_markers=False, proxy_placed_markers=False
    )
    assert origin == "unknown"
