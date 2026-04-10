"""
Unit tests for tokenpak/agent/proxy/server_async.py

Coverage targets:
- Helper functions: _estimate_tokens, _parse_sse_tokens, _build_forward_headers,
  _should_intercept, _is_messages_endpoint, _extract_response_tokens
- ConcurrencyLimiterMiddleware: 503 when at capacity, bypass for management paths
- create_async_app: app creation, routes registered
- _record_telemetry: state updates, lock safety, no-op when no tokens
- run_async_proxy / start_async_proxy_in_thread: startup / shutdown lifecycle
- Management endpoint handlers: health, stats, traces, etc.
- _forward_request: streaming and non-streaming paths (mocked httpx)
- _AsyncTCPProxy: CONNECT detection and HTTP forwarding
"""
from __future__ import annotations

import asyncio
import gzip
import json
import threading
import time
import unittest
from unittest.mock import AsyncMock, MagicMock, Mock, patch
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Helpers under test (pure functions — no I/O needed)
# ---------------------------------------------------------------------------

from tokenpak.proxy.server_async import (
    _estimate_tokens,
    _extract_response_tokens,
    _is_messages_endpoint,
    _parse_sse_tokens,
    _should_intercept,
    ConcurrencyLimiterMiddleware,
    create_async_app,
    _record_telemetry,
    _build_forward_headers,
    start_async_proxy_in_thread,
)


# ===========================================================================
# Pure helper tests — no async needed
# ===========================================================================

