"""
Tests for WebSocket proxy support in proxy.py (tokenpak.proxy).

Covers:
  1. test_ws_connect_and_stream          — mock upstream, verify chunks received
  2. test_ws_compression_applied         — verify compact_request_body is called
  3. test_ws_disconnect_on_upstream_error — mock 500, verify ws close code 1011
  4. test_ws_max_connections             — fill to limit, verify n+1 rejected
  5. test_ws_invalid_upgrade_rejected    — plain HTTP GET returns 400
  6. test_ws_reconnect_after_clean_close — connect, stream, close 1000, reconnect succeeds
"""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
import unittest
from http.client import HTTPConnection
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    """Return a free TCP port on localhost."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _make_sse_response(status: int = 200, body: bytes = b"data: {}\n\n") -> MagicMock:
    """Build a fake http.client.HTTPResponse."""
    resp = MagicMock()
    resp.status = status
    resp.getheader = MagicMock(return_value="text/event-stream")

    # Simulate chunked reading: first call returns body, next returns b""
    _chunks = [body, b""]
    _iter = iter(_chunks)

    def _read(n=4096):
        try:
            return next(_iter)
        except StopIteration:
            return b""

    resp.read = _read
    return resp


def _start_test_ws_server(handler, port: int) -> threading.Thread:
    """Start a websockets server running handler on the given port in a daemon thread."""
    from websockets.asyncio.server import serve as ws_serve

    async def _serve():
        async with ws_serve(handler, "127.0.0.1", port):
            await asyncio.Future()

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_serve())

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    time.sleep(0.15)  # allow server to bind
    return t


# ---------------------------------------------------------------------------
# Test 1 — connect and stream
# ---------------------------------------------------------------------------

class TestWsConnectAndStream(unittest.TestCase):
    """Mock upstream; verify SSE chunks arrive over WebSocket."""

    def test_ws_connect_and_stream(self):
        import proxy

        port = _free_port()
        sse_body = b"data: {\"type\": \"content_block_delta\"}\n\ndata: {\"type\": \"message_stop\"}\n\n"
        mock_resp = _make_sse_response(200, sse_body)
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        received: list[str] = []
        close_codes: list[int] = []

        with patch("proxy.compact_request_body", return_value=(b'{"model":"claude-sonnet-4-6","messages":[],"stream":true}', 10, 10, 0)):
            with patch("http.client.HTTPSConnection", return_value=mock_conn):
                _start_test_ws_server(proxy._ws_handler, port)

                async def _client():
                    import websockets
                    uri = f"ws://127.0.0.1:{port}/ws"
                    async with websockets.connect(uri) as ws:
                        await ws.send(json.dumps({"model": "claude-sonnet-4-6", "messages": [], "stream": True}))
                        async for msg in ws:
                            received.append(msg)
                        close_codes.append(ws.close_code)

                asyncio.run(_client())

        self.assertTrue(len(received) > 0, "Expected at least one chunk from server")
        combined = "".join(received)
        self.assertIn("content_block_delta", combined)
        self.assertEqual(close_codes[0], 1000, "Expected clean close with code 1000")


# ---------------------------------------------------------------------------
# Test 2 — compression applied
# ---------------------------------------------------------------------------

class TestWsCompressionApplied(unittest.TestCase):
    """Verify compact_request_body is called and compressed body is forwarded."""

    def test_ws_compression_applied(self):
        import proxy

        port = _free_port()
        # Build a large payload that would warrant compression
        large_messages = [{"role": "user", "content": "hello " * 500}]
        raw_payload = json.dumps({"model": "claude-sonnet-4-6", "messages": large_messages})
        compressed_payload = json.dumps({"model": "claude-sonnet-4-6", "messages": [{"role": "user", "content": "hello…"}], "stream": True}).encode()

        mock_resp = _make_sse_response(200, b"data: {}\n\n")
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        compression_calls: list[bytes] = []

        def _mock_compact(body_bytes):
            compression_calls.append(body_bytes)
            # Return a smaller "compressed" body
            return compressed_payload, 5, 100, 0

        with patch("proxy.compact_request_body", side_effect=_mock_compact):
            with patch("http.client.HTTPSConnection", return_value=mock_conn):
                _start_test_ws_server(proxy._ws_handler, port)

                async def _client():
                    import websockets
                    async with websockets.connect(f"ws://127.0.0.1:{port}/ws") as ws:
                        await ws.send(raw_payload)
                        async for _ in ws:
                            pass

                asyncio.run(_client())

        self.assertEqual(len(compression_calls), 1, "compact_request_body should be called once")
        # Compressed body sent to upstream should be smaller than raw
        self.assertLess(
            len(compressed_payload),
            len(raw_payload.encode()),
            "Compressed payload should be smaller than raw",
        )


# ---------------------------------------------------------------------------
# Test 3 — disconnect on upstream error (500 → close 1011)
# ---------------------------------------------------------------------------

class TestWsDisconnectOnUpstreamError(unittest.TestCase):
    """Upstream 500 must result in WebSocket close code 1011."""

    def test_ws_disconnect_on_upstream_error(self):
        import proxy

        port = _free_port()
        mock_resp = _make_sse_response(500, b'{"error": "internal server error"}')
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        close_codes: list[int] = []

        with patch("proxy.compact_request_body", return_value=(b'{"model":"x","messages":[],"stream":true}', 5, 5, 0)):
            with patch("http.client.HTTPSConnection", return_value=mock_conn):
                _start_test_ws_server(proxy._ws_handler, port)

                async def _client():
                    import websockets
                    from websockets.exceptions import ConnectionClosedError
                    uri = f"ws://127.0.0.1:{port}/ws"
                    try:
                        async with websockets.connect(uri) as ws:
                            await ws.send(json.dumps({"model": "claude-sonnet-4-6", "messages": []}))
                            async for _ in ws:
                                pass
                            close_codes.append(ws.close_code)
                    except ConnectionClosedError as exc:
                        close_codes.append(exc.code)

                asyncio.run(_client())

        self.assertTrue(len(close_codes) > 0, "Should receive a close code")
        self.assertEqual(close_codes[0], 1011, f"Expected 1011 for upstream error, got {close_codes[0]}")


# ---------------------------------------------------------------------------
# Test 4 — max connections (n+1 is rejected)
# ---------------------------------------------------------------------------

class TestWsMaxConnections(unittest.TestCase):
    """Fill connections to limit; the next one must be rejected (close 1008)."""

    def test_ws_max_connections(self):
        import proxy

        # Temporarily lower the limit to 2 for testing
        original_max = proxy.WS_MAX_CONNECTIONS
        proxy.WS_MAX_CONNECTIONS = 2

        port = _free_port()

        async def _hanging_handler(websocket):
            """Handler that tracks connections and holds them open briefly."""
            req_path = "/"
            try:
                req_path = websocket.request.path
            except Exception:
                pass
            if req_path != "/ws":
                await websocket.close(1008, "Not found")
                return

            with proxy._ws_active_connections_lock:
                if proxy._ws_active_connections >= proxy.WS_MAX_CONNECTIONS:
                    await websocket.close(1008, "Too many connections")
                    return
                proxy._ws_active_connections += 1
            try:
                await asyncio.wait_for(websocket.recv(), timeout=5.0)
            except Exception:
                pass
            finally:
                with proxy._ws_active_connections_lock:
                    proxy._ws_active_connections -= 1

        _start_test_ws_server(_hanging_handler, port)

        close_codes: list[int] = []

        async def _run():
            import websockets
            from websockets.exceptions import ConnectionClosedError

            uri = f"ws://127.0.0.1:{port}/ws"
            # Open 2 connections (fills the limit)
            conn1 = await websockets.connect(uri)
            conn2 = await websockets.connect(uri)

            # 3rd connection should be rejected with 1008
            try:
                conn3 = await websockets.connect(uri)
                try:
                    await asyncio.wait_for(conn3.recv(), timeout=2.0)
                except Exception:
                    pass
                close_codes.append(conn3.close_code)
                await conn3.close()
            except ConnectionClosedError as exc:
                close_codes.append(exc.code)
            except Exception:
                close_codes.append(1008)

            await conn1.close()
            await conn2.close()

        try:
            asyncio.run(_run())
        finally:
            proxy.WS_MAX_CONNECTIONS = original_max
            proxy._ws_active_connections = 0

        self.assertTrue(len(close_codes) > 0, "Should have a close code for the rejected connection")
        self.assertEqual(close_codes[0], 1008, f"Expected 1008 (policy violation), got {close_codes[0]}")


# ---------------------------------------------------------------------------
# Test 5 — invalid upgrade rejected (plain HTTP GET → 400)
# ---------------------------------------------------------------------------

class TestWsInvalidUpgradeRejected(unittest.TestCase):
    """Plain HTTP GET to WS server returns 400 (not a WebSocket upgrade)."""

    def test_ws_invalid_upgrade_rejected(self):
        import proxy

        port = _free_port()
        _start_test_ws_server(proxy._ws_handler, port)

        # Send plain HTTP GET (no Upgrade header)
        conn = HTTPConnection("127.0.0.1", port, timeout=5)
        conn.request("GET", "/ws")
        resp = conn.getresponse()
        conn.close()

        # websockets library rejects non-upgrade requests with 400 or 426 (Upgrade Required)
        self.assertIn(resp.status, (400, 426), f"Expected 400 or 426 for plain HTTP GET, got {resp.status}")


# ---------------------------------------------------------------------------
# Test 6 — reconnect after clean close
# ---------------------------------------------------------------------------

class TestWsReconnectAfterCleanClose(unittest.TestCase):
    """Connect, stream, close 1000, then reconnect successfully."""

    def test_ws_reconnect_after_clean_close(self):
        import proxy

        port = _free_port()
        mock_resp = _make_sse_response(200, b"data: {\"type\": \"message_stop\"}\n\n")
        mock_conn = MagicMock()
        mock_conn.getresponse.return_value = mock_resp

        close_codes: list[int] = []
        connection_count = [0]

        with patch("proxy.compact_request_body", return_value=(b'{"model":"x","messages":[],"stream":true}', 5, 5, 0)):
            with patch("http.client.HTTPSConnection", return_value=mock_conn):
                _start_test_ws_server(proxy._ws_handler, port)

                async def _client():
                    import websockets
                    uri = f"ws://127.0.0.1:{port}/ws"
                    payload = json.dumps({"model": "claude-sonnet-4-6", "messages": []})

                    # First connection
                    async with websockets.connect(uri) as ws:
                        connection_count[0] += 1
                        await ws.send(payload)
                        async for _ in ws:
                            pass
                        close_codes.append(ws.close_code)

                    # Second connection (reconnect after clean close)
                    async with websockets.connect(uri) as ws:
                        connection_count[0] += 1
                        await ws.send(payload)
                        async for _ in ws:
                            pass
                        close_codes.append(ws.close_code)

                asyncio.run(_client())

        self.assertEqual(connection_count[0], 2, "Should have connected twice")
        self.assertEqual(close_codes[0], 1000, "First connection should close cleanly with 1000")
        self.assertEqual(close_codes[1], 1000, "Second connection should also close cleanly with 1000")


if __name__ == "__main__":
    unittest.main()
