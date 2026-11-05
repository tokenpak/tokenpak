# SPDX-License-Identifier: Apache-2.0
"""Real proxy handler with in-memory client I/O and a mock upstream transport."""

import json
from email.message import Message
from io import BytesIO
from types import SimpleNamespace

import httpx
import pytest

from tokenpak.proxy import circuit_breaker, monitor, server
from tokenpak.proxy.spend_guard import usage_anchor

MODEL = "claude-sonnet-4-6"
URL = "https://api.anthropic.com/v1/messages"


@pytest.fixture
def proxy_domain(tmp_path, monkeypatch):
    usage_anchor.reset_for_testing()
    assert monitor._stop_db_write_queue(timeout=3)
    monkeypatch.setattr(circuit_breaker, "_registry", circuit_breaker.CircuitBreakerRegistry())
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "spend_guard": {
                    "enabled": True,
                    "reservations_enabled": True,
                    "audit_db_path": str(tmp_path / "guard.db"),
                    "rolling_caps_per_agent_max_cost_usd": 0,
                    "rolling_caps_per_fleet_max_cost_usd": 0,
                }
            }
        )
    )
    for key, value in {
        "TOKENPAK_HOME": str(tmp_path / "state"),
        "TOKENPAK_CONFIG": str(config),
        "TOKENPAK_PASSTHROUGH": "1",
        "TOKENPAK_CAPSULE_BUILDER": "0",
        "TOKENPAK_UPSTREAM_RETRIES": "1",
        "TOKENPAK_CREDS_ROUTER_ENABLED": "0",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("tokenpak.proxy.config.MONITOR_DB", str(tmp_path / "monitor.db"))
    monkeypatch.setattr(
        "tokenpak.proxy.spend_guard._context_window.get_model_max_context", lambda _: 1000
    )
    received, behavior = [], {}

    def transport(request):
        assert str(request.url) == URL
        received.append(bytes(request.content))
        usage = {
            "input_tokens": 25,
            "cache_read_input_tokens": 940,
            "cache_creation_input_tokens": 35,
            "output_tokens": 1,
        }
        envelope = {
            "type": "message",
            "role": "assistant",
            "model": MODEL,
            "content": [],
            "usage": usage,
            "stop_reason": "end_turn",
        }
        if behavior.get("status"):
            return httpx.Response(behavior["status"], json=envelope)
        if json.loads(request.content).get("stream"):
            events = [
                {"type": "message_start", "message": envelope},
                {"type": "message_delta", "usage": {"output_tokens": 1}},
            ]
            if not behavior.get("partial"):
                events.append({"type": "message_stop"})
            raw = b"".join(b"data: " + json.dumps(event).encode() + b"\n\n" for event in events)
            return httpx.Response(
                200, stream=httpx.ByteStream(raw), headers={"Content-Type": "text/event-stream"}
            )
        return httpx.Response(200, json=envelope)

    proxy = server.ProxyServer(port=0)
    proxy._connection_pool._make_client = lambda: httpx.Client(
        transport=httpx.MockTransport(transport)
    )

    def send(*, stream=False):
        raw = json.dumps(
            {
                "model": MODEL,
                "max_tokens": 16,
                "stream": stream,
                "messages": [{"role": "user", "content": "synthetic input"}],
            }
        ).encode()
        handler = object.__new__(server._ProxyHandler)
        handler.server = SimpleNamespace(proxy_server=proxy)
        handler.headers = Message()
        for key, value in {
            "Content-Type": "application/json",
            "Content-Length": str(len(raw)),
            "x-api-key": "synthetic-only",
            "X-TokenPak-Session": "fixture",
        }.items():
            handler.headers[key] = value
        handler.path = URL
        handler.client_address = ("127.0.0.1", 12345)
        handler.rfile, handler.wfile = BytesIO(raw), BytesIO()
        statuses = []
        handler.send_response = lambda status, *args: statuses.append(status)
        handler.send_header = lambda *args: None
        handler.end_headers = lambda: None
        handler._proxy_to(URL, "POST")
        return statuses[-1], handler.wfile.getvalue(), raw

    yield send, received, behavior
    proxy.stop()
    assert monitor._stop_db_write_queue(timeout=3)
    with monitor._DB_LOCK:
        if monitor._DB_CONNECTION is not None:
            monitor._DB_CONNECTION.close()
        monitor._DB_CONNECTION = None
        monitor._DB_CONNECTION_PATH = None
    usage_anchor.reset_for_testing()


@pytest.mark.parametrize("stream", [False, True])
def test_completed_provider_usage_anchors_next_guard_request(proxy_domain, stream):
    send, received, _ = proxy_domain
    status, response, raw = send(stream=stream)
    assert status == 200, response
    assert usage_anchor.projection_floor(raw, MODEL, "fixture") == 1000
    status, response, _ = send(stream=stream)
    assert status == 402, response
    error = json.loads(response)["error"]
    assert error["type"] == "tokenpak_spend_guard_hard_blocked"
    assert error["projected_input_tokens"] == 1000
    assert len(received) == 1


@pytest.mark.parametrize("failure", ["partial", "http_error"])
def test_incomplete_or_failed_response_does_not_create_anchor(proxy_domain, failure):
    send, received, behavior = proxy_domain
    behavior.update({"partial": True} if failure == "partial" else {"status": 400})
    status, response, raw = send(stream=failure == "partial")
    assert status == (200 if failure == "partial" else 400), response
    assert len(received) == 1
    assert usage_anchor.projection_floor(raw, MODEL, "fixture") == 0
