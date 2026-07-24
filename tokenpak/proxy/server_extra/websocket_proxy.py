"""
websocket_proxy.py — TokenPak WebSocket Proxy Module

Provides WebSocket connection management and compression utilities for
the TokenPak proxy layer. This module is used by the WebSocket proxy
server to manage concurrent connections and compress SSE streaming data.

Public API:
  - WebSocketConnectionManager — manages active WebSocket connections
  - WebSocketConnectionStats   — per-connection statistics
  - compress_chunk(data)        — gzip-compress a chunk
  - decompress_chunk(data)      — gzip-decompress a chunk
"""

from __future__ import annotations

import gzip
import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

try:
    from websockets.server import WebSocketServerProtocol  # type: ignore
except ImportError:
    WebSocketServerProtocol = None

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Compression utilities
# ---------------------------------------------------------------------------


def compress_chunk(data: str | bytes) -> bytes:
    """
    Gzip-compress *data*.

    Args:
        data: String or bytes to compress.

    Returns:
        Compressed bytes.
    """
    if isinstance(data, str):
        data = data.encode("utf-8")
    return gzip.compress(data)


def decompress_chunk(data: bytes) -> str:
    """
    Gzip-decompress *data* and return as a UTF-8 string.

    Args:
        data: Compressed bytes.

    Returns:
        Decompressed string.

    Raises:
        OSError / BadGzipFile: If data is not valid gzip.
    """
    return gzip.decompress(data).decode("utf-8")


# ---------------------------------------------------------------------------
# Connection statistics
# ---------------------------------------------------------------------------


@dataclass
class WebSocketConnectionStats:
    """Per-connection statistics for a WebSocket client."""

    connection_id: str
    client_address: str
    connected_at: float

    # Message counters
    messages_received: int = 0

    # Chunk / byte counters
    chunks_sent: int = 0
    bytes_sent_compressed: int = 0
    bytes_sent_uncompressed: int = 0

    # Error counters
    upstream_errors: int = 0

    # Lifecycle
    disconnected_at: Optional[float] = None
    close_code: Optional[int] = None

    # ------------------------------------------------------------------ #

    @property
    def compression_ratio(self) -> float:
        """Ratio of compressed to uncompressed bytes (lower = better compression).

        Returns 1.0 when no data has been sent to avoid division by zero.
        """
        if self.bytes_sent_uncompressed == 0:
            return 1.0
        return self.bytes_sent_compressed / self.bytes_sent_uncompressed

    @property
    def duration_seconds(self) -> float:
        """Total connection duration in seconds."""
        end = self.disconnected_at if self.disconnected_at is not None else time.time()
        return end - self.connected_at

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict for JSON output / dashboard."""
        return {
            "connection_id": self.connection_id,
            "client_address": self.client_address,
            "connected_at": self.connected_at,
            "disconnected_at": self.disconnected_at,
            "close_code": self.close_code,
            "messages_received": self.messages_received,
            "chunks_sent": self.chunks_sent,
            "bytes_sent": self.bytes_sent_compressed,
            "bytes_uncompressed": self.bytes_sent_uncompressed,
            "compression_ratio": self.compression_ratio,
            "upstream_errors": self.upstream_errors,
            "duration_seconds": self.duration_seconds,
        }


# ---------------------------------------------------------------------------
# Connection manager
# ---------------------------------------------------------------------------


class WebSocketConnectionManager:
    """
    Thread-safe (GIL-protected) manager for WebSocket connection lifecycle.

    Tracks active and historical connections, enforces the max-connection
    limit, and accumulates per-connection statistics.

    Args:
        max_connections: Maximum number of simultaneous active connections.
    """

    def __init__(self, max_connections: int = 100) -> None:
        self._max_connections = max_connections
        # Active connections (currently connected)
        self._active: Dict[str, WebSocketConnectionStats] = {}
        # All stats including closed connections
        self._all: Dict[str, WebSocketConnectionStats] = {}

    # ------------------------------------------------------------------ #
    # Capacity
    # ------------------------------------------------------------------ #

    def can_accept(self) -> bool:
        """Return True if the server can accept another connection."""
        return len(self._active) < self._max_connections

    def active_count(self) -> int:
        """Return the number of currently active connections."""
        return len(self._active)

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #

    def register(self, connection_id: str, client_address: str) -> bool:
        """
        Register a new connection.

        Args:
            connection_id: Unique identifier for this connection.
            client_address: Remote address string (e.g. "127.0.0.1:12345").

        Returns:
            True if registered successfully, False if at connection limit.
        """
        if not self.can_accept():
            logger.warning(
                "Connection limit reached (%d); rejecting %s",
                self._max_connections,
                connection_id,
            )
            return False

        stats = WebSocketConnectionStats(
            connection_id=connection_id,
            client_address=client_address,
            connected_at=time.time(),
        )
        self._active[connection_id] = stats
        self._all[connection_id] = stats
        logger.debug("Registered connection %s from %s", connection_id, client_address)
        return True

    def unregister(self, connection_id: str, close_code: Optional[int] = None) -> None:
        """
        Unregister an active connection.

        The connection stats are retained in history; only the active slot
        is freed.

        Args:
            connection_id: The connection to close.
            close_code: WebSocket close code (e.g. 1000 = normal).
        """
        stats = self._active.pop(connection_id, None)
        if stats is not None:
            stats.disconnected_at = time.time()
            stats.close_code = close_code
            logger.debug("Unregistered connection %s (code=%s)", connection_id, close_code)

    # ------------------------------------------------------------------ #
    # Stats recording
    # ------------------------------------------------------------------ #

    def record_message(self, connection_id: str) -> None:
        """Increment the messages-received counter for *connection_id*."""
        stats = self._all.get(connection_id)
        if stats is not None:
            stats.messages_received += 1

    def record_chunk(self, connection_id: str, compressed: int, uncompressed: int) -> None:
        """
        Record a compressed chunk sent to *connection_id*.

        Args:
            connection_id: Target connection.
            compressed: Byte size of the compressed chunk.
            uncompressed: Byte size of the original chunk.
        """
        stats = self._all.get(connection_id)
        if stats is not None:
            stats.chunks_sent += 1
            stats.bytes_sent_compressed += compressed
            stats.bytes_sent_uncompressed += uncompressed

    def record_upstream_error(self, connection_id: str) -> None:
        """Increment the upstream-error counter for *connection_id*."""
        stats = self._all.get(connection_id)
        if stats is not None:
            stats.upstream_errors += 1

    # ------------------------------------------------------------------ #
    # Queries
    # ------------------------------------------------------------------ #

    def get_stats(self, connection_id: str) -> Optional[WebSocketConnectionStats]:
        """Return stats for *connection_id* (active or historical), or None."""
        return self._all.get(connection_id)

    def get_all_stats(self) -> List[dict[str, Any]]:
        """Return a list of serialised stats dicts for all tracked connections."""
        return [s.to_dict() for s in self._all.values()]