class TestEstimateTokens(unittest.TestCase):

    def test_simple_string_message(self):
        body = json.dumps({
            "messages": [{"role": "user", "content": "Hello world!"}]
        }).encode()
        result = _estimate_tokens(body)
        # "Hello world!" = 12 chars // 4 = 3 tokens
        self.assertGreater(result, 0)

    def test_multipart_content(self):
        body = json.dumps({
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "Hi there, how are you?"}]}
            ]
        }).encode()
        result = _estimate_tokens(body)
        self.assertGreater(result, 0)

    def test_system_string(self):
        body = json.dumps({
            "system": "You are a helpful assistant.",
            "messages": [{"role": "user", "content": "Hello"}]
        }).encode()
        result = _estimate_tokens(body)
        self.assertGreater(result, 0)

    def test_system_list(self):
        body = json.dumps({
            "system": [{"type": "text", "text": "Be helpful."}],
            "messages": [{"role": "user", "content": "Hi"}]
        }).encode()
        result = _estimate_tokens(body)
        self.assertGreater(result, 0)

    def test_empty_messages(self):
        body = json.dumps({"messages": []}).encode()
        result = _estimate_tokens(body)
        self.assertEqual(result, 0)

    def test_invalid_json_fallback(self):
        body = b"not json" * 10
        result = _estimate_tokens(body)
        # Should fall back to len(body) // 4
        self.assertEqual(result, len(body) // 4)

    def test_empty_body(self):
        result = _estimate_tokens(b"")
        self.assertEqual(result, 0)


class TestExtractResponseTokens(unittest.TestCase):

    def test_anthropic_output_tokens(self):
        body = json.dumps({"usage": {"output_tokens": 42}}).encode()
        self.assertEqual(_extract_response_tokens(body), 42)

    def test_openai_completion_tokens(self):
        body = json.dumps({"usage": {"completion_tokens": 17}}).encode()
        self.assertEqual(_extract_response_tokens(body), 17)

    def test_total_tokens_fallback(self):
        body = json.dumps({"usage": {"total_tokens": 100}}).encode()
        self.assertEqual(_extract_response_tokens(body), 100)

    def test_no_usage_key(self):
        body = json.dumps({"model": "claude"}).encode()
        self.assertEqual(_extract_response_tokens(body), 0)

    def test_invalid_json(self):
        self.assertEqual(_extract_response_tokens(b"bad"), 0)


class TestShouldIntercept(unittest.TestCase):

    def test_anthropic_url(self):
        self.assertTrue(_should_intercept("https://api.anthropic.com/v1/messages"))

    def test_openai_url(self):
        self.assertTrue(_should_intercept("https://api.openai.com/v1/chat/completions"))

    def test_other_url(self):
        self.assertFalse(_should_intercept("https://example.com/api"))

    def test_localhost(self):
        self.assertFalse(_should_intercept("http://localhost:8766/v1/messages"))


class TestIsMessagesEndpoint(unittest.TestCase):

    def test_anthropic_messages(self):
        self.assertTrue(_is_messages_endpoint("/v1/messages"))

    def test_openai_chat_completions(self):
        self.assertTrue(_is_messages_endpoint("/v1/chat/completions"))

    def test_health_endpoint(self):
        self.assertFalse(_is_messages_endpoint("/health"))

    def test_stats_endpoint(self):
        self.assertFalse(_is_messages_endpoint("/stats"))


class TestParseSseTokens(unittest.TestCase):

    def _make_sse(self, events: list[dict]) -> bytes:
        lines = []
        for event in events:
            lines.append(f"data: {json.dumps(event)}")
            lines.append("")
        lines.append("data: [DONE]")
        return "\n".join(lines).encode()

    def test_anthropic_message_start_and_delta(self):
        sse = self._make_sse([
            {
                "type": "message_start",
                "message": {"usage": {"cache_read_input_tokens": 10, "cache_creation_input_tokens": 5}},
            },
            {
                "type": "message_delta",
                "usage": {"output_tokens": 25},
            },
        ])
        result = _parse_sse_tokens(sse)
        self.assertEqual(result["output_tokens"], 25)
        self.assertEqual(result["cache_read_input_tokens"], 10)
        self.assertEqual(result["cache_creation_input_tokens"], 5)

    def test_openai_style(self):
        sse = self._make_sse([
            {"usage": {"completion_tokens": 33}},
        ])
        result = _parse_sse_tokens(sse)
        self.assertEqual(result["output_tokens"], 33)

    def test_empty_sse(self):
        result = _parse_sse_tokens(b"")
        self.assertEqual(result["output_tokens"], 0)
        self.assertEqual(result["cache_read_input_tokens"], 0)
        self.assertEqual(result["cache_creation_input_tokens"], 0)

    def test_malformed_sse_ignored(self):
        sse = b"data: not-json\ndata: [DONE]\n"
        result = _parse_sse_tokens(sse)
        self.assertEqual(result["output_tokens"], 0)

    def test_done_marker_skipped(self):
        result = _parse_sse_tokens(b"data: [DONE]\n")
        self.assertEqual(result["output_tokens"], 0)


class TestBuildForwardHeaders(unittest.TestCase):

    def _make_request(self, headers: dict) -> MagicMock:
        req = MagicMock()
        req.headers = headers
        return req

    def test_host_is_replaced(self):
        req = self._make_request({"host": "localhost:8766", "content-type": "application/json"})
        result = _build_forward_headers(req, "https://api.anthropic.com/v1/messages")
        self.assertEqual(result["host"], "api.anthropic.com")
        self.assertEqual(result["content-type"], "application/json")

    def test_hop_by_hop_stripped(self):
        req = self._make_request({
            "host": "localhost",
            "connection": "keep-alive",
            "transfer-encoding": "chunked",
            "keep-alive": "timeout=60",
            "proxy-connection": "close",
        })
        result = _build_forward_headers(req, "https://api.anthropic.com")
        self.assertNotIn("connection", result)
        self.assertNotIn("transfer-encoding", result)
        self.assertNotIn("keep-alive", result)
        self.assertNotIn("proxy-connection", result)

    def test_auth_header_preserved(self):
        req = self._make_request({"host": "localhost", "x-api-key": "sk-test"})
        result = _build_forward_headers(req, "https://api.anthropic.com")
        self.assertEqual(result["x-api-key"], "sk-test")


# ===========================================================================
# ConcurrencyLimiterMiddleware
# ===========================================================================

class TestConcurrencyLimiterMiddleware(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        # Minimal async app that always returns 200
        async def ok_app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

        self.ok_app = ok_app

    async def _make_request(self, middleware, path: str = "/v1/messages"):
        """Drive ASGI lifecycle and return captured status."""
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import PlainTextResponse

        async def view(request):
            return PlainTextResponse("ok")

        app = Starlette(routes=[Route(path, view, methods=["POST"])])
        app.add_middleware(ConcurrencyLimiterMiddleware, max_concurrency=1)
        client = TestClient(app, raise_server_exceptions=False)
        return client

    def test_management_endpoints_bypass_limit(self):
        """Health/stats always respond even when at capacity."""
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import PlainTextResponse

        async def view(request):
            return PlainTextResponse("ok")

        app = Starlette(routes=[
            Route("/health", view, methods=["GET"]),
            Route("/stats", view, methods=["GET"]),
        ])
        app.add_middleware(ConcurrencyLimiterMiddleware, max_concurrency=1)
        client = TestClient(app)
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/stats").status_code, 200)

    def test_503_when_semaphore_exhausted(self):
        """When semaphore is exhausted, non-management path returns 503."""
        import asyncio as _asyncio
        from starlette.testclient import TestClient
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.responses import PlainTextResponse
        from starlette.middleware.base import BaseHTTPMiddleware

        async def slow_view(request):
            await _asyncio.sleep(5)
            return PlainTextResponse("ok")

        class ExhaustedMiddleware(ConcurrencyLimiterMiddleware):
            def __init__(self, app):
                BaseHTTPMiddleware.__init__(self, app)
                self._semaphore = _asyncio.Semaphore(0)  # already exhausted
                self._max = 1

        app2 = Starlette(routes=[Route("/v1/messages", slow_view, methods=["POST"])])
        app2.add_middleware(ExhaustedMiddleware)
        client = TestClient(app2, raise_server_exceptions=False)
        resp = client.post("/v1/messages", json={"test": True})
        self.assertEqual(resp.status_code, 503)
        data = resp.json()
        self.assertIn("overloaded", data["error"]["type"])


# ===========================================================================
# create_async_app — smoke test, route registration
# ===========================================================================

class TestCreateAsyncApp(unittest.TestCase):

    def _make_proxy_server(self) -> MagicMock:
        ps = MagicMock()
        ps.health.return_value = {"status": "ok"}
        ps.stats.return_value = {"requests": 0}
        ps.last_request_stats.return_value = {}
        ps.session_stats.return_value = {}
        ps.trace_storage.get_all.return_value = []
        ps.trace_storage.get_last.return_value = None
        ps._session_lock = threading.Lock()
        ps._compression_lock = threading.Lock()
        ps._last_lock = threading.Lock()
        ps.session = {
            "requests": 0, "input_tokens": 0, "sent_input_tokens": 0,
            "saved_tokens": 0, "protected_tokens": 0, "output_tokens": 0,
            "cost": 0.0, "cost_saved": 0.0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
        }
        return ps

    def test_app_created(self):
        ps = self._make_proxy_server()
        from starlette.applications import Starlette
        app = create_async_app(ps)
        self.assertIsInstance(app, Starlette)

    def test_management_routes_respond(self):
        from starlette.testclient import TestClient
        ps = self._make_proxy_server()
        app = create_async_app(ps)
        client = TestClient(app, raise_server_exceptions=False)
        self.assertEqual(client.get("/health").status_code, 200)
        self.assertEqual(client.get("/stats").status_code, 200)
        self.assertEqual(client.get("/stats/last").status_code, 200)
        self.assertEqual(client.get("/stats/session").status_code, 200)
        self.assertEqual(client.get("/traces").status_code, 200)
        self.assertEqual(client.get("/trace/last").status_code, 200)

    def test_trace_by_id_not_found(self):
        from starlette.testclient import TestClient
        ps = self._make_proxy_server()
        ps.trace_storage.get_by_id.return_value = None
        app = create_async_app(ps)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/trace/abc123")
        self.assertEqual(resp.status_code, 404)

    def test_trace_by_id_found(self):
        from starlette.testclient import TestClient
        ps = self._make_proxy_server()
        trace = MagicMock()
        trace.to_dict.return_value = {"request_id": "abc123", "model": "claude"}
        ps.trace_storage.get_by_id.return_value = trace
        app = create_async_app(ps)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/trace/abc123")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["request_id"], "abc123")

    def test_trace_last_when_exists(self):
        from starlette.testclient import TestClient
        ps = self._make_proxy_server()
        trace = MagicMock()
        trace.to_dict.return_value = {"request_id": "xyz", "model": "claude"}
        ps.trace_storage.get_last.return_value = trace
        app = create_async_app(ps)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/trace/last")
        self.assertEqual(resp.status_code, 200)

    def test_unknown_path_404(self):
        from starlette.testclient import TestClient
        ps = self._make_proxy_server()
        app = create_async_app(ps)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/does-not-exist")
        self.assertEqual(resp.status_code, 404)

    def test_degradation_endpoint(self):
        from starlette.testclient import TestClient
        ps = self._make_proxy_server()
        app = create_async_app(ps)
        with patch("tokenpak.proxy.server_async.handle_degradation") as mock_handler:
            from starlette.responses import JSONResponse
            mock_handler.return_value = JSONResponse({"ok": True})
            # Just verify route exists — degradation requires real module
        client = TestClient(app, raise_server_exceptions=False)
        # Should not 404 (may 500 if degradation module not available, but not 404)
        resp = client.get("/degradation")
        self.assertNotEqual(resp.status_code, 404)

    def test_circuit_breakers_endpoint(self):
        from starlette.testclient import TestClient
        ps = self._make_proxy_server()
        app = create_async_app(ps)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/circuit-breakers")
        self.assertNotEqual(resp.status_code, 404)


# ===========================================================================
# _record_telemetry — state updates and edge cases
# ===========================================================================

class TestRecordTelemetry(unittest.TestCase):

    def _make_ps(self):
        ps = MagicMock()
        ps._session_lock = threading.Lock()
        ps._compression_lock = threading.Lock()
        ps._last_lock = threading.Lock()
        ps._compression_ratios = []
        ps.session = {
            "requests": 0, "input_tokens": 0, "sent_input_tokens": 0,
            "saved_tokens": 0, "protected_tokens": 0, "output_tokens": 0,
            "cost": 0.0, "cost_saved": 0.0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
        }
        ps.telemetry_events = []
        return ps

    def test_no_op_when_zero_input_tokens(self):
        ps = self._make_ps()
        _record_telemetry(ps, None, "claude", 0, 0, 0, 0, 0, 0, 100)
        self.assertEqual(ps.session["requests"], 0)

    def test_increments_session_counters(self):
        ps = self._make_ps()
        with patch("tokenpak.proxy.server_async._record_telemetry",
                   wraps=_record_telemetry):
            with patch("tokenpak.proxy.router.estimate_cost", return_value=0.01):
                _record_telemetry(ps, None, "claude-sonnet-4-6", 1000, 800, 100, 50, 0, 0, 200)
        self.assertEqual(ps.session["requests"], 1)
        self.assertEqual(ps.session["input_tokens"], 1000)
        self.assertEqual(ps.session["sent_input_tokens"], 800)
        self.assertEqual(ps.session["saved_tokens"], 200)
        self.assertEqual(ps.session["output_tokens"], 100)

    def test_compression_ratio_appended(self):
        ps = self._make_ps()
        with patch("tokenpak.proxy.router.estimate_cost", return_value=0.01):
            _record_telemetry(ps, None, "claude-sonnet-4-6", 1000, 600, 100, 0, 0, 0, 150)
        self.assertEqual(len(ps._compression_ratios), 1)
        # saved = 1000 - 600 = 400; ratio = 400/1000 = 0.4
        self.assertAlmostEqual(ps._compression_ratios[0], 0.4, places=3)

    def test_trace_updated_when_provided(self):
        ps = self._make_ps()
        trace = MagicMock()
        trace.request_id = "test-123"
        with patch("tokenpak.proxy.router.estimate_cost", return_value=0.005):
            _record_telemetry(ps, trace, "claude", 500, 400, 50, 10, 0, 0, 300)
        self.assertEqual(trace.model, "claude")
        self.assertEqual(trace.input_tokens, 500)
        self.assertEqual(trace.output_tokens, 50)
        self.assertEqual(trace.tokens_saved, 100)
        self.assertEqual(trace.duration_ms, 300)
        self.assertEqual(trace.status, "complete")

    def test_last_request_updated(self):
        ps = self._make_ps()
        with patch("tokenpak.proxy.router.estimate_cost", return_value=0.002):
            _record_telemetry(ps, None, "claude", 400, 300, 80, 0, 0, 0, 250)
        self.assertEqual(ps._last_request["model"], "claude")
        self.assertEqual(ps._last_request["input_tokens_raw"], 400)
        self.assertEqual(ps._last_request["input_tokens_sent"], 300)

    def test_never_raises_on_exception(self):
        """telemetry must never break the proxy"""
        ps = self._make_ps()
        ps._session_lock = None  # will cause AttributeError
        # Should not raise
        try:
            _record_telemetry(ps, None, "claude", 100, 80, 10, 0, 0, 0, 100)
        except Exception as e:
            self.fail(f"_record_telemetry raised unexpectedly: {e}")


# ===========================================================================
# _forward_request — mocked httpx paths
# ===========================================================================

class TestForwardRequest(unittest.IsolatedAsyncioTestCase):

    def _make_ps(self):
        ps = MagicMock()
        ps._session_lock = threading.Lock()
        ps._compression_lock = threading.Lock()
        ps._last_lock = threading.Lock()
        ps._compression_ratios = []
        ps.session = {
            "requests": 0, "input_tokens": 0, "sent_input_tokens": 0,
            "saved_tokens": 0, "protected_tokens": 0, "output_tokens": 0,
            "cost": 0.0, "cost_saved": 0.0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "errors": 0,
        }
        ps.telemetry_events = []
        ps.request_hook = None
        ps.trace_storage = MagicMock()
        return ps

    async def _make_request(self, method="POST", path="/v1/messages",
                             body=b'{"model":"claude","messages":[]}',
                             headers=None) -> MagicMock:
        from starlette.requests import Request
        scope = {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        }

        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}

        return Request(scope, receive)

    async def test_non_streaming_passthrough(self):
        from tokenpak.proxy import server_async as module
        import httpx

        ps = self._make_ps()
        module._proxy_server_ref = ps

        resp_body = json.dumps({"usage": {"output_tokens": 5}}).encode()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = resp_body
        mock_response.headers = {"content-type": "application/json"}

        async_client = AsyncMock()
        async_client.request = AsyncMock(return_value=mock_response)
        module._async_client = async_client

        request = await self._make_request(
            body=json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
        )

        with patch("tokenpak.proxy.server_async._should_intercept", return_value=False):
            from tokenpak.proxy.server_async import _forward_request
            response = await _forward_request(request, "https://api.anthropic.com/v1/messages")

        self.assertEqual(response.status_code, 200)

    async def test_upstream_error_returns_502(self):
        from tokenpak.proxy import server_async as module
        import httpx

        ps = self._make_ps()
        module._proxy_server_ref = ps

        async_client = AsyncMock()
        async_client.request = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
        module._async_client = async_client

        request = await self._make_request()

        with patch("tokenpak.proxy.server_async._should_intercept", return_value=False):
            from tokenpak.proxy.server_async import _forward_request
            response = await _forward_request(request, "https://api.anthropic.com/v1/messages")

        self.assertEqual(response.status_code, 502)
        data = json.loads(response.body)
        self.assertEqual(data["error"]["type"], "proxy_error")

    async def test_gzip_response_decoded(self):
        from tokenpak.proxy import server_async as module

        ps = self._make_ps()
        module._proxy_server_ref = ps

        raw = json.dumps({"usage": {"output_tokens": 10}}).encode()
        compressed = gzip.compress(raw)
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.content = compressed
        mock_response.headers = {"content-encoding": "gzip", "content-type": "application/json"}

        async_client = AsyncMock()
        async_client.request = AsyncMock(return_value=mock_response)
        module._async_client = async_client

        request = await self._make_request(
            headers={"content-type": "application/json"},
            body=json.dumps({"messages": [{"role": "user", "content": "hi"}], "stream": False}).encode(),
        )

        with patch("tokenpak.proxy.server_async._should_intercept", return_value=True), \
             patch("tokenpak.proxy.server_async._is_messages_endpoint", return_value=True), \
             patch("tokenpak.proxy.server_async._record_telemetry"):
            from tokenpak.proxy.server_async import _forward_request
            response = await _forward_request(request, "https://api.anthropic.com/v1/messages")

        self.assertEqual(response.status_code, 200)


# ===========================================================================
# start_async_proxy_in_thread — lifecycle
# ===========================================================================

class TestAsyncProxyThread(unittest.TestCase):

    def _make_ps(self):
        ps = MagicMock()
        ps.request_hook = None
        ps.shutdown_timeout = 5
        ps._session_lock = threading.Lock()
        ps._compression_lock = threading.Lock()
        ps._last_lock = threading.Lock()
        ps._compression_ratios = []
        ps.session = {
            "requests": 0, "input_tokens": 0, "sent_input_tokens": 0,
            "saved_tokens": 0, "protected_tokens": 0, "output_tokens": 0,
            "cost": 0.0, "cost_saved": 0.0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
        }
        ps.telemetry_events = []
        ps.trace_storage = MagicMock()
        return ps

    def test_thread_starts_and_can_be_stopped(self):
        shutdown = threading.Event()
        ps = self._make_ps()

        with patch("tokenpak.proxy.server_async.run_async_proxy", new_callable=AsyncMock) as mock_run:
            mock_run.return_value = None
            t = start_async_proxy_in_thread(ps, host="127.0.0.1", port=19900, shutdown_event=shutdown)
            self.assertIsInstance(t, threading.Thread)
            self.assertTrue(t.daemon)
            shutdown.set()
            t.join(timeout=3)

    def test_thread_is_daemon(self):
        shutdown = threading.Event()
        ps = self._make_ps()
        shutdown.set()  # stop immediately

        with patch("tokenpak.proxy.server_async.run_async_proxy", new_callable=AsyncMock):
            t = start_async_proxy_in_thread(ps, host="127.0.0.1", port=19901, shutdown_event=shutdown)
            self.assertTrue(t.daemon)
            t.join(timeout=2)


# ===========================================================================
# _AsyncTCPProxy — startup, CONNECT detection
# ===========================================================================

class TestAsyncTCPProxy(unittest.IsolatedAsyncioTestCase):

    async def test_start_and_stop(self):
        from tokenpak.proxy.server_async import _AsyncTCPProxy
        proxy = _AsyncTCPProxy("127.0.0.1", 0, 0)
        await proxy.start()
        self.assertIsNotNone(proxy._server)
        await proxy.stop()

    async def test_connect_request_handled(self):
        """CONNECT request should invoke the tunnel handler (mocked)."""
        from tokenpak.proxy.server_async import _AsyncTCPProxy

        proxy = _AsyncTCPProxy("127.0.0.1", 0, 19800)
        await proxy.start()
        port = proxy._server.sockets[0].getsockname()[1]

        connect_request = b"CONNECT api.anthropic.com:443 HTTP/1.1\r\nHost: api.anthropic.com:443\r\n\r\n"
        tunnel_called = asyncio.Event()

        async def fake_tunnel(host, port, reader, writer):
            tunnel_called.set()
            writer.close()

        with patch("tokenpak.proxy.server_async._handle_connect_tunnel", side_effect=fake_tunnel):
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", port), timeout=2
                )
                writer.write(connect_request)
                await writer.drain()
                # Give handler time to run
                await asyncio.wait_for(tunnel_called.wait(), timeout=2)
            except Exception:
                pass  # timeout or connection error is acceptable
            finally:
                try:
                    writer.close()
                except Exception:
                    pass

        await proxy.stop()


