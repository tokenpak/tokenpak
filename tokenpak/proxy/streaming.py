"""
TokenPak SSE/Streaming Response Handler

Handles Server-Sent Events (SSE) streaming responses from LLM providers.
Extracts usage metrics from streaming responses.
"""

import io
import json
import zlib
from dataclasses import dataclass
from typing import Any, Dict, Iterator


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


def extract_sse_tokens(sse_bytes: bytes) -> Dict[str, int]:
    """
    Extract token usage from SSE stream bytes.

    Parses the SSE format and extracts usage information from:
    - Anthropic: message_start (cache tokens), message_delta (output tokens)
    - OpenAI: usage object with completion_tokens

    Args:
        sse_bytes: Raw bytes from SSE response

    Returns:
        Dict with output_tokens, cache_read_input_tokens, cache_creation_input_tokens
    """
    usage = StreamUsage()

    try:
        text = sse_bytes.decode("utf-8", errors="replace")

        for line in text.split("\n"):
            line = line.strip()

            # Skip non-data lines
            if not line.startswith("data: "):
                continue

            data_str = line[6:]  # Remove "data: " prefix

            # Skip the [DONE] marker
            if data_str == "[DONE]":
                continue

            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                continue

            # Anthropic: message_start contains cache token info
            if event.get("type") == "message_start":
                msg_usage = event.get("message", {}).get("usage", {})
                if "cache_read_input_tokens" in msg_usage:
                    usage.cache_read_input_tokens = msg_usage["cache_read_input_tokens"]
                if "cache_creation_input_tokens" in msg_usage:
                    usage.cache_creation_input_tokens = msg_usage["cache_creation_input_tokens"]

            # Anthropic: message_delta contains output token count
            if event.get("type") == "message_delta":
                delta_usage = event.get("usage", {})
                if "output_tokens" in delta_usage:
                    usage.output_tokens = delta_usage["output_tokens"]

            # OpenAI: usage object with completion_tokens
            if "usage" in event and "completion_tokens" in event.get("usage", {}):
                usage.output_tokens = event["usage"]["completion_tokens"]

    except Exception as e:
        # Log parsing errors but don't fail
        print(f"  ⚠️ SSE parse error: {e}")

    return usage.to_dict()


class StreamHandler:
    """
    Handles streaming responses with buffering and metrics extraction.

    Supports gzip decompression and chunk-by-chunk forwarding.
    """

    def __init__(self, content_encoding: str = ""):
        """
        Initialize stream handler.

        Args:
            content_encoding: Content-Encoding header value (e.g., "gzip")
        """
        self._buffer = io.BytesIO()
        self._chunk_count = 0
        self._decompressor = None

        if "gzip" in content_encoding:
            self._decompressor = zlib.decompressobj(zlib.MAX_WBITS | 16)

    def process_chunk(self, chunk: bytes) -> bytes:
        """
        Process a chunk from the stream.

        Decompresses if needed and buffers for later analysis.

        Args:
            chunk: Raw bytes from response

        Returns:
            Processed bytes to forward to client
        """
        self._chunk_count += 1

        if self._decompressor:
            try:
                chunk = self._decompressor.decompress(chunk)
            except Exception:
                pass

        if chunk:
            self._buffer.write(chunk)

        return chunk

    def get_buffer(self) -> bytes:
        """Get all buffered data."""
        return self._buffer.getvalue()

    def extract_usage(self) -> Dict[str, int]:
        """Extract usage metrics from buffered stream."""
        return extract_sse_tokens(self.get_buffer())

    @property
    def chunk_count(self) -> int:
        """Number of chunks processed."""
        return self._chunk_count


def iter_sse_events(stream_bytes: bytes) -> Iterator[Dict[str, Any]]:
    """
    Iterate over SSE events in a stream.

    Yields parsed JSON events from SSE data lines.
    Useful for processing events one at a time.

    Args:
        stream_bytes: Raw SSE stream bytes

    Yields:
        Parsed JSON event dicts
    """
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
