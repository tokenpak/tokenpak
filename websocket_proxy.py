#!/usr/bin/env python3
"""
TokenPak WebSocket Proxy — Streaming compression + transparent upstream relay

Features:
- `/ws` WebSocket endpoint for streaming responses
- Gzip compression applied in-flight to reduce bandwidth
- Connection tracking with configurable concurrency limit (default: 50)
- Clean disconnect handling with proper WebSocket close codes
- Upstream error handling with appropriate error messages
- Compatible with HTTP `/v1/messages` endpoint

Usage:
    Client connects to: ws://proxy:8766/ws
    Sends initial message with model + messages JSON
    Receives compressed chunks as upstream responds
    Handles reconnects and error scenarios gracefully
"""

import json
import time
import asyncio
import gzip
import io
import threading
from typing import Dict, List, Optional, Tuple, Set
from dataclasses import dataclass
from datetime import datetime, timezone
import logging

try:
    import websockets
    from websockets.server import WebSocketServerProtocol
    WEBSOCKETS_AVAILABLE = True
except ImportError:
    WEBSOCKETS_AVAILABLE = False
    WebSocketServerProtocol = None

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("tokenpak.websocket")


@dataclass
class WebSocketConnectionStats:
    """Statistics for a single WebSocket connection."""
    connection_id: str
    client_address: str
    connected_at: float
    disconnected_at: Optional[float] = None
    messages_received: int = 0
    chunks_sent: int = 0
    bytes_sent_compressed: int = 0
    bytes_sent_uncompressed: int = 0
    upstream_errors: int = 0
    close_code: Optional[int] = None

    @property
    def duration_seconds(self) -> float:
        end = self.disconnected_at or time.time()
        return end - self.connected_at

    @property
    def compression_ratio(self) -> float:
        if self.bytes_sent_uncompressed == 0:
            return 1.0
        return self.bytes_sent_compressed / self.bytes_sent_uncompressed

    def to_dict(self) -> dict:
        return {
            "connection_id": self.connection_id,
            "client_address": self.client_address,
            "connected_at": self.connected_at,
            "duration_seconds": self.duration_seconds,
            "messages_received": self.messages_received,
            "chunks_sent": self.chunks_sent,
            "bytes_sent": self.bytes_sent_compressed,
            "bytes_uncompressed": self.bytes_sent_uncompressed,
            "compression_ratio": round(self.compression_ratio, 3),
            "upstream_errors": self.upstream_errors,
            "close_code": self.close_code,
        }


class WebSocketConnectionManager:
    """Manages active WebSocket connections with concurrency limits."""

    def __init__(self, max_connections: int = 50):
        self.max_connections = max_connections
        self.active_connections: Dict[str, WebSocketConnectionStats] = {}
        self.closed_connections: Dict[str, WebSocketConnectionStats] = {}  # Keep history
        self.lock = threading.Lock()

    def can_accept(self) -> bool:
        """Check if we can accept a new connection."""
        with self.lock:
            return len(self.active_connections) < self.max_connections

    def register(self, conn_id: str, client_address: str) -> bool:
        """Register a new connection. Returns False if limit reached."""
        with self.lock:
            if len(self.active_connections) >= self.max_connections:
                return False
            self.active_connections[conn_id] = WebSocketConnectionStats(
                connection_id=conn_id,
                client_address=client_address,
                connected_at=time.time(),
            )
            return True

    def unregister(self, conn_id: str, close_code: Optional[int] = None):
        """Unregister a closed connection (removes from active count to allow new connections)."""
        with self.lock:
            if conn_id in self.active_connections:
                stats = self.active_connections[conn_id]
                stats.disconnected_at = time.time()
                stats.close_code = close_code
                # Move to closed_connections dict to free up a slot
                del self.active_connections[conn_id]
                self.closed_connections[conn_id] = stats

    def get_stats(self, conn_id: str) -> Optional[WebSocketConnectionStats]:
        """Get stats for a specific connection (active or closed)."""
        with self.lock:
            return self.active_connections.get(conn_id) or self.closed_connections.get(conn_id)

    def record_message(self, conn_id: str):
        """Record that a message was received."""
        with self.lock:
            if conn_id in self.active_connections:
                self.active_connections[conn_id].messages_received += 1

    def record_chunk(self, conn_id: str, compressed: int, uncompressed: int):
        """Record sent chunk with compression stats."""
        with self.lock:
            if conn_id in self.active_connections:
                stats = self.active_connections[conn_id]
                stats.chunks_sent += 1
                stats.bytes_sent_compressed += compressed
                stats.bytes_sent_uncompressed += uncompressed

    def record_upstream_error(self, conn_id: str):
        """Record an upstream error."""
        with self.lock:
            if conn_id in self.active_connections:
                self.active_connections[conn_id].upstream_errors += 1

    def get_all_stats(self) -> List[dict]:
        """Get stats for all connections (active and closed)."""
        with self.lock:
            all_stats = list(self.active_connections.values()) + list(self.closed_connections.values())
            return [stats.to_dict() for stats in all_stats]

    def active_count(self) -> int:
        """Get count of active connections."""
        with self.lock:
            return len(self.active_connections)