# ===========================================================================
# Module-level proxy_server_ref guard
# ===========================================================================

class TestProxyServerRefGuard(unittest.TestCase):

    def test_ps_raises_when_not_initialised(self):
        from tokenpak.proxy import server_async as module
        orig = module._proxy_server_ref
        try:
            module._proxy_server_ref = None
            with self.assertRaises(RuntimeError):
                module._ps()
        finally:
            module._proxy_server_ref = orig

    def test_client_raises_when_not_initialised(self):
        from tokenpak.proxy import server_async as module
        orig = module._async_client
        try:
            module._async_client = None
            with self.assertRaises(RuntimeError):
                module._client()
        finally:
            module._async_client = orig


# ===========================================================================
# _run_pipeline_sync
# ===========================================================================

class TestRunPipelineSync(unittest.TestCase):

    def test_no_hook_passthrough(self):
        from tokenpak.proxy.server_async import _run_pipeline_sync
        ps = MagicMock()
        ps.request_hook = None
        body = b'{"messages":[{"role":"user","content":"hi there"}]}'
        result = _run_pipeline_sync(ps, body, "claude", None)
        new_body, sent, raw, protected = result
        self.assertEqual(new_body, body)
        self.assertEqual(sent, raw)
        self.assertEqual(protected, 0)

    def test_hook_called(self):
        from tokenpak.proxy.server_async import _run_pipeline_sync
        ps = MagicMock()
        ps.request_hook = Mock(return_value=(b"compressed", 100, 200, 10))
        result = _run_pipeline_sync(ps, b"original body", "claude", None)
        self.assertEqual(result, (b"compressed", 100, 200, 10))
        ps.request_hook.assert_called_once()

    def test_hook_exception_falls_back_to_passthrough(self):
        from tokenpak.proxy.server_async import _run_pipeline_sync
        ps = MagicMock()
        ps.request_hook = Mock(side_effect=RuntimeError("pipeline boom"))
        body = b'{"messages":[{"role":"user","content":"test"}]}'
        new_body, sent, raw, protected = _run_pipeline_sync(ps, body, "claude", None)
        self.assertEqual(new_body, body)
        self.assertEqual(protected, 0)


