"""Provider-usage ledger truth for Dispatch session economics.

All response fixtures are local. The tests prove provider usage is observed
from streaming and non-streaming copies, persisted with provenance, and does
not alter the bytes relayed to the client.
"""

from __future__ import annotations

import http.client
import json
import socket
import sqlite3
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

import tokenpak.proxy.server as server_module
from tokenpak.proxy.monitor import Monitor
from tokenpak.proxy.router import ProviderRouter, estimate_cost
from tokenpak.proxy.server import (
    ProxyServer,
    _cost_observation,
    _extract_response_usage,
    _local_rate_estimated_cost_saved,
    _provider_usage_observation,
    _safe_provider_usage_observation,
)
from tokenpak.proxy.streaming import _extract_sse_usage, extract_sse_tokens
from tokenpak.services.providers import _registry as usage_registry
from tokenpak.services.providers import get_usage_parser

_FIXTURES = Path(__file__).parent.parent / "fixtures"
_JSON_BODY = (_FIXTURES / "json_response_messages.json").read_bytes()
_SSE_BODY = (_FIXTURES / "sse_response_message_delta.txt").read_bytes()

_OPENAI_USAGE = {
    "input_tokens": 100,
    "input_tokens_details": {"cached_tokens": 40},
    "output_tokens": 60,
    "output_tokens_details": {"reasoning_tokens": 25},
    "total_tokens": 160,
}
_OPENAI_JSON_BODY = json.dumps(
    {"id": "resp_ledger", "object": "response", "usage": _OPENAI_USAGE},
    separators=(",", ":"),
).encode()
_OPENAI_SSE_BODY = (
    "event: response.completed\n"
    f"data: {json.dumps({'type': 'response.completed', 'response': {'usage': _OPENAI_USAGE}}, separators=(',', ':'))}\n\n"
    "data: [DONE]\n\n"
).encode()
_GOOGLE_USAGE = {
    "promptTokenCount": 80,
    "candidatesTokenCount": 20,
    "thoughtsTokenCount": 10,
    "cachedContentTokenCount": 30,
    "totalTokenCount": 110,
}
_GOOGLE_JSON_BODY = json.dumps(
    {"candidates": [{"content": {"parts": [{"text": "ok"}]}}], "usageMetadata": _GOOGLE_USAGE},
    separators=(",", ":"),
).encode()
_GOOGLE_SSE_BODY = (
    f"data: {json.dumps({'candidates': [{'content': {'parts': [{'text': 'ok'}]}}], 'usageMetadata': _GOOGLE_USAGE}, separators=(',', ':'))}\n\n"
).encode()
_RAW_ERROR_BODY = b'{"error":"raw upstream detail"}'


class _UsageFixtureHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        request_body = self.rfile.read(length) if length else b""
        status = self.server.response_status  # type: ignore[attr-defined]
        provider = self.server.usage_provider  # type: ignore[attr-defined]
        is_streaming = "streamGenerateContent" in self.path or "alt=sse" in self.path
        try:
            is_streaming = is_streaming or bool(json.loads(request_body).get("stream"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            pass

        if status >= 400:
            response_body = _RAW_ERROR_BODY
            content_type = "application/json"
        elif provider == "google":
            response_body = _GOOGLE_SSE_BODY if is_streaming else _GOOGLE_JSON_BODY
            content_type = "text/event-stream" if is_streaming else "application/json"
        else:
            response_body = _OPENAI_SSE_BODY if is_streaming else _OPENAI_JSON_BODY
            content_type = "text/event-stream" if is_streaming else "application/json"

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(response_body)))
        self.end_headers()
        self.wfile.write(response_body)


@contextmanager
def _usage_upstream(provider: str, *, status: int = 200):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _UsageFixtureHandler)
    server.usage_provider = provider  # type: ignore[attr-defined]
    server.response_status = status  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5.0)
        server.server_close()


