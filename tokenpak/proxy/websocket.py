"""
tokenpak.proxy.websocket — WebSocket proxy handler for the TokenPak proxy.

Provides a /ws endpoint on WS_PORT (default 8767) that:
  - Accepts JSON requests over WebSocket
  - Applies TokenPak compression pipeline
  - Forwards to Anthropic upstream with streaming
  - Streams SSE chunks back as text frames

Extracted from tokenpak/runtime/proxy.py (TPK-RESTRUCTURE-008).
"""

import asyncio
import http.client
import json
import ssl
import threading
from typing import Dict
from urllib.parse import urlparse

from tokenpak.proxy.config import (
    WS_PORT,
    WS_MAX_CONNECTIONS,
    UPSTREAM_ROUTES,
    UPSTREAM_TIMEOUT,
)

# ---------------------------------------------------------------------------
# Active connection tracking
# ---------------------------------------------------------------------------

_ws_active_connections: int = 0
_ws_active_connections_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

async def _ws_handler(websocket, compact_request_body) -> None:
    """Handle a single WebSocket connection: receive JSON, compress, proxy to Anthropic, stream back.

    Args:
        websocket: The WebSocket connection object (websockets library).
        compact_request_body: Callable from runtime/proxy.py that applies the compression pipeline.
    """
    global _ws_active_connections

    # Check path — only /ws is supported
    req_path = "/"
    try:
        req_path = websocket.request.path
    except Exception:
        pass
    if req_path != "/ws":
        await websocket.close(1008, "Not found")
        return

    # Enforce max connections
    with _ws_active_connections_lock:
        if _ws_active_connections >= WS_MAX_CONNECTIONS:
            await websocket.close(1008, "Too many connections")
            return
        _ws_active_connections += 1

    try:
        # Receive request JSON from client
        try:
            raw = await asyncio.wait_for(websocket.recv(), timeout=30.0)
        except asyncio.TimeoutError:
            await websocket.close(1008, "Receive timeout")
            return

        try:
            req_data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            await websocket.close(1003, "Invalid JSON")
            return

        # Force streaming
        req_data["stream"] = True
        body_bytes: bytes = json.dumps(req_data).encode()

        # Apply TokenPak compression pipeline (sync — run in thread executor)
        loop = asyncio.get_event_loop()
        try:
            compressed_body, _sent, _orig, _prot = await loop.run_in_executor(
                None, compact_request_body, body_bytes
            )
        except Exception:
            compressed_body = body_bytes

        # Resolve Anthropic upstream
        upstream_base = UPSTREAM_ROUTES.get("anthropic-messages", "https://api.anthropic.com")
        parsed_up = urlparse(upstream_base)
        upstream_host = parsed_up.netloc or "api.anthropic.com"
        upstream_path = "/v1/messages"

        # Forward headers: pass through auth headers from WS upgrade request
        fwd_headers: Dict[str, str] = {
            "Content-Type": "application/json",
            "Content-Length": str(len(compressed_body)),
            "Host": upstream_host,
            "anthropic-version": "2023-06-01",
        }
        try:
            for hname, hval in websocket.request.headers.items():
                hl = hname.lower()
                if hl in ("x-api-key", "authorization", "anthropic-version", "anthropic-beta"):
                    fwd_headers[hl] = hval
        except Exception:
            pass

        # Connect to upstream and stream SSE back (sync — run in executor)
        def _connect_upstream():
            ctx = ssl.create_default_context()
            conn = http.client.HTTPSConnection(upstream_host, timeout=UPSTREAM_TIMEOUT, context=ctx)
            conn.request("POST", upstream_path, body=compressed_body, headers=fwd_headers)
            return conn, conn.getresponse()

        try:
            conn, resp = await loop.run_in_executor(None, _connect_upstream)
        except Exception as exc:
            await websocket.close(1011, f"Upstream connection failed: {str(exc)[:100]}")
            return

        # Non-2xx: close with error code 1011
        if resp.status >= 400:
            try:
                err_body = await loop.run_in_executor(None, resp.read)
                await websocket.send(err_body.decode("utf-8", errors="replace"))
            except Exception:
                pass
            await websocket.close(1011, f"Upstream error {resp.status}")
            return

        # Stream SSE chunks back as text frames
        while True:
            chunk = await loop.run_in_executor(None, resp.read, 4096)
            if not chunk:
                break
            try:
                await websocket.send(chunk.decode("utf-8", errors="replace"))
            except Exception:
                break  # client disconnected

        await websocket.close(1000, "Done")

    except Exception as exc:
        try:
            await websocket.close(1011, str(exc)[:123])
        except Exception:
            pass
    finally:
        with _ws_active_connections_lock:
            _ws_active_connections -= 1


# ---------------------------------------------------------------------------
# Server startup
# ---------------------------------------------------------------------------

def start_ws_server(compact_request_body) -> "threading.Thread | None":
    """Start the asyncio WebSocket server in a daemon thread on WS_PORT.

    Args:
        compact_request_body: The compression callable from runtime/proxy.py.

    Returns:
        The daemon thread running the WS server, or None if websockets not installed.
    """
    try:
        from websockets.asyncio.server import serve as ws_serve
    except ImportError:
        print(
            "[ws] websockets library not installed — WebSocket server disabled. Run: pip install websockets>=12.0"
        )
        return None  # type: ignore[return-value]

    async def _serve() -> None:
        async def _handler(ws):
            await _ws_handler(ws, compact_request_body)

        try:
            async with ws_serve(_handler, "0.0.0.0", WS_PORT, reuse_address=True):
                print(f"[ws] TokenPak WebSocket server ready — port={WS_PORT}")
                await asyncio.Future()  # run until cancelled
        except Exception as exc:
            print(f"[ws] WebSocket server error: {exc}")

    def _run() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_serve())
        except Exception:
            pass

    t = threading.Thread(target=_run, daemon=True, name="tokenpak-ws-server")
    t.start()
    return t