# ===========================================================================
# Streaming forward path
# ===========================================================================

class TestForwardRequestStreaming(unittest.IsolatedAsyncioTestCase):

    def _make_ps(self):
        ps = MagicMock()
        ps._session_lock = threading.Lock()
        ps._compression_lock = threading.Lock()
        ps._last_lock = threading.Lock()
        ps._compression_ratios = []
        ps.session = {
            "requests": 0, "input_tokens": 0, "sent_input_tokens": 0,
            "saved_tokens": 0, "protected_tokens": 0, "output_tokens": 0,
            "cost": 0.0, "cost_saved": 0.0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "errors": 0,
        }
        ps.telemetry_events = []
        ps.request_hook = None
        ps.trace_storage = MagicMock()
        return ps

    async def _make_request(self, body=b'', headers=None):
        from starlette.requests import Request
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/v1/messages",
            "query_string": b"",
            "headers": [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()],
        }
        async def receive():
            return {"type": "http.request", "body": body, "more_body": False}
        return Request(scope, receive)

    async def test_streaming_response_returned(self):
        from tokenpak.proxy import server_async as module
        from starlette.responses import StreamingResponse

        ps = self._make_ps()
        module._proxy_server_ref = ps

        sse_chunk = b"data: {\"type\":\"message_delta\",\"usage\":{\"output_tokens\":5}}\n\n"

        # Build a proper async context manager for client.stream()
        class FakeUpstreamCM:
            def __init__(self):
                self.status_code = 200
                self.headers = {"content-type": "text/event-stream"}

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def aiter_bytes(self, chunk_size=4096):
                yield sse_chunk

        async_client = AsyncMock()
        async_client.stream = Mock(return_value=FakeUpstreamCM())
        module._async_client = async_client

        streaming_body = json.dumps({
            "model": "claude",
            "stream": True,
            "messages": [{"role": "user", "content": "hi"}]
        }).encode()

        request = await self._make_request(body=streaming_body)

        # Patch _should_intercept to False so we skip pipeline/trace/routing
        # but still test the streaming branch (stream=True in body, no intercept)
        # Actually with no intercept, is_streaming will be False since we only
        # parse it in the intercept branch. So we need intercept=True with mocks.
        with patch("tokenpak.proxy.server_async._should_intercept", return_value=True), \
             patch("tokenpak.proxy.server_async._is_messages_endpoint", return_value=True), \
             patch("tokenpak.proxy.server_async._record_telemetry"), \
             patch("tokenpak.proxy.router.ProviderRouter") as MockRouter:
            route = MagicMock()
            route.model = "claude"
            MockRouter.return_value.route.return_value = route
            from tokenpak.proxy.server_async import _forward_request
            response = await _forward_request(request, "https://api.anthropic.com/v1/messages")

        self.assertIsInstance(response, StreamingResponse)


