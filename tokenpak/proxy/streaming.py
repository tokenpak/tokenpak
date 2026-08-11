"""
TokenPak SSE / Streaming utilities.

Provides:
- extract_sse_tokens(): parse SSE bytes → usage dict
- _extract_sse_tokens(): legacy alias used by runtime/proxy.py
- StreamUsage: dataclass for streaming usage metrics
- StreamHandler: buffered stream handler with gzip support
- iter_sse_events(): iterate parsed events from SSE bytes

Merged from proxy/ and agent.proxy/.
"""

import io
import json
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, Iterator

# ---------------------------------------------------------------------------
# StreamUsage dataclass (merged from agent.proxy.streaming)
# ---------------------------------------------------------------------------


@dataclass
class StreamUsage:
    """Usage metrics extracted from streaming response."""

    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0

    def to_dict(self) -> Dict[str, int]:
        return {
            "output_tokens": self.output_tokens,
            "cache_read_input_tokens": self.cache_read_input_tokens,
            "cache_creation_input_tokens": self.cache_creation_input_tokens,
        }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _extract_sse_usage(sse_bytes: bytes) -> dict[str, object] | None:
    """Return the provider's cumulative usage object from an SSE copy.

    The stream itself is never rewritten. Anthropic splits usage between
    ``message_start`` and ``message_delta``; OpenAI Responses puts the final
    object under ``response.completed.response.usage``; Gemini uses
    ``usageMetadata``. Later cumulative values win while fields emitted only
    at stream start are retained.
    """
    merged: dict[str, object] = {}
    found = False
    for event in iter_sse_events(sse_bytes):
        if not isinstance(event, Mapping):
            continue

        candidates: list[object] = []
        if event.get("type") == "message_start":
            message = event.get("message")
            if isinstance(message, Mapping):
                candidates.append(message.get("usage"))

        candidates.extend((event.get("usage"), event.get("usageMetadata")))
        response = event.get("response")
        if isinstance(response, Mapping):
            candidates.extend((response.get("usage"), response.get("usageMetadata")))

        for candidate in candidates:
            if isinstance(candidate, Mapping):
                merged.update({str(key): value for key, value in candidate.items()})
                found = True
    return merged if found else None


def _usage_int(usage: Mapping[str, object], *names: str) -> int | None:
    for name in names:
        value = usage.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _sse_data_payload(line: str) -> str | None:
    """Return an SSE data payload, accepting the optional post-colon space."""
    line = line.strip()
    if not line.startswith("data:"):
        return None
    payload = line[5:]
    return payload[1:] if payload.startswith(" ") else payload


def extract_sse_tokens(sse_bytes: bytes) -> Dict[str, int]:
    """
    Extract token usage metrics from raw SSE stream bytes.

    Supports:
    - Anthropic: message_start (cache tokens) + message_delta (output tokens)
    - OpenAI: usage.completion_tokens

    Returns dict with keys:
        output_tokens, cache_read_input_tokens, cache_creation_input_tokens,
        cache_creation_ephemeral_1h_input_tokens,
        cache_creation_ephemeral_5m_input_tokens

    The ``cache_creation_ephemeral_*`` keys are populated from the
    ``usage.cache_creation`` sub-object when Anthropic includes the per-TTL
    breakdown (extended cache feature). They default to 0 otherwise — additive,
    backward-compatible with existing callers that only read the flat total.
    """
    result: Dict[str, int] = {
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_creation_ephemeral_1h_input_tokens": 0,
        "cache_creation_ephemeral_5m_input_tokens": 0,
    }
    usage = _extract_sse_usage(sse_bytes)
    if usage is None:
        return result

    output_tokens = _usage_int(
        usage,
        "output_tokens",
        "completion_tokens",
        "candidatesTokenCount",
        "candidates_token_count",
    )
    if output_tokens is not None:
        result["output_tokens"] = output_tokens

    cache_read_tokens = _usage_int(
        usage,
        "cache_read_input_tokens",
        "cachedContentTokenCount",
        "cached_content_token_count",
    )
    if cache_read_tokens is None:
        details = usage.get("input_tokens_details") or usage.get("prompt_tokens_details")
        if isinstance(details, Mapping):
            cache_read_tokens = _usage_int(details, "cached_tokens")
    if cache_read_tokens is not None:
        result["cache_read_input_tokens"] = cache_read_tokens

    cache_creation_tokens = _usage_int(usage, "cache_creation_input_tokens")
    if cache_creation_tokens is not None:
        result["cache_creation_input_tokens"] = cache_creation_tokens

    cache_creation = usage.get("cache_creation")
    if isinstance(cache_creation, Mapping):
        one_hour = _usage_int(cache_creation, "ephemeral_1h_input_tokens")
        five_minutes = _usage_int(cache_creation, "ephemeral_5m_input_tokens")
        if one_hour is not None:
            result["cache_creation_ephemeral_1h_input_tokens"] = one_hour
        if five_minutes is not None:
            result["cache_creation_ephemeral_5m_input_tokens"] = five_minutes

    return result


