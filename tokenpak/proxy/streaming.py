"""
TokenPak SSE / Streaming utilities.

Provides:
- extract_sse_tokens(): parse SSE bytes → usage dict
- _extract_sse_tokens(): legacy alias used by runtime/proxy.py
- StreamUsage: dataclass for streaming usage metrics
- StreamHandler: buffered stream handler with gzip support
- iter_sse_events(): iterate parsed events from SSE bytes

Merged from proxy/ and agent.proxy/ (FIN-07).
"""

import io
import json
import zlib
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

def extract_sse_tokens(sse_bytes: bytes) -> Dict[str, int]:
    """
    Extract token usage metrics from raw SSE stream bytes.

    Supports:
    - Anthropic: message_start (cache tokens) + message_delta (output tokens)
    - OpenAI: usage.completion_tokens

    Returns dict with keys:
        output_tokens, cache_read_input_tokens, cache_creation_input_tokens
    """
    result: Dict[str, int] = {
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    }
    try:
        text = sse_bytes.decode("utf-8", errors="replace")
        for line in text.split("\n"):
            line = line.strip()
            if not line.startswith("data: "):
                continue
            data_str = line[6:]
            if data_str == "[DONE]":
                continue
            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            # Anthropic: cache tokens arrive in message_start
            if event.get("type") == "message_start":
                usage = event.get("message", {}).get("usage", {})
                if "cache_read_input_tokens" in usage:
                    result["cache_read_input_tokens"] = usage["cache_read_input_tokens"]
                if "cache_creation_input_tokens" in usage:
                    result["cache_creation_input_tokens"] = usage["cache_creation_input_tokens"]

            # Anthropic: output tokens arrive in message_delta
            if event.get("type") == "message_delta":
                usage = event.get("usage", {})
                if "output_tokens" in usage:
                    result["output_tokens"] = usage["output_tokens"]

            # OpenAI: completion_tokens in usage block
            if "usage" in event and "completion_tokens" in event.get("usage", {}):
                result["output_tokens"] = event["usage"]["completion_tokens"]

    except Exception as e:
        print(f"  ⚠️ SSE parse error: {e}")

    return result


# Legacy name used by runtime/proxy.py
_extract_sse_tokens = extract_sse_tokens


def iter_sse_events(stream_bytes: bytes) -> Iterator[Dict[str, Any]]:
    """Yield parsed JSON events from raw SSE bytes."""
    text = stream_bytes.decode("utf-8", errors="replace")
    for line in text.split("\n"):
        line = line.strip()
        if not line.startswith("data: "):
            continue
        data_str = line[6:]
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