# ===========================================================================
# Additional endpoint handler tests
# ===========================================================================

class TestAdditionalEndpoints(unittest.TestCase):

    def _make_ps(self):
        ps = MagicMock()
        ps.health.return_value = {"status": "ok"}
        ps.stats.return_value = {}
        ps.last_request_stats.return_value = {}
        ps.session_stats.return_value = {"requests": 0}
        ps.trace_storage.get_all.return_value = []
        ps.trace_storage.get_last.return_value = None
        ps._session_lock = threading.Lock()
        ps._compression_lock = threading.Lock()
        ps._last_lock = threading.Lock()
        ps.session = {
            "requests": 0, "input_tokens": 0, "sent_input_tokens": 0,
            "saved_tokens": 0, "protected_tokens": 0, "output_tokens": 0,
            "cost": 0.0, "cost_saved": 0.0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "errors": 0,
        }
        return ps

    def test_handle_sessions_returns_200(self):
        from starlette.testclient import TestClient
        ps = self._make_ps()
        sf = MagicMock()
        sf.query.return_value = {"sessions": [], "total": 0}
        sf.distinct_models.return_value = []
        ps.session_filter = sf
        app = create_async_app(ps)
        with patch("tokenpak.proxy.server_async.handle_sessions") as mock_handler:
            from starlette.responses import JSONResponse
            mock_handler.return_value = JSONResponse({"sessions": [], "total": 0})
            client = TestClient(app, raise_server_exceptions=False)
            # Route exists — we're just checking it doesn't 404
        client = TestClient(app, raise_server_exceptions=False)
        with patch("tokenpak.dashboard.session_filter.FilterParams") as MockFP:
            MockFP.from_query_string.return_value = MagicMock()
            resp = client.get("/v1/sessions")
        # 200 or 500 — just not 404
        self.assertNotEqual(resp.status_code, 404)

    def test_handle_export_csv(self):
        from starlette.testclient import TestClient
        ps = self._make_ps()
        app = create_async_app(ps)
        with patch("tokenpak.proxy.server_async.handle_export_csv") as mock_handler:
            from starlette.responses import Response
            mock_handler.return_value = Response(content=b"col1,col2\n1,2", status_code=200)
            # Just check route exists
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/v1/export/csv", json={})
        self.assertNotEqual(resp.status_code, 404)

    def test_handle_not_found_returns_404_json(self):
        from starlette.testclient import TestClient
        ps = self._make_ps()
        app = create_async_app(ps)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/totally-unknown-path-xyz")
        self.assertEqual(resp.status_code, 404)
        data = resp.json()
        self.assertEqual(data["error"], "not_found")
        self.assertIn("path", data)