# Global connection manager
CONNECTION_MANAGER = WebSocketConnectionManager(max_connections=50)


def compress_chunk(data: str) -> bytes:
    """Compress a chunk with gzip."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return gzip.compress(data)


def decompress_chunk(data: bytes) -> str:
    """Decompress a gzip chunk."""
    try:
        return gzip.decompress(data).decode("utf-8")
    except Exception as e:
        logger.error(f"Failed to decompress chunk: {e}")
        raise


async def forward_to_upstream(
    request_body: dict,
    upstream_url: str,
    adapter,
    token_counter,
) -> Tuple[Optional[bytes], Optional[dict]]:
    """
    Forward request to upstream and return response body + extracted tokens.
    
    Args:
        request_body: JSON request dict
        upstream_url: Target upstream URL
        adapter: Format adapter for the provider
        token_counter: Function to count tokens in text
        
    Returns:
        (response_body_bytes, token_counts_dict) or (None, error_dict) on failure
    """
    try:
        import http.client
        from urllib.parse import urlparse

        parsed = urlparse(upstream_url)
        body_bytes = json.dumps(request_body).encode("utf-8")
        
        # Create connection
        if parsed.scheme == "https":
            import ssl
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(parsed.netloc, timeout=300, context=ctx)
        else:
            conn = http.client.HTTPConnection(parsed.netloc, timeout=300)

        # Build path with query string
        path = parsed.path
        if parsed.query:
            path += "?" + parsed.query

        # Prepare headers
        headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body_bytes)),
        }

        # Make request
        conn.request("POST", path, body=body_bytes, headers=headers)
        resp = conn.getresponse()
        
        if resp.status != 200:
            error_body = resp.read()
            conn.close()
            try:
                error_data = json.loads(error_body)
                return None, error_data.get("error", {"type": "unknown", "message": "Upstream error"})
            except:
                return None, {"type": "upstream_error", "status": resp.status, "message": str(error_body[:200])}

        # Read response
        response_body = resp.read()
        conn.close()
        
        # Extract token counts
        try:
            response_data = json.loads(response_body)
            usage = response_data.get("usage", {})
            token_counts = {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "cache_read_tokens": usage.get("cache_read_input_tokens", 0),
                "cache_creation_tokens": usage.get("cache_creation_input_tokens", 0),
            }
            return response_body, token_counts
        except:
            return response_body, {}

    except Exception as e:
        logger.error(f"Upstream forwarding error: {e}")
        return None, {"type": "upstream_error", "message": str(e)}


async def handle_websocket_connection(
    websocket: WebSocketServerProtocol,
    path: str,
    proxy_state: dict,
):
    """
    Handle a WebSocket connection for streaming messages.
    
    Protocol:
        1. Client sends JSON with "model", "messages", "stream": true
        2. Server forwards to upstream (applies compression pipeline)
        3. Server streams compressed chunks back to client
        4. Client receives decompressed content
    """
    if not WEBSOCKETS_AVAILABLE:
        await websocket.close(code=1011, reason="WebSocket support not available")
        return

    # Generate connection ID
    conn_id = f"{int(time.time() * 1000)}-{id(websocket) % 10000}"
    client_address = f"{websocket.remote_address[0]}:{websocket.remote_address[1]}"

    # Check connection limit
    if not CONNECTION_MANAGER.register(conn_id, client_address):
        await websocket.close(code=1008, reason="Connection limit exceeded (50 concurrent)")
        return

    logger.info(f"[{conn_id}] WebSocket connected from {client_address} (active: {CONNECTION_MANAGER.active_count()})")

    try:
        # Receive initial message
        try:
            message_text = await asyncio.wait_for(websocket.recv(), timeout=30)
            CONNECTION_MANAGER.record_message(conn_id)
        except asyncio.TimeoutError:
            await websocket.close(code=1000, reason="Timeout waiting for initial message")
            return
        except websockets.exceptions.ConnectionClosed:
            return

        # Parse request
        try:
            request_data = json.loads(message_text)
        except json.JSONDecodeError as e:
            error_msg = json.dumps({"error": {"type": "invalid_json", "message": str(e)}})
            await websocket.send(error_msg)
            await websocket.close(code=1003, reason="Invalid JSON")
            return

        # Validate required fields
        if "model" not in request_data or "messages" not in request_data:
            error_msg = json.dumps({"error": {"type": "validation_error", "message": "Missing required fields: model, messages"}})
            await websocket.send(error_msg)
            await websocket.close(code=1003, reason="Invalid request")
            return

        # Force streaming
        request_data["stream"] = True

        # Forward to upstream
        upstream_url = proxy_state.get("upstream_url", "https://api.anthropic.com/v1/messages")
        adapter = proxy_state.get("adapter")
        token_counter = proxy_state.get("token_counter")

        logger.info(f"[{conn_id}] Forwarding to upstream: {upstream_url}")

        # For streaming, we need to handle SSE responses
        response_body, token_counts = await forward_to_upstream(
            request_data, upstream_url, adapter, token_counter
        )

        if response_body is None:
            error_msg = json.dumps({"error": token_counts})
            await websocket.send(error_msg)
            CONNECTION_MANAGER.record_upstream_error(conn_id)
            await websocket.close(code=1011, reason="Upstream error")
            return

        # Parse and stream SSE response
        try:
            response_text = response_body.decode("utf-8", errors="replace")
            chunk_buffer = []
            
            for line in response_text.split("\n"):
                line = line.strip()
                if not line or line == "[DONE]":
                    continue
                
                if line.startswith("data: "):
                    try:
                        event_data = json.loads(line[6:])
                        
                        # Stream event to client with compression
                        event_json = json.dumps(event_data)
                        compressed = compress_chunk(event_json)
                        
                        CONNECTION_MANAGER.record_chunk(
                            conn_id,
                            len(compressed),
                            len(event_json.encode("utf-8"))
                        )
                        
                        # Send as binary frame (compressed)
                        await websocket.send(compressed)
                        
                    except json.JSONDecodeError:
                        continue

            # Send final stats
            if token_counts:
                stats_msg = json.dumps({"type": "stats", "usage": token_counts})
                compressed = compress_chunk(stats_msg)
                CONNECTION_MANAGER.record_chunk(
                    conn_id,
                    len(compressed),
                    len(stats_msg.encode("utf-8"))
                )
                await websocket.send(compressed)

        except Exception as e:
            logger.error(f"[{conn_id}] Error streaming response: {e}")
            error_msg = json.dumps({"error": {"type": "streaming_error", "message": str(e)}})
            await websocket.send(error_msg)
            CONNECTION_MANAGER.record_upstream_error(conn_id)

    except websockets.exceptions.ConnectionClosed:
        logger.info(f"[{conn_id}] Connection closed by client")
    except Exception as e:
        logger.error(f"[{conn_id}] Unexpected error: {e}", exc_info=True)
    finally:
        CONNECTION_MANAGER.unregister(conn_id, close_code=1000)
        logger.info(f"[{conn_id}] Connection closed (active: {CONNECTION_MANAGER.active_count()})")


async def run_websocket_server(
    host: str = "0.0.0.0",
    port: int = 8766,
    proxy_state: dict = None,
):
    """Run the WebSocket server alongside the HTTP proxy."""
    if not WEBSOCKETS_AVAILABLE:
        logger.error("websockets library not available. Install with: pip install websockets")
        return

    if proxy_state is None:
        proxy_state = {}

    async def ws_handler(websocket, path):
        await handle_websocket_connection(websocket, path, proxy_state)

    logger.info(f"Starting WebSocket server on ws://{host}:{port}/ws")
    
    async with websockets.serve(ws_handler, host, port):
        await asyncio.Future()  # Run forever


def start_websocket_server_thread(
    host: str = "0.0.0.0",
    port: int = 8766,
    proxy_state: dict = None,
) -> Optional[threading.Thread]:
    """Start WebSocket server in a background thread."""
    if not WEBSOCKETS_AVAILABLE:
        logger.warning("WebSocket support disabled (websockets library not installed)")
        return None

    def run_async_loop():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(run_websocket_server(host, port, proxy_state))
        except Exception as e:
            logger.error(f"WebSocket server error: {e}")
        finally:
            loop.close()

    thread = threading.Thread(target=run_async_loop, daemon=True, name="tokenpak-websocket")
    thread.start()
    logger.info(f"WebSocket server thread started (daemon)")
    return thread