def _post(
    proxy_port: int,
    target: str,
    *,
    stream: bool,
    payload: dict[str, object] | None = None,
) -> tuple[int, bytes]:
    if payload is None:
        payload = {
            "model": "claude-sonnet-4-5",
            "max_tokens": 32,
            "messages": [{"role": "user", "content": "ledger truth probe"}],
            "stream": stream,
        }
    body = json.dumps(payload).encode()
    conn = http.client.HTTPConnection("127.0.0.1", proxy_port, timeout=60)
    try:
        conn.request(
            "POST",
            target,
            body=body,
            headers={"Content-Type": "application/json", "x-api-key": "test-key"},
        )
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def _start_test_proxy(
    monkeypatch: pytest.MonkeyPatch,
    db: Path,
    *,
    provider: str,
    upstream_base: str,
) -> tuple[ProxyServer, int]:
    import tokenpak.proxy.config as proxy_config

    monkeypatch.setattr(proxy_config, "MONITOR_DB", str(db))
    monkeypatch.setattr(
        server_module,
        "INTERCEPT_HOSTS",
        set(server_module.INTERCEPT_HOSTS) | {"127.0.0.1"},
    )
    monkeypatch.setenv("TOKENPAK_SPEND_GUARD_ENABLED", "0")
    monkeypatch.setenv("TOKENPAK_CAPSULE_BUILDER", "0")

    http_server_type = server_module._ThreadedHTTPServer

    def bind_ephemeral(address, handler):
        return http_server_type((address[0], 0), handler)

    monkeypatch.setattr(server_module, "_ThreadedHTTPServer", bind_ephemeral)
    proxy = ProxyServer(host="127.0.0.1", port=1)
    proxy.router = ProviderRouter(
        custom_urls={provider: upstream_base},
        custom_hosts={upstream_base: provider},
    )
    proxy.start(blocking=False)
    assert proxy._server is not None
    assert proxy.monitor is not None
    # These tests own persistence/call-site truth, not the process-global
    # async writer (covered by test_monitor_write_truth.py). Stop the empty
    # queue so request rows use Monitor.log()'s synchronous fallback and one
    # slow filesystem drain cannot poison later writer-lifecycle tests.
    assert proxy.monitor.stop(timeout=20.0)
    port = int(proxy._server.server_address[1])
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return proxy, port
        except OSError:
            time.sleep(0.05)
    proxy.stop()
    pytest.fail("proxy did not open its listener")


def test_anthropic_sse_usage_is_merged_without_changing_source_bytes():
    before = bytes(_SSE_BODY)
    usage = _extract_sse_usage(_SSE_BODY)
    tokens = extract_sse_tokens(_SSE_BODY)

    assert _SSE_BODY == before
    assert usage == {
        "input_tokens": 42,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
        "output_tokens": 12,
    }
    assert tokens["output_tokens"] == 12
    assert tokens["cache_read_input_tokens"] == 0
    assert tokens["cache_creation_input_tokens"] == 0


def test_openai_responses_stream_usage_and_request_effort_are_truthful():
    raw_usage = {
        "input_tokens": 100,
        "input_tokens_details": {"cached_tokens": 40},
        "output_tokens": 60,
        "output_tokens_details": {"reasoning_tokens": 25},
        "total_tokens": 160,
    }
    stream = (
        "event: response.completed\n"
        f"data: {json.dumps({'type': 'response.completed', 'response': {'usage': raw_usage}})}\n\n"
        "data: [DONE]\n\n"
    ).encode()

    assert _extract_sse_usage(stream) == raw_usage
    assert extract_sse_tokens(stream)["cache_read_input_tokens"] == 40

    observed = _provider_usage_observation(
        "openai-codex",
        raw_usage,
        b'{"reasoning":{"effort":"high"}}',
    )
    assert observed == {
        "reasoning_tokens": 25,
        "visible_output_tokens": 35,
        "total_billable_tokens": 160,
        "reasoning_effort": "high",
        "reasoning_usage_source": "provider_usage_object",
        "provider_usage_ref": observed["provider_usage_ref"],
        "provider_input_tokens": 100,
        "provider_input_tokens_include_cache": True,
        "provider_output_tokens": 60,
        "provider_cache_read_tokens": 40,
        "provider_cache_creation_tokens": None,
        "provider_usage_source": "provider_usage_object",
        "provider_usage_confidence": "high",
        "reasoning_effort_source": "request_body",
        "reasoning_effort_raw": "high",
    }
    assert observed["provider_usage_ref"]


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (
            {"usage": {"input_tokens": 1, "output_tokens": 2}},
            {"input_tokens": 1, "output_tokens": 2},
        ),
        (
            {"usageMetadata": {"promptTokenCount": 3, "candidatesTokenCount": 4}},
            {"promptTokenCount": 3, "candidatesTokenCount": 4},
        ),
        (
            {"response": {"usage": {"input_tokens": 5, "output_tokens": 6}}},
            {"input_tokens": 5, "output_tokens": 6},
        ),
    ],
)
def test_nonstream_usage_shapes_are_observed(payload, expected):
    wire = json.dumps(payload, separators=(",", ":")).encode()
    before = bytes(wire)
    assert _extract_response_usage(wire) == expected
    assert wire == before