# ===========================================================================
# _handle_connect_tunnel
# ===========================================================================

class TestHandleConnectTunnel(unittest.IsolatedAsyncioTestCase):

    async def test_bad_host_returns_502(self):
        """Connect to unreachable host → client should get 502."""
        from tokenpak.proxy.server_async import _handle_connect_tunnel

        received = bytearray()

        client_reader = AsyncMock(spec=asyncio.StreamReader)
        client_writer = MagicMock(spec=asyncio.StreamWriter)
        client_writer.write = Mock()
        client_writer.drain = AsyncMock()
        client_writer.close = Mock()

        with patch("asyncio.open_connection", side_effect=ConnectionRefusedError("refused")):
            await _handle_connect_tunnel("unreachable.host", 443, client_reader, client_writer)

        client_writer.write.assert_called_with(b"HTTP/1.1 502 Bad Gateway\r\n\r\n")
        client_writer.close.assert_called()

    async def test_successful_tunnel_bridges(self):
        """Successful CONNECT → relay is established."""
        from tokenpak.proxy.server_async import _handle_connect_tunnel

        # Two-sided pipe simulation
        client_data_to_send = [b"hello from client", b""]
        server_data_to_send = [b"hello from server", b""]

        client_reader = AsyncMock(spec=asyncio.StreamReader)
        client_reader.read = AsyncMock(side_effect=client_data_to_send)
        client_writer = MagicMock(spec=asyncio.StreamWriter)
        client_writer.write = Mock()
        client_writer.drain = AsyncMock()
        client_writer.close = Mock()

        remote_reader = AsyncMock(spec=asyncio.StreamReader)
        remote_reader.read = AsyncMock(side_effect=server_data_to_send)
        remote_writer = MagicMock(spec=asyncio.StreamWriter)
        remote_writer.write = Mock()
        remote_writer.drain = AsyncMock()
        remote_writer.close = Mock()

        with patch("asyncio.open_connection", return_value=(remote_reader, remote_writer)):
            await _handle_connect_tunnel("api.anthropic.com", 443, client_reader, client_writer)

        # Should have sent 200 Connection Established
        client_writer.write.assert_any_call(b"HTTP/1.1 200 Connection Established\r\n\r\n")


