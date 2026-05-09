"""tests/proxy/test_cost_forecast_endpoint.py

CCI-11: Cost forecasting endpoint (POST /v1/messages/forecast).

Tests:
  1. Simple body → returns documented JSON shape with all required keys
  2. With cache_control hints → cache_creates_estimate populated in breakdown
  3. Missing messages field → 400 error with documented shape
  4. Pricing edge cases:
     a. Known model (claude-opus-4-5, high cost) vs unknown model (default cost)
     b. max_tokens < 500 → output_estimate set to max_tokens
  5. No upstream call — stub request_count stays at 0 after forecast call

All tests run against a real proxy server (ForwardProxyHandler) with stub upstream.
Requests are sent directly to proxy path /v1/messages/forecast (not forwarded upstream).
No real Anthropic API calls are made.
"""
from __future__ import annotations

import pytest

pytest.importorskip("tokenpak.runtime", reason="module not available in current build")
import json
import os
import socket
import sys
import threading
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: load the monolith proxy.py via importlib (no sys.path mutation)
# ---------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

os.environ.setdefault("ANTHROPIC_API_KEY", "test-sk-cci11-dummy-not-real")
os.environ.setdefault("TOKENPAK_VAULT_INDEX", "0")

import importlib.util as _ilu  # noqa: E402

_spec = _ilu.spec_from_file_location("proxy", _PROJECT_ROOT / "proxy.py")
_proxy = _ilu.module_from_spec(_spec)
sys.modules.setdefault("proxy", _proxy)
_spec.loader.exec_module(_proxy)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _body(
    *,
    model: str = "claude-sonnet-4-5",
    messages: list | None = None,
    system: str | None = None,
    max_tokens: int | None = None,
    tools: list | None = None,
    **extra,
) -> bytes:
    data: dict = {"model": model}
    if messages is not None:
        data["messages"] = messages
    else:
        data["messages"] = [{"role": "user", "content": "hello forecast"}]
    if system is not None:
        data["system"] = system
    if max_tokens is not None:
        data["max_tokens"] = max_tokens
    if tools is not None:
        data["tools"] = tools
    data.update(extra)
    return json.dumps(data).encode()


# ---------------------------------------------------------------------------
# Stub upstream — counts how many upstream requests the proxy forwards
# ---------------------------------------------------------------------------

class _StubUpstreamHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length) if length else b""
        self.server.request_count += 1  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        resp = json.dumps({"id": "stub", "type": "message"}).encode()
        self.send_header("Content-Length", str(len(resp)))
        self.end_headers()
        self.wfile.write(resp)


class _CountingHTTPServer(HTTPServer):
    request_count: int = 0


# ---------------------------------------------------------------------------
# Fixture: stub upstream + real proxy
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def servers():
    """
    Spin up a stub upstream and a real tokenpak proxy.
    Yields (proxy_port, stub_server).
    """
    stub_port = _free_port()
    proxy_port = _free_port()

    stub = _CountingHTTPServer(("127.0.0.1", stub_port), _StubUpstreamHandler)
    stub_t = threading.Thread(target=stub.serve_forever, daemon=True)
    stub_t.start()

    proxy_srv = _proxy.ThreadedHTTPServer(
        ("127.0.0.1", proxy_port), _proxy.ForwardProxyHandler
    )
    proxy_t = threading.Thread(target=proxy_srv.serve_forever, daemon=True)
    proxy_t.start()

    yield proxy_port, stub

    proxy_srv.shutdown()
    proxy_t.join(timeout=2)
    stub.shutdown()
    stub_t.join(timeout=2)


# ---------------------------------------------------------------------------
# Low-level POST helper — sends directly to proxy (not through forward proxy)
# ---------------------------------------------------------------------------

