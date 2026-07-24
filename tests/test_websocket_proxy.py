"""Contract tests for the modular TokenPak WebSocket proxy handler."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip(
    "websockets",
    reason="websockets is the optional transport backend exercised by this module",
)

from tokenpak.proxy import websocket as ws_proxy


class _Response:
    def __init__(self, status: int = 200, chunks: list[bytes] | None = None) -> None:
        self.status = status
        self._chunks = iter(chunks or [b"data: {}\n\n", b""])

    def read(self, _size: int = 4096) -> bytes:
        return next(self._chunks, b"")


class _InlineExecutor:
    """Execute handler blocking-call adapters inline for deterministic unit tests."""

    async def run_in_executor(self, _executor, func, *args):
        return func(*args)


def _websocket(*, path: str = "/ws", payload: dict | None = None):
    ws = SimpleNamespace()
    ws.request = SimpleNamespace(path=path, headers={})
    ws.recv = AsyncMock(
        return_value=json.dumps(payload or {"model": "claude-sonnet-4-6", "messages": []})
    )
    ws.send = AsyncMock()
    ws.close = AsyncMock()
    return ws


def _connection(response: _Response) -> MagicMock:
    conn = MagicMock()
    conn.getresponse.return_value = response
    return conn


def _run_handler(ws, compact, response: _Response | None = None) -> MagicMock:
    conn = _connection(response or _Response())
    with (
        patch.object(ws_proxy.http.client, "HTTPSConnection", return_value=conn),
        patch.object(ws_proxy.ssl, "create_default_context"),
        patch.object(ws_proxy.asyncio, "get_event_loop", return_value=_InlineExecutor()),
    ):
        asyncio.run(ws_proxy._ws_handler(ws, compact))
    return conn


@pytest.fixture(autouse=True)
def _reset_active_connections():
    ws_proxy._ws_active_connections = 0
    yield
    ws_proxy._ws_active_connections = 0


def test_ws_connect_and_stream():
    ws = _websocket()
    compact = MagicMock(return_value=(b'{"stream":true}', 10, 10, 0))
    response = _Response(chunks=[b'data: {"type":"content_block_delta"}\n\n', b""])

    _run_handler(ws, compact, response)

    ws.send.assert_awaited_once()
    assert "content_block_delta" in ws.send.await_args.args[0]
    ws.close.assert_awaited_with(1000, "Done")
    assert ws_proxy._ws_active_connections == 0


def test_ws_compression_applied_and_forwarded():
    raw_payload = {
        "model": "claude-sonnet-4-6",
        "messages": [{"role": "user", "content": "hello " * 50}],
    }
    compressed = b'{"model":"claude-sonnet-4-6","messages":[],"stream":true}'
    ws = _websocket(payload=raw_payload)
    compact = MagicMock(return_value=(compressed, 5, 100, 0))

    conn = _run_handler(ws, compact)

    compact.assert_called_once()
    submitted = json.loads(compact.call_args.args[0])
    assert submitted["stream"] is True
    assert conn.request.call_args.kwargs["body"] == compressed


def test_ws_disconnects_on_upstream_error():
    ws = _websocket()
    compact = MagicMock(return_value=(b"{}", 1, 1, 0))

    _run_handler(ws, compact, _Response(status=500, chunks=[b'{"error":"upstream"}', b""]))

    ws.close.assert_awaited_with(1011, "Upstream error 500")
    ws.send.assert_awaited_once_with('{"error":"upstream"}')


def test_ws_rejects_connection_over_limit():
    ws = _websocket()
    compact = MagicMock()
    ws_proxy._ws_active_connections = 2

    with patch.object(ws_proxy, "WS_MAX_CONNECTIONS", 2):
        asyncio.run(ws_proxy._ws_handler(ws, compact))

    ws.close.assert_awaited_once_with(1008, "Too many connections")
    compact.assert_not_called()


def test_ws_rejects_non_ws_path():
    ws = _websocket(path="/")
    compact = MagicMock()

    asyncio.run(ws_proxy._ws_handler(ws, compact))

    ws.close.assert_awaited_once_with(1008, "Not found")
    compact.assert_not_called()


def test_ws_reconnects_after_clean_close():
    compact = MagicMock(return_value=(b"{}", 1, 1, 0))
    first = _websocket()
    second = _websocket()

    _run_handler(first, compact)
    assert ws_proxy._ws_active_connections == 0
    _run_handler(second, compact)

    first.close.assert_awaited_with(1000, "Done")
    second.close.assert_awaited_with(1000, "Done")
    assert compact.call_count == 2
    assert ws_proxy._ws_active_connections == 0