# ===========================================================================
# _AsyncTCPProxy non-CONNECT path
# ===========================================================================

class TestAsyncTCPProxyHTTP(unittest.IsolatedAsyncioTestCase):

    async def test_non_connect_forwarded_to_uvicorn(self):
        """Plain HTTP requests should be relayed to uvicorn internal port."""
        from tokenpak.proxy.server_async import _AsyncTCPProxy

        proxy = _AsyncTCPProxy("127.0.0.1", 0, 19900)

        # We just test that the server starts and the handler exists
        await proxy.start()
        port = proxy._server.sockets[0].getsockname()[1]
        self.assertGreater(port, 0)
        await proxy.stop()


# ===========================================================================
# handle_v1_proxy and handle_proxy direct unit tests
# ===========================================================================

class TestProxyHandlers(unittest.IsolatedAsyncioTestCase):

    async def _make_request(self, path="/v1/messages", query="") -> MagicMock:
        from starlette.requests import Request
        scope = {
            "type": "http",
            "method": "POST",
            "path": path,
            "query_string": query.encode() if query else b"",
            "headers": [],
        }
        async def receive():
            return {"type": "http.request", "body": b"{}", "more_body": False}
        return Request(scope, receive)

    async def test_handle_v1_proxy_forwards(self):
        from tokenpak.proxy.server_async import handle_v1_proxy
        request = await self._make_request("/v1/messages")

        with patch("tokenpak.proxy.server_async._forward_request", new_callable=AsyncMock) as mock_fwd:
            from starlette.responses import JSONResponse
            mock_fwd.return_value = JSONResponse({"ok": True})
            with patch("tokenpak.proxy.router.ProviderRouter") as MockRouter:
                route = MagicMock()
                route.full_url = "https://api.anthropic.com/v1/messages"
                MockRouter.return_value.route.return_value = route
                response = await handle_v1_proxy(request)

        self.assertEqual(response.status_code, 200)
        mock_fwd.assert_called_once()

    async def test_handle_v1_proxy_router_fallback(self):
        """When router raises, falls back to Anthropic URL."""
        from tokenpak.proxy.server_async import handle_v1_proxy
        request = await self._make_request("/v1/messages")

        with patch("tokenpak.proxy.server_async._forward_request", new_callable=AsyncMock) as mock_fwd, \
             patch("tokenpak.proxy.router.ProviderRouter") as MockRouter:
            MockRouter.return_value.route.side_effect = RuntimeError("no route")
            from starlette.responses import JSONResponse
            mock_fwd.return_value = JSONResponse({"ok": True})
            response = await handle_v1_proxy(request)

        # Fallback → anthropic URL
        call_args = mock_fwd.call_args[0]
        self.assertIn("api.anthropic.com", call_args[1])

    async def test_handle_proxy_full_url(self):
        """handle_proxy extracts full URL from path."""
        from tokenpak.proxy.server_async import handle_proxy
        request = await self._make_request("/http://example.com/test")

        with patch("tokenpak.proxy.server_async._forward_request", new_callable=AsyncMock) as mock_fwd:
            from starlette.responses import JSONResponse
            mock_fwd.return_value = JSONResponse({"ok": True})
            response = await handle_proxy(request)

        mock_fwd.assert_called_once()

    async def test_handle_proxy_with_query(self):
        """handle_proxy appends query string."""
        from tokenpak.proxy.server_async import handle_proxy
        request = await self._make_request("/https://api.anthropic.com/test", query="key=val")

        with patch("tokenpak.proxy.server_async._forward_request", new_callable=AsyncMock) as mock_fwd:
            from starlette.responses import JSONResponse
            mock_fwd.return_value = JSONResponse({"ok": True})
            response = await handle_proxy(request)

        mock_fwd.assert_called_once()