def _extract_sse_stop_reason(sse_bytes: bytes) -> str:
    """
    Extract the final ``stop_reason`` from raw SSE stream bytes.

    Anthropic streams carry the stop reason in the ``message_delta`` event
    (``delta.stop_reason``). This is a read-only observation on the buffered
    stream copy - the forwarded bytes are never modified. Returns the last
    non-empty stop_reason seen (streams emit exactly one in practice), or
    ``""`` when the stream contains none (e.g. errored / truncated streams,
    non-Anthropic providers).
    """
    stop_reason = ""
    try:
        text = sse_bytes.decode("utf-8", errors="replace")
        for line in text.splitlines():
            data_str = _sse_data_payload(line)
            if data_str is None:
                continue
            if data_str == "[DONE]":
                continue
            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "message_delta":
                continue
            delta = event.get("delta")
            if isinstance(delta, dict):
                value = delta.get("stop_reason")
                if value:
                    stop_reason = str(value)
    except Exception:
        # Fail-open: stop_reason observation must never break stream handling.
        return stop_reason
    return stop_reason


# Legacy name used by runtime/proxy.py
_extract_sse_tokens = extract_sse_tokens


def iter_sse_events(stream_bytes: bytes) -> Iterator[Dict[str, Any]]:
    """Yield parsed JSON events from raw SSE bytes."""
    text = stream_bytes.decode("utf-8", errors="replace")
    for line in text.splitlines():
        data_str = _sse_data_payload(line)
        if data_str is None:
            continue
        if data_str == "[DONE]":
            continue
        try:
            yield json.loads(data_str)
        except json.JSONDecodeError:
            continue


# ---------------------------------------------------------------------------
# StreamHandler (merged from agent.proxy.streaming)
# ---------------------------------------------------------------------------


class StreamHandler:
    """
    Handles streaming responses with buffering and metrics extraction.

    Supports gzip decompression and chunk-by-chunk forwarding.
    """

    def __init__(self, content_encoding: str = ""):
        self._buffer = io.BytesIO()
        self._chunk_count = 0
        self._decompressor = None
        # Line-level buffer: accumulates partial text until a newline arrives.
        # Prevents cross-chunk SSE parse failures when a data: line is split
        # across two recv() calls.
        self._line_buffer: str = ""

        if "gzip" in content_encoding:
            self._decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)

    def process_chunk(self, chunk: bytes) -> bytes:
        """Process a chunk: decompress if needed, buffer for later analysis.

        Cross-chunk SSE lines are held in self._line_buffer until a newline
        arrives, then flushed into self._buffer as a complete line.
        """
        self._chunk_count += 1
        if self._decompressor:
            try:
                chunk = self._decompressor.decompress(chunk)
            except Exception:
                pass
        if chunk:
            text = chunk.decode("utf-8", errors="replace")
            self._line_buffer += text
            # Flush all complete lines into the byte buffer; keep the remainder.
            while "\n" in self._line_buffer:
                line, self._line_buffer = self._line_buffer.split("\n", 1)
                self._buffer.write((line + "\n").encode("utf-8"))
        return chunk

    def get_buffer(self) -> bytes:
        """Get all buffered data, flushing any partial line held in the line buffer."""
        if self._line_buffer:
            self._buffer.write(self._line_buffer.encode("utf-8"))
            self._line_buffer = ""
        return self._buffer.getvalue()

    def extract_usage(self) -> Dict[str, int]:
        """Extract usage metrics from buffered stream."""
        return extract_sse_tokens(self.get_buffer())

    @property
    def chunk_count(self) -> int:
        """Number of chunks processed."""
        return self._chunk_count