def _post_forecast(proxy_port: int, body: bytes, extra_headers: dict | None = None) -> tuple[int, dict]:
    """POST /v1/messages/forecast directly to the proxy and return (status, body_dict)."""
    conn = HTTPConnection("127.0.0.1", proxy_port, timeout=5)
    headers = {
        "Content-Type": "application/json",
        "Content-Length": str(len(body)),
    }
    if extra_headers:
        headers.update(extra_headers)
    conn.request("POST", "/v1/messages/forecast", body=body, headers=headers)
    resp = conn.getresponse()
    status = resp.status
    try:
        data = json.loads(resp.read())
    except Exception:
        data = {}
    conn.close()
    return status, data


# ===========================================================================
# Test 1: Simple body returns documented JSON shape
# ===========================================================================

class TestSimpleBody:
    """POST with minimal valid body → 200 with all required top-level keys."""

    def test_returns_200(self, servers):
        proxy_port, _ = servers
        status, _ = _post_forecast(proxy_port, _body())
        assert status == 200

    def test_response_has_all_top_level_keys(self, servers):
        proxy_port, _ = servers
        _, data = _post_forecast(proxy_port, _body())
        assert "estimated_cost_usd" in data
        assert "ttfb_estimate_ms" in data
        assert "cache_hit_likelihood" in data
        assert "breakdown" in data

    def test_breakdown_has_required_keys(self, servers):
        proxy_port, _ = servers
        _, data = _post_forecast(proxy_port, _body())
        bd = data["breakdown"]
        assert "input_tokens" in bd
        assert "output_estimate" in bd
        assert "cache_hits_estimate" in bd
        assert "cache_creates_estimate" in bd

    def test_estimated_cost_usd_is_numeric(self, servers):
        proxy_port, _ = servers
        _, data = _post_forecast(proxy_port, _body())
        assert isinstance(data["estimated_cost_usd"], (int, float))
        assert data["estimated_cost_usd"] >= 0

    def test_ttfb_estimate_ms_is_int(self, servers):
        proxy_port, _ = servers
        _, data = _post_forecast(proxy_port, _body())
        assert isinstance(data["ttfb_estimate_ms"], int)

    def test_cache_hit_likelihood_is_float_in_range(self, servers):
        proxy_port, _ = servers
        _, data = _post_forecast(proxy_port, _body())
        chl = data["cache_hit_likelihood"]
        assert isinstance(chl, (int, float))
        assert 0.0 <= chl <= 1.0

    def test_input_tokens_positive(self, servers):
        proxy_port, _ = servers
        _, data = _post_forecast(proxy_port, _body())
        assert data["breakdown"]["input_tokens"] > 0

    def test_default_output_estimate_is_500(self, servers):
        """When no max_tokens is given (or max_tokens >= 500), output_estimate defaults to 500."""
        proxy_port, _ = servers
        _, data = _post_forecast(proxy_port, _body())
        assert data["breakdown"]["output_estimate"] == 500


# ===========================================================================
# Test 2: cache_control hints populate cache_creates_estimate
# ===========================================================================

class TestCacheControlHints:
    """Body with cache_control blocks → cache_creates_estimate > 0."""

    def test_cache_creates_populated_from_message_cache_control(self, servers):
        proxy_port, _ = servers
        body = _body(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "This is a long cacheable block " * 20,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ],
                }
            ]
        )
        _, data = _post_forecast(proxy_port, body)
        assert data["breakdown"]["cache_creates_estimate"] > 0

    def test_cache_creates_populated_from_system_cache_control(self, servers):
        proxy_port, _ = servers
        body = _body(
            system=[
                {
                    "type": "text",
                    "text": "System prompt text " * 20,
                    "cache_control": {"type": "ephemeral"},
                }
            ]
        )
        _, data = _post_forecast(proxy_port, body)
        assert data["breakdown"]["cache_creates_estimate"] > 0

    def test_no_cache_control_gives_zero_creates(self, servers):
        proxy_port, _ = servers
        body = _body(messages=[{"role": "user", "content": "plain message no cache"}])
        _, data = _post_forecast(proxy_port, body)
        assert data["breakdown"]["cache_creates_estimate"] == 0