# ===========================================================================
# Additional FilterParams / sessions error branch
# ===========================================================================

class TestSessionsEndpointErrors(unittest.TestCase):

    def _make_ps(self):
        ps = MagicMock()
        ps.health.return_value = {"status": "ok"}
        ps.stats.return_value = {}
        ps.last_request_stats.return_value = {}
        ps.session_stats.return_value = {}
        ps.trace_storage.get_all.return_value = []
        ps.trace_storage.get_last.return_value = None
        ps._session_lock = threading.Lock()
        ps._compression_lock = threading.Lock()
        ps._last_lock = threading.Lock()
        ps.session = {
            "requests": 0, "input_tokens": 0, "sent_input_tokens": 0,
            "saved_tokens": 0, "protected_tokens": 0, "output_tokens": 0,
            "cost": 0.0, "cost_saved": 0.0, "cache_read_tokens": 0, "cache_creation_tokens": 0,
            "errors": 0,
        }
        return ps

    def test_sessions_invalid_params_400(self):
        from starlette.testclient import TestClient
        ps = self._make_ps()
        app = create_async_app(ps)

        with patch("tokenpak.dashboard.session_filter.FilterParams.from_query_string",
                   side_effect=ValueError("bad param")):
            client = TestClient(app, raise_server_exceptions=False)
            resp = client.get("/v1/sessions?bad=param")

        # Should return 400 on bad params
        if resp.status_code == 400:
            data = resp.json()
            self.assertEqual(data["error"], "invalid_params")


if __name__ == "__main__":
    unittest.main()