def test_missing_usage_is_unknown_not_provider_zero():
    observed = _provider_usage_observation(
        "openai",
        None,
        b'{"reasoning_effort":"low"}',
    )
    assert observed["provider_usage_source"] == "unavailable"
    assert observed["provider_usage_confidence"] == "unknown"
    assert observed["provider_input_tokens"] is None
    assert observed["provider_output_tokens"] is None
    assert observed["total_billable_tokens"] is None
    assert observed["reasoning_effort"] == "low"
    assert observed["reasoning_effort_source"] == "request_body"
    assert observed["reasoning_effort_raw"] == "low"


def test_new_reasoning_effort_is_preserved_without_widening_normalized_contract():
    observed = _provider_usage_observation(
        "openai-codex",
        {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        b'{"reasoning":{"effort":"xhigh"}}',
    )
    assert observed["reasoning_effort"] == ""
    assert observed["reasoning_effort_raw"] == "xhigh"
    assert observed["reasoning_effort_source"] == "request_body_unrecognized"


def test_sse_data_line_without_space_is_valid():
    raw_usage = {"input_tokens": 4, "output_tokens": 2, "total_tokens": 6}
    stream = (
        f"data:{json.dumps({'type': 'response.completed', 'response': {'usage': raw_usage}})}\n\n"
        "data:[DONE]\n\n"
    ).encode()
    assert _extract_sse_usage(stream) == raw_usage
    assert extract_sse_tokens(stream)["output_tokens"] == 2


def test_route_provider_identity_drives_codex_and_custom_parser_selection(monkeypatch):
    codex_route = ProviderRouter().route(
        "https://chatgpt.com/backend-api/codex/responses",
        {},
        b'{"model":"gpt-5.6-sol"}',
    )
    assert codex_route.provider == "openai-codex"
    assert (
        _provider_usage_observation(
            codex_route.provider,
            {"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
            b"{}",
        )["provider_input_tokens"]
        == 3
    )

    custom_base = "http://127.0.0.1:43210"
    custom_router = ProviderRouter(
        custom_urls={"custom-acme": custom_base},
        custom_hosts={custom_base: "custom-acme"},
    )
    custom_route = custom_router.route(
        f"{custom_base}/v1/responses",
        {},
        b'{"model":"acme-reasoner"}',
    )
    assert custom_route.provider == "custom-acme"
    monkeypatch.setitem(
        usage_registry._REGISTRY,
        custom_route.provider,
        get_usage_parser("openai"),
    )
    assert (
        _provider_usage_observation(
            custom_route.provider,
            {"input_tokens": 7, "output_tokens": 1, "total_tokens": 8},
            b"{}",
        )["provider_input_tokens"]
        == 7
    )


def test_third_party_parser_failure_is_logged_and_keeps_usage_unknown(monkeypatch, caplog):
    def broken_parser(_usage):
        raise RuntimeError("parser probe failure")

    monkeypatch.setitem(usage_registry._REGISTRY, "custom-broken", broken_parser)
    observed = _safe_provider_usage_observation(
        "custom-broken",
        {"input_tokens": 7},
        b'{"reasoning":{"effort":"xhigh"}}',
    )

    assert observed["provider_usage_source"] == "unavailable"
    assert observed["provider_usage_confidence"] == "unknown"
    assert observed["provider_input_tokens"] is None
    assert observed["reasoning_effort_raw"] == "xhigh"
    assert "provider usage parser failed" in caplog.text


def test_cost_observation_uses_provider_counts_and_labels_price_provenance():
    anthropic_usage = _provider_usage_observation(
        "anthropic",
        {
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_input_tokens": 25,
            "cache_creation_input_tokens": 10,
        },
        b"{}",
    )
    observed = _cost_observation(
        provider="anthropic",
        model="claude-sonnet-4-5",
        status_code=200,
        usage=anthropic_usage,
        fallback_input_tokens=999,
        fallback_output_tokens=999,
        fallback_cache_read_tokens=0,
        fallback_cache_creation_tokens=0,
    )
    assert observed == {
        "input_tokens": 135,
        "output_tokens": 50,
        "cache_read_tokens": 25,
        "cache_creation_tokens": 10,
        "cost_basis": "provider_usage_rate_estimate",
        "pricing_source": "seed",
    }

    inferred = _cost_observation(
        provider="openai",
        model="gpt-5.6-sol",
        status_code=200,
        usage=_provider_usage_observation(
            "openai",
            {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            b"{}",
        ),
        fallback_input_tokens=999,
        fallback_output_tokens=999,
        fallback_cache_read_tokens=0,
        fallback_cache_creation_tokens=0,
    )
    assert inferred["pricing_source"] == "inferred"

    subscription = _cost_observation(
        provider="openai-codex",
        model="gpt-5.6-sol",
        status_code=200,
        usage=_provider_usage_observation(
            "openai-codex",
            {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            b"{}",
        ),
        fallback_input_tokens=999,
        fallback_output_tokens=999,
        fallback_cache_read_tokens=0,
        fallback_cache_creation_tokens=0,
    )
    assert subscription["cost_basis"] == "subscription_billed_cost_unknown"
    assert subscription["pricing_source"] == "unknown"


def test_estimated_parser_counts_do_not_replace_local_cost_inputs(monkeypatch):
    def estimated_parser(_usage):
        return {
            "input_tokens": 1,
            "visible_output_tokens": 1,
            "reasoning_tokens": None,
            "total_output_tokens": 1,
            "total_billable_tokens": 2,
            "reasoning_effort": None,
            "usage_source": "estimated",
            "provider_usage_ref": None,
        }

    monkeypatch.setitem(usage_registry._REGISTRY, "custom-estimated", estimated_parser)
    monkeypatch.setitem(
        usage_registry._INPUT_TOKENS_INCLUDE_CACHE,
        "custom-estimated",
        True,
    )
    observed_usage = _provider_usage_observation(
        "custom-estimated",
        {"input_tokens": 1, "output_tokens": 1},
        b"{}",
    )
    observed_cost = _cost_observation(
        provider="custom-estimated",
        model="custom-model",
        status_code=200,
        usage=observed_usage,
        fallback_input_tokens=70,
        fallback_output_tokens=30,
        fallback_cache_read_tokens=0,
        fallback_cache_creation_tokens=0,
    )

    assert observed_usage["provider_usage_source"] == "estimated"
    assert observed_cost["input_tokens"] == 70
    assert observed_cost["output_tokens"] == 30
    assert observed_cost["cost_basis"] == "route_cost_unknown"


def test_custom_provider_can_declare_catalog_cost_policy(monkeypatch):
    provider = "custom-catalog"
    monkeypatch.setitem(usage_registry._REGISTRY, provider, get_usage_parser("openai"))
    monkeypatch.setitem(usage_registry._INPUT_TOKENS_INCLUDE_CACHE, provider, True)
    monkeypatch.setitem(usage_registry._COST_POLICIES, provider, "catalog_rate_estimate")

    observed = _cost_observation(
        provider=provider,
        model="gpt-5.6-sol",
        status_code=200,
        usage=_provider_usage_observation(
            provider,
            {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            b"{}",
        ),
        fallback_input_tokens=999,
        fallback_output_tokens=999,
        fallback_cache_read_tokens=0,
        fallback_cache_creation_tokens=0,
    )

    assert observed["input_tokens"] == 10
    assert observed["output_tokens"] == 2
    assert observed["cost_basis"] == "provider_usage_rate_estimate"
    assert observed["pricing_source"] == "inferred"


def test_cost_saved_uses_one_local_token_scale():
    expected = estimate_cost("claude-sonnet-4-5", 100, 20, 10, 5) - estimate_cost(
        "claude-sonnet-4-5", 70, 20, 10, 5
    )

    observed = _local_rate_estimated_cost_saved(
        model="claude-sonnet-4-5",
        input_tokens=100,
        sent_input_tokens=70,
        output_tokens=20,
        cache_read_tokens=10,
        cache_creation_tokens=5,
    )

    assert observed == pytest.approx(expected)


def test_monitor_log_persists_provider_usage_and_provenance(tmp_path):
    db = tmp_path / "monitor.db"
    monitor = Monitor(db_path=str(db))
    assert monitor.stop(timeout=20.0)
    monitor.log(
        model="gpt-test",
        input_tokens=110,
        output_tokens=60,
        cost=0.01,
        latency_ms=10,
        status_code=200,
        endpoint="local-test",
        reasoning_tokens=25,
        visible_output_tokens=35,
        total_billable_tokens=160,
        reasoning_effort="high",
        reasoning_usage_source="provider_usage_object",
        provider_usage_ref="abcdef123456",
        provider_usage_provider="openai",
        provider_input_tokens=100,
        provider_output_tokens=60,
        provider_cache_read_tokens=40,
        provider_cache_creation_tokens=None,
        provider_usage_source="provider_usage_object",
        provider_usage_confidence="high",
        reasoning_effort_source="request_body",
        reasoning_effort_raw="high",
        cost_basis="provider_usage_rate_estimate",
        pricing_source="seed",
        stream_mode="json",
    )
    monitor.log(
        model="unknown-model",
        input_tokens=0,
        output_tokens=0,
        cost=0.0,
        latency_ms=10,
        status_code=200,
        endpoint="local-test-missing-usage",
    )
    assert monitor.flush(timeout=10.0)
    conn = sqlite3.connect(str(db))
    try:
        rows = conn.execute(
            "SELECT reasoning_tokens, visible_output_tokens, total_billable_tokens, "
            "reasoning_effort, reasoning_usage_source, provider_usage_ref, "
            "provider_usage_provider, "
            "provider_input_tokens, provider_output_tokens, provider_cache_read_tokens, "
            "provider_cache_creation_tokens, provider_usage_source, "
            "provider_usage_confidence, reasoning_effort_source, reasoning_effort_raw, "
            "cost_basis, pricing_source, stream_mode "
            "FROM requests "
            "ORDER BY id"
        ).fetchall()
    finally:
        conn.close()
        monitor.stop(timeout=5.0)
    assert rows[0] == (
        25,
        35,
        160,
        "high",
        "provider_usage_object",
        "abcdef123456",
        "openai",
        100,
        60,
        40,
        None,
        "provider_usage_object",
        "high",
        "request_body",
        "high",
        "provider_usage_rate_estimate",
        "seed",
        "json",
    )
    assert rows[1] == (
        None,
        None,
        None,
        "",
        "",
        "",
        "",
        None,
        None,
        None,
        None,
        "",
        "",
        "",
        "",
        "",
        "",
        "",
    )


@pytest.mark.needs_proxy
@pytest.mark.timeout(120)
def test_real_proxy_persists_equivalent_usage_without_rewriting_responses(
    tmp_path,
    monkeypatch,
    stub_upstream,
):
    """Exercise the actual response and Monitor.log call sites in both modes."""
    db = tmp_path / "monitor.db"
    import tokenpak.proxy.config as proxy_config

    monkeypatch.setattr(proxy_config, "MONITOR_DB", str(db))
    monkeypatch.setattr(
        server_module,
        "INTERCEPT_HOSTS",
        set(server_module.INTERCEPT_HOSTS) | {"127.0.0.1"},
    )
    monkeypatch.setenv("TOKENPAK_SPEND_GUARD_ENABLED", "0")
    monkeypatch.setenv("TOKENPAK_CAPSULE_BUILDER", "0")

    upstream_base = f"http://127.0.0.1:{stub_upstream.server_port}"
    target = f"{upstream_base}/v1/messages"
    http_server_type = server_module._ThreadedHTTPServer

    def bind_ephemeral(address, handler):
        return http_server_type((address[0], 0), handler)

    monkeypatch.setattr(server_module, "_ThreadedHTTPServer", bind_ephemeral)
    proxy = ProxyServer(host="127.0.0.1", port=1)
    proxy.router = ProviderRouter(
        custom_urls={"anthropic": upstream_base},
        custom_hosts={upstream_base: "anthropic"},
    )
    proxy.start(blocking=False)
    assert proxy._server is not None
    assert proxy.monitor is not None
    assert proxy.monitor.stop(timeout=20.0)
    port = int(proxy._server.server_address[1])
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                break
        except OSError:
            time.sleep(0.05)
    else:
        proxy.stop()
        pytest.fail("proxy did not open its listener")

    try:
        nonstream_status, nonstream_body = _post(port, target, stream=False)
        stream_status, stream_body = _post(port, target, stream=True)
        assert nonstream_status == stream_status == 200
        assert nonstream_body == _JSON_BODY
        assert stream_body == _SSE_BODY
        assert proxy.monitor is not None
        assert proxy.monitor.flush(timeout=10.0)

        conn = sqlite3.connect(str(db))
        try:
            rows = conn.execute(
                "SELECT provider_input_tokens, provider_output_tokens, "
                "provider_cache_read_tokens, provider_cache_creation_tokens, "
                "total_billable_tokens, provider_usage_source, "
                "provider_usage_confidence, provider_usage_provider, cost_basis, "
                "pricing_source, estimated_cost, stream_mode, "
                "event_transform_applied FROM requests ORDER BY id"
            ).fetchall()
        finally:
            conn.close()
    finally:
        proxy.stop()

    expected_cost = estimate_cost("claude-sonnet-4-5", 42, 12, 0, 0)
    assert [row[:-3] for row in rows] == [
        (
            42,
            12,
            0,
            0,
            54,
            "provider_usage_object",
            "high",
            "anthropic",
            "provider_usage_rate_estimate",
            "seed",
        ),
        (
            42,
            12,
            0,
            0,
            54,
            "provider_usage_object",
            "high",
            "anthropic",
            "provider_usage_rate_estimate",
            "seed",
        ),
    ]
    assert [row[-2:] for row in rows] == [("json", 0), ("sse", 0)]
    assert rows[0][-3] == pytest.approx(expected_cost)
    assert rows[1][-3] == pytest.approx(expected_cost)


@pytest.mark.needs_proxy
@pytest.mark.timeout(120)
@pytest.mark.parametrize(
    (
        "provider",
        "json_path",
        "stream_path",
        "json_body",
        "stream_body",
        "expected_facts",
        "expected_cost_basis",
    ),
    [
        (
            "openai-codex",
            "/v1/responses",
            "/v1/responses",
            _OPENAI_JSON_BODY,
            _OPENAI_SSE_BODY,
            (100, 60, 40, None, 25, 35, 160),
            "subscription_billed_cost_unknown",
        ),
        (
            "google",
            "/v1beta/models/gemini-2.5-pro:generateContent",
            "/v1beta/models/gemini-2.5-pro:streamGenerateContent?alt=sse",
            _GOOGLE_JSON_BODY,
            _GOOGLE_SSE_BODY,
            (80, 30, 30, None, 10, 20, 110),
            "provider_usage_rate_estimate",
        ),
    ],
)
def test_real_proxy_persists_openai_codex_and_google_json_sse_parity(
    tmp_path,
    monkeypatch,
    provider,
    json_path,
    stream_path,
    json_body,
    stream_body,
    expected_facts,
    expected_cost_basis,
):
    db = tmp_path / f"{provider}-monitor.db"
    with _usage_upstream(provider) as upstream:
        upstream_base = f"http://127.0.0.1:{upstream.server_port}"
        proxy, port = _start_test_proxy(
            monkeypatch,
            db,
            provider=provider,
            upstream_base=upstream_base,
        )
        try:
            if provider == "google":
                # A valid provider response must persist even when the local
                # request estimator sees zero text tokens.
                json_payload = {
                    "model": "gemini-2.5-pro",
                    "contents": [{"role": "user", "parts": []}],
                }
                stream_payload = dict(json_payload)
            else:
                json_payload = {
                    "model": "gpt-5.6-sol",
                    "input": "ledger truth probe",
                    "reasoning": {"effort": "high"},
                    "stream": False,
                }
                stream_payload = {**json_payload, "stream": True}

            json_status, relayed_json = _post(
                port,
                f"{upstream_base}{json_path}",
                stream=False,
                payload=json_payload,
            )
            stream_status, relayed_stream = _post(
                port,
                f"{upstream_base}{stream_path}",
                stream=True,
                payload=stream_payload,
            )
            assert json_status == stream_status == 200
            assert relayed_json == json_body
            assert relayed_stream == stream_body
            assert proxy.monitor is not None
            assert proxy.monitor.flush(timeout=20.0)

            with sqlite3.connect(str(db)) as conn:
                rows = conn.execute(
                    "SELECT provider_input_tokens, provider_output_tokens, "
                    "provider_cache_read_tokens, provider_cache_creation_tokens, "
                    "reasoning_tokens, visible_output_tokens, total_billable_tokens, "
                    "provider_usage_source, provider_usage_confidence, "
                    "provider_usage_provider, cost_basis, pricing_source, stream_mode, "
                    "event_transform_applied FROM requests ORDER BY id"
                ).fetchall()
        finally:
            proxy.stop()

    assert len(rows) == 2
    assert [row[:7] for row in rows] == [expected_facts, expected_facts]
    assert [row[7:10] for row in rows] == [
        ("provider_usage_object", "high", provider),
        ("provider_usage_object", "high", provider),
    ]
    assert [row[10] for row in rows] == [expected_cost_basis, expected_cost_basis]
    if provider == "openai-codex":
        assert [row[11] for row in rows] == ["unknown", "unknown"]
    else:
        assert all(row[11] in {"seed", "discovered", "inferred"} for row in rows)
    assert [row[12:] for row in rows] == [("json", 0), ("sse", 0)]


@pytest.mark.needs_proxy
@pytest.mark.timeout(120)
def test_real_proxy_custom_parser_stays_route_cost_unknown(tmp_path, monkeypatch):
    provider = "custom-acme"
    monkeypatch.setitem(usage_registry._REGISTRY, provider, get_usage_parser("openai"))
    monkeypatch.setitem(usage_registry._INPUT_TOKENS_INCLUDE_CACHE, provider, True)
    monkeypatch.delitem(usage_registry._COST_POLICIES, provider, raising=False)

    db = tmp_path / "custom-monitor.db"
    with _usage_upstream(provider) as upstream:
        upstream_base = f"http://127.0.0.1:{upstream.server_port}"
        proxy, port = _start_test_proxy(
            monkeypatch,
            db,
            provider=provider,
            upstream_base=upstream_base,
        )
        try:
            status, relayed = _post(
                port,
                f"{upstream_base}/v1/responses",
                stream=False,
                payload={"model": "custom-model", "input": "probe", "stream": False},
            )
            assert status == 200
            assert relayed == _OPENAI_JSON_BODY
            assert proxy.monitor is not None
            assert proxy.monitor.flush(timeout=20.0)
            with sqlite3.connect(str(db)) as conn:
                row = conn.execute(
                    "SELECT provider_usage_provider, provider_input_tokens, "
                    "provider_output_tokens, provider_usage_source, cost_basis, "
                    "pricing_source, event_transform_applied FROM requests"
                ).fetchone()
        finally:
            proxy.stop()

    assert row == (
        provider,
        100,
        60,
        "provider_usage_object",
        "route_cost_unknown",
        "unknown",
        0,
    )


@pytest.mark.needs_proxy
@pytest.mark.timeout(120)
def test_real_proxy_marks_normalized_error_bodies_as_transformed(tmp_path, monkeypatch):
    provider = "custom-error"
    db = tmp_path / "error-monitor.db"
    with _usage_upstream(provider, status=418) as upstream:
        upstream_base = f"http://127.0.0.1:{upstream.server_port}"
        proxy, port = _start_test_proxy(
            monkeypatch,
            db,
            provider=provider,
            upstream_base=upstream_base,
        )
        try:
            responses = [
                _post(
                    port,
                    f"{upstream_base}/v1/responses",
                    stream=stream,
                    payload={"model": "custom-model", "input": "probe", "stream": stream},
                )
                for stream in (False, True)
            ]
            assert [status for status, _ in responses] == [418, 418]
            assert all(body != _RAW_ERROR_BODY for _, body in responses)
            assert proxy.monitor is not None
            assert proxy.monitor.flush(timeout=20.0)
            with sqlite3.connect(str(db)) as conn:
                rows = conn.execute(
                    "SELECT status_code, provider_usage_source, cost_basis, "
                    "stream_mode, event_transform_applied FROM requests ORDER BY id"
                ).fetchall()
        finally:
            proxy.stop()

    assert rows == [
        (418, "unavailable", "non_success_cost_unmeasured", "json", 1),
        (418, "unavailable", "non_success_cost_unmeasured", "sse", 1),
    ]