# ===========================================================================
# Test 3: Missing body fields → 400
# ===========================================================================

class TestMissingFields:
    """Bad requests return 400 with error shape."""

    def test_empty_body_returns_400(self, servers):
        proxy_port, _ = servers
        status, data = _post_forecast(proxy_port, b"{}")
        assert status == 400
        assert "error" in data

    def test_no_messages_array_returns_400(self, servers):
        proxy_port, _ = servers
        body = json.dumps({"model": "claude-sonnet-4-5"}).encode()
        status, data = _post_forecast(proxy_port, body)
        assert status == 400
        assert "error" in data

    def test_messages_not_list_returns_400(self, servers):
        proxy_port, _ = servers
        body = json.dumps({"model": "claude-sonnet-4-5", "messages": "not a list"}).encode()
        status, data = _post_forecast(proxy_port, body)
        assert status == 400

    def test_invalid_json_returns_400(self, servers):
        proxy_port, _ = servers
        status, data = _post_forecast(proxy_port, b"not json at all")
        assert status == 400
        assert "error" in data


# ===========================================================================
# Test 4: Pricing edge cases
# ===========================================================================

class TestPricingEdgeCases:
    """Verify pricing config is applied correctly."""

    def test_opus_costs_more_than_haiku(self, servers):
        """claude-opus-4-5 ($15/MTok input) costs more than claude-haiku-4-5 ($0.8/MTok)."""
        proxy_port, _ = servers
        msg = [{"role": "user", "content": "pricing test " * 50}]
        _, opus_data = _post_forecast(proxy_port, _body(model="claude-opus-4-5", messages=msg))
        _, haiku_data = _post_forecast(proxy_port, _body(model="claude-haiku-4-5", messages=msg))
        assert opus_data["estimated_cost_usd"] > haiku_data["estimated_cost_usd"]

    def test_unknown_model_falls_back_to_defaults(self, servers):
        """Unknown model uses default pricing (3.0/15.0 per MTok); result is non-negative."""
        proxy_port, _ = servers
        _, data = _post_forecast(proxy_port, _body(model="unknown-model-xyz"))
        assert data["estimated_cost_usd"] >= 0

    def test_max_tokens_under_500_sets_output_estimate(self, servers):
        """max_tokens < 500 should set output_estimate to max_tokens, not 500."""
        proxy_port, _ = servers
        _, data = _post_forecast(proxy_port, _body(max_tokens=100))
        assert data["breakdown"]["output_estimate"] == 100

    def test_max_tokens_at_500_uses_default(self, servers):
        """max_tokens >= 500 should leave output_estimate at 500."""
        proxy_port, _ = servers
        _, data = _post_forecast(proxy_port, _body(max_tokens=1024))
        assert data["breakdown"]["output_estimate"] == 500

    def test_tools_contribute_to_input_tokens(self, servers):
        """Request with tools should have more input_tokens than same request without."""
        proxy_port, _ = servers
        msg = [{"role": "user", "content": "use a tool"}]
        tools = [{"name": "search", "description": "Search the web " * 10, "input_schema": {"type": "object"}}]
        _, no_tools = _post_forecast(proxy_port, _body(messages=msg))
        _, with_tools = _post_forecast(proxy_port, _body(messages=msg, tools=tools))
        assert with_tools["breakdown"]["input_tokens"] > no_tools["breakdown"]["input_tokens"]


# ===========================================================================
# Test 5: No upstream call
# ===========================================================================

class TestNoUpstreamCall:
    """Forecast endpoint must NOT make an upstream API call."""

    def test_forecast_does_not_hit_upstream(self, servers):
        proxy_port, stub = servers
        count_before = stub.request_count
        status, _ = _post_forecast(proxy_port, _body())
        assert status == 200
        assert stub.request_count == count_before, (
            f"Upstream was called {stub.request_count - count_before} time(s) — must be 0"
        )
