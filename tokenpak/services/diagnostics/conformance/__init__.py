"""TIP-1.0 self-conformance capture primitives.

This module is the single shared contract that proxy + companion emit
through so tests can validate live artifacts against the registry
schemas. There is no parallel tree: ``proxy/monitor.py``,
``proxy/middleware/*``, and ``companion/journal/store.py`` all notify
through the same helpers here.

Phase TIP-SC (2026-04-22) owns this module. Production emit paths gain
exactly one ``_notify_*`` call at the chokepoint; the notification is a
no-op when no observer is installed, so the release-default path pays
no cost.

Validation itself is delegated to the ``tokenpak-tip-validator`` PyPI
package (a ``[dev]`` extra). This module does not re-implement schema
validation — it only captures + forwards.
"""
from __future__ import annotations

import threading
from typing import Any, Callable, Mapping, Protocol, runtime_checkable


@runtime_checkable
class ConformanceObserver(Protocol):
    """Observer contract satisfied by the pytest conformance harness.

    Production code calls ``notify_*`` free functions below, which
    dispatch to the thread-local observer if one is installed.
    """

    def on_telemetry_row(self, row: Mapping[str, Any]) -> None: ...
    def on_response_headers(
        self, headers: Mapping[str, str], direction: str
    ) -> None: ...
    def on_companion_journal_row(self, row: Mapping[str, Any]) -> None: ...
    def on_capability_published(
        self, profile: str, caps: "list[str] | tuple[str, ...] | frozenset[str]"
    ) -> None: ...
    # SC+1 / SC2-01 — the capture surface for outbound-side invariants
    # (byte-identity, TTL ordering, DLP leak, header allowlist). Fired
    # at the proxy dispatch chokepoint, right before bytes go to httpx.
    def on_outbound_request(
        self,
        route_class: str,
        target_url: str,
        method: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> None: ...
    # SC+2 / SC2p-01 — the capture surface for streaming invariants
    # (I6 frame-ordering, I7 streaming cache-attribution, I10
    # streaming telemetry completeness). Fired once per complete
    # Anthropic-style SSE frame as the proxy forwards bytes to the
    # client, in receipt order.
    def on_stream_event(
        self,
        route_class: str,
        event_type: str,
        frame: bytes,
    ) -> None: ...


_tls = threading.local()


def _get() -> "ConformanceObserver | None":
    return getattr(_tls, "observer", None)


def install(observer: ConformanceObserver) -> Callable[[], None]:
    """Install ``observer`` for the current thread. Returns uninstall callback.

    Designed for pytest fixtures: install at setup, uninstall in
    teardown. Thread-local isolation means parallel tests do not race.
    """
    prior = _get()
    _tls.observer = observer

    def _uninstall() -> None:
        _tls.observer = prior

    return _uninstall


def notify_telemetry_row(row: Mapping[str, Any]) -> None:
    """Forward a wire-side telemetry row to the active observer, if any."""
    obs = _get()
    if obs is not None:
        obs.on_telemetry_row(row)


def notify_response_headers(
    headers: Mapping[str, str], direction: str = "response"
) -> None:
    """Forward an outbound header set to the active observer, if any."""
    obs = _get()
    if obs is not None:
        obs.on_response_headers(headers, direction)


def notify_companion_journal_row(row: Mapping[str, Any]) -> None:
    """Forward a companion journal row to the active observer, if any."""
    obs = _get()
    if obs is not None:
        obs.on_companion_journal_row(row)


def notify_capability_published(
    profile: str, caps: "list[str] | tuple[str, ...] | frozenset[str]"
) -> None:
    """Forward a capability self-declaration at startup to the observer."""
    obs = _get()
    if obs is not None:
        obs.on_capability_published(profile, caps)


def notify_stream_event(
    route_class: str,
    event_type: str,
    frame: bytes,
) -> None:
    """Forward a complete SSE frame to the observer, in receipt order.

    SC+2 capture surface for streaming invariants:

    - I6 frame-ordering: every frame forwarded upstream to client is
      notified here exactly once, in receipt order. Test harnesses
      assert the observed sequence equals the upstream-emitted sequence.
    - I7 streaming cache-attribution: observer filters ``event_type
      == 'message_start'`` and extracts ``usage.cache_read_input_tokens``
      / ``usage.cache_creation_input_tokens``; these must drive the
      downstream ``telemetry-row.cache_origin`` classification per
      Constitution §5.3 (the streaming analog of SC+1 I2).
    - I10 streaming telemetry completeness (indirect): a stream's
      ``message_stop`` is the signal that ``Monitor.log`` may fire —
      tests cross-correlate this with ``on_telemetry_row`` to assert
      the log fires exactly once and post-stream-end.

    Fired at the SSE forwarding loop in ``proxy/server.py``. No-op when
    no observer is installed; ship-safe.
    """
    obs = _get()
    if obs is not None:
        obs.on_stream_event(route_class, event_type, frame)


def notify_outbound_request(
    route_class: str,
    target_url: str,
    method: str,
    headers: Mapping[str, str],
    body: bytes,
) -> None:
    """Forward an outbound request (right before dispatch) to the observer.

    SC+1 capture surface for outbound-side invariants:

    - I1 byte-identity: assert ``body`` equals client input on
      ``claude-code-*`` routes.
    - I3 TTL ordering: parse ``body`` JSON; assert no 1h-after-default
      ``cache_control`` on non-byte-preserve routes.
    - I4 DLP leak: grep ``body`` for registered secret patterns when
      ``Policy.dlp_mode='redact'``.
    - I5 header allowlist: assert ``headers`` ⊆ ``PERMITTED_HEADERS_PROXY``
      (see ``tokenpak.core.contracts.permitted_headers``).

    Fired at the two dispatch chokepoints in ``proxy/server.py`` (stream
    + non-stream). No-op when no observer installed; ship-safe.
    """
    obs = _get()
    if obs is not None:
        obs.on_outbound_request(route_class, target_url, method, headers, body)


__all__ = [
    "ConformanceObserver",
    "install",
    "notify_telemetry_row",
    "notify_response_headers",
    "notify_companion_journal_row",
    "notify_capability_published",
    # SC+1 / SC2-01 — outbound-request capture surface.
    "notify_outbound_request",
    # SC+2 / SC2p-01 — streaming-SSE capture surface (I6/I7/I10).
    "notify_stream_event",
    # SC+2 / SC2p-01 — SSE frame parser used by both the production
    # chokepoint (to extract event_type) and test harnesses (to
    # reconstruct frame sequences).
    "parse_sse_frames",
    # SC-07 — doctor --conformance runner.
    "run_conformance_checks",
    "summarize",
    "exit_code_for",
]


# --------------------------------------------------------------------------- #
# SC+2 / SC2p-01 — SSE frame parser
# --------------------------------------------------------------------------- #
#
# Minimal Anthropic-style SSE frame parser. Used by the proxy streaming
# chokepoint to split incoming bytes into complete frames AND by test
# harnesses to validate frame sequences.
#
# A frame is terminated by a blank line (``\n\n`` or ``\r\n\r\n``). Inside
# a frame, lines prefixed ``event: NAME`` set the event type; lines
# prefixed ``data: …`` carry the payload. Per the HTML5 EventSource spec,
# a frame without an explicit ``event:`` field defaults to event type
# ``message`` — we surface it as the Anthropic default so tests see it
# consistently.
#
# The parser is byte-in / tuple-out. It advances over complete frames
# only; any trailing partial frame is returned as ``remainder`` for
# caller-side accumulation on the next chunk.
# --------------------------------------------------------------------------- #
def parse_sse_frames(buf: bytes) -> "tuple[list[tuple[str, bytes]], bytes]":
    """Split a byte buffer into ``(event_type, frame_bytes)`` entries + remainder.

    - ``event_type`` is the parsed ``event:`` field value or ``"message"``
      per the HTML5 EventSource default.
    - ``frame_bytes`` is the complete raw frame including its terminator
      (``\\n\\n`` or ``\\r\\n\\r\\n``). Byte-identical to what the chokepoint
      forwarded to the client, so tests can assert byte-order.
    - ``remainder`` is any trailing partial frame (no terminator yet) —
      accumulate with the next chunk.
    """
    frames: list[tuple[str, bytes]] = []
    # Accept both LF-only and CRLF terminators. We search for whichever
    # appears; Anthropic uses LF-only, but tolerate httpx-normalized CRLF.
    i = 0
    n = len(buf)
    while i < n:
        # Find the next blank-line terminator.
        idx_lf = buf.find(b"\n\n", i)
        idx_crlf = buf.find(b"\r\n\r\n", i)
        candidates = [c for c in (idx_lf, idx_crlf) if c != -1]
        if not candidates:
            break
        end = min(candidates)
        if end == idx_crlf:
            frame_end = end + 4
        else:
            frame_end = end + 2
        frame = buf[i:frame_end]
        event_type = "message"  # HTML5 default
        for line in frame.splitlines():
            if line.startswith(b"event:"):
                event_type = line[len(b"event:"):].strip().decode(
                    "utf-8", errors="replace"
                )
                break
        frames.append((event_type, bytes(frame)))
        i = frame_end
    remainder = bytes(buf[i:])
    return frames, remainder


# Re-export the SC-07 runner so ``tokenpak doctor --conformance`` and
# any other caller imports from the same diagnostics-layer namespace.
# Placed at the module bottom to avoid a circular import with
# runner.py (which imports the observer helpers above at call time,
# not at import time).
from tokenpak.services.diagnostics.conformance.runner import (  # noqa: E402
    exit_code_for,
    run_conformance_checks,
    summarize,
)
