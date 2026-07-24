"""
Tests for TokenPak Connection Pool

Covers:
- PoolConfig defaults and env-var overrides
- ConnectionPool construction and client lifecycle
- Per-netloc client isolation (one client per provider)
- HTTP/2 availability detection
- Metrics counters (total_requests, reuse_rate)
- Thread-safety (concurrent client access)
- Pool close releases all clients
- Global singleton pool creation and reset
- ProxyServer exposes _connection_pool attribute
- ProxyServer health() includes connection_pool keys
- Non-streaming request via pool (live mock server)
- Streaming request via pool (live mock server)
- Connection reuse across multiple requests (same client returned)
- PoolMetrics.reuse_rate is 0.0 when no requests
- Pool survives exception during request (errors counter)
"""

from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import List
from unittest.mock import patch

import pytest

from tokenpak.proxy.connection_pool import (
    ConnectionPool,
    PoolConfig,
    PoolMetrics,
    _http2_available,
    get_global_pool,
    reset_global_pool,
)
from tokenpak.proxy.server import ProxyServer

# ---------------------------------------------------------------------------
# Minimal test HTTP server (spin up locally, never hits real APIs)
# ---------------------------------------------------------------------------


class _EchoHandler(BaseHTTPRequestHandler):
    """Simple HTTP/1.1 server that echoes back a JSON body."""

    def log_message(self, fmt, *args):
        pass  # silence output

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        _ = self.rfile.read(length)

        if "/stream" in self.path:
            # Simulate SSE response
            payload = b'data: {"type":"ping"}\n\ndata: {"type":"message_stop"}\n\n'
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
        else:
            payload = json.dumps({"ok": True, "path": self.path}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    def do_GET(self):
        payload = json.dumps({"ok": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


@pytest.fixture(scope="module")
def echo_server():
    """Start a local HTTP echo server for pool tests."""
    server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


# ---------------------------------------------------------------------------
# Test 1 — PoolConfig defaults
# ---------------------------------------------------------------------------


def test_pool_config_defaults():
    cfg = PoolConfig()
    assert cfg.max_connections == 20
    assert cfg.max_keepalive_connections == 10
    assert cfg.keepalive_expiry == 30.0
    assert cfg.connect_timeout == 10.0
    assert cfg.read_timeout == 300.0
    assert cfg.http2 is True


# ---------------------------------------------------------------------------
# Test 2 — PoolConfig.from_env() respects env vars
# ---------------------------------------------------------------------------


def test_pool_config_from_env():
    env = {
        "TOKENPAK_POOL_MAX_CONNECTIONS": "50",
        "TOKENPAK_POOL_MAX_KEEPALIVE": "25",
        "TOKENPAK_POOL_KEEPALIVE_EXPIRY": "60",
        "TOKENPAK_HTTP2": "0",
    }
    with patch.dict(os.environ, env):
        cfg = PoolConfig.from_env()
    assert cfg.max_connections == 50
    assert cfg.max_keepalive_connections == 25
    assert cfg.keepalive_expiry == 60.0
    assert cfg.http2 is False


# ---------------------------------------------------------------------------
# Test 3 — PoolConfig.from_env() uses defaults when vars absent
# ---------------------------------------------------------------------------


def test_pool_config_from_env_defaults():
    clean = {
        k: ""
        for k in [
            "TOKENPAK_POOL_MAX_CONNECTIONS",
            "TOKENPAK_POOL_MAX_KEEPALIVE",
            "TOKENPAK_POOL_KEEPALIVE_EXPIRY",
            "TOKENPAK_HTTP2",
        ]
    }
    env = {
        k: "20"
        if "MAX_CONN" in k
        else "10"
        if "KEEPALIVE" in k and "EX" not in k
        else "30"
        if "EX" in k
        else "1"
        for k in clean
    }
    with patch.dict(os.environ, {}, clear=False):
        # Remove the keys if present
        saved = {k: os.environ.pop(k, None) for k in clean}
        try:
            cfg = PoolConfig.from_env()
            assert cfg.max_connections == 20
            assert cfg.max_keepalive_connections == 10
            assert cfg.keepalive_expiry == 30.0
            assert cfg.http2 is True
        finally:
            for k, v in saved.items():
                if v is not None:
                    os.environ[k] = v


# ---------------------------------------------------------------------------
# Test 4 — ConnectionPool constructs without error
# ---------------------------------------------------------------------------


def test_pool_constructs():
    pool = ConnectionPool()
    assert pool is not None
    pool.close()


# ---------------------------------------------------------------------------
# Test 5 — http2_enabled reflects config and h2 availability
# ---------------------------------------------------------------------------


def test_pool_http2_enabled_when_h2_available():
    """When h2 is installed, http2_enabled follows config."""
    from tokenpak.proxy.connection_pool import _H2_AVAILABLE

    pool = ConnectionPool(PoolConfig(http2=True))
    assert pool.http2_enabled == _H2_AVAILABLE
    pool.close()


def test_pool_http2_disabled_via_config():
    pool = ConnectionPool(PoolConfig(http2=False))
    assert pool.http2_enabled is False
    pool.close()


# ---------------------------------------------------------------------------
# Test 6 — active_providers is empty before first request
# ---------------------------------------------------------------------------


def test_pool_no_providers_initially():
    pool = ConnectionPool()
    assert pool.active_providers == []
    pool.close()


# ---------------------------------------------------------------------------
# Test 7 — active_providers populated after first request
# ---------------------------------------------------------------------------


def test_pool_providers_after_request(echo_server):
    pool = ConnectionPool(PoolConfig(http2=False))  # test server is HTTP/1.1
    resp = pool.request("GET", echo_server + "/ping")
    assert resp.status_code == 200
    providers = pool.active_providers
    assert len(providers) == 1
    assert "127.0.0.1" in providers[0]
    pool.close()


# ---------------------------------------------------------------------------
# Test 8 — same client returned for same netloc (connection reuse)
# ---------------------------------------------------------------------------


def test_pool_same_client_per_netloc(echo_server):
    pool = ConnectionPool(PoolConfig(http2=False))
    pool._get_client("127.0.0.1:99")  # prime
    client_a = pool._get_client("127.0.0.1:99")
    client_b = pool._get_client("127.0.0.1:99")
    assert client_a is client_b, "Expected identical client for same netloc"
    pool.close()


# ---------------------------------------------------------------------------
# Test 9 — different clients for different netlocs
# ---------------------------------------------------------------------------


def test_pool_different_clients_per_netloc():
    pool = ConnectionPool(PoolConfig(http2=False))
    client_a = pool._get_client("api.anthropic.com")
    client_b = pool._get_client("api.openai.com")
    assert client_a is not client_b
    pool.close()


# ---------------------------------------------------------------------------
# Test 10 — metrics.total_requests increments
# ---------------------------------------------------------------------------


def test_pool_metrics_total_requests(echo_server):
    pool = ConnectionPool(PoolConfig(http2=False))
    before = pool.metrics()["total_requests"]
    pool.request("GET", echo_server + "/")
    pool.request("GET", echo_server + "/")
    after = pool.metrics()["total_requests"]
    assert after == before + 2
    pool.close()


# ---------------------------------------------------------------------------
# Test 11 — metrics.reuse_rate is 0.0 with no requests
# ---------------------------------------------------------------------------


def test_pool_metrics_reuse_rate_zero_initially():
    pool = ConnectionPool()
    m = pool.metrics()
    assert m["reuse_rate"] == 0.0
    pool.close()


# ---------------------------------------------------------------------------
# Test 12 — reset_metrics clears counters
# ---------------------------------------------------------------------------


def test_pool_reset_metrics(echo_server):
    pool = ConnectionPool(PoolConfig(http2=False))
    pool.request("GET", echo_server + "/")
    pool.reset_metrics()
    m = pool.metrics()
    assert m["total_requests"] == 0
    assert m["reused_connections"] == 0
    assert m["new_connections"] == 0
    assert m["errors"] == 0
    pool.close()


# ---------------------------------------------------------------------------
# Test 13 — non-streaming request returns correct status and body
# ---------------------------------------------------------------------------


def test_pool_non_streaming_request(echo_server):
    pool = ConnectionPool(PoolConfig(http2=False))
    resp = pool.request(
        "POST",
        echo_server + "/v1/messages",
        content=b'{"test": true}',
        headers={"Content-Type": "application/json"},
    )
    assert resp.status_code == 200
    data = json.loads(resp.content)
    assert data["ok"] is True
    pool.close()


# ---------------------------------------------------------------------------
# Test 14 — streaming request yields chunks
# ---------------------------------------------------------------------------


def test_pool_streaming_request(echo_server):
    pool = ConnectionPool(PoolConfig(http2=False))
    chunks: List[bytes] = []
    with pool.stream(
        "POST",
        echo_server + "/stream",
        content=b'{"stream": true}',
        headers={"Content-Type": "application/json"},
    ) as resp:
        assert resp.status_code == 200
        for chunk in resp.iter_bytes(chunk_size=64):
            chunks.append(chunk)
    assert len(chunks) > 0
    combined = b"".join(chunks)
    assert b"data:" in combined
    pool.close()


# ---------------------------------------------------------------------------
# Test 15 — pool.close() releases all clients without error
# ---------------------------------------------------------------------------


def test_pool_close_idempotent(echo_server):
    pool = ConnectionPool(PoolConfig(http2=False))
    pool.request("GET", echo_server + "/")
    assert len(pool.active_providers) == 1
    pool.close()
    # After close, internal clients dict should be empty
    assert pool.active_providers == []
    # Second close should not raise
    pool.close()


# ---------------------------------------------------------------------------
# Test 16 — thread-safety: concurrent requests from multiple threads
# ---------------------------------------------------------------------------


def test_pool_thread_safety(echo_server):
    pool = ConnectionPool(PoolConfig(http2=False, max_connections=10))
    errors: List[Exception] = []
    results: List[int] = []

    def worker():
        try:
            resp = pool.request("GET", echo_server + "/")
            results.append(resp.status_code)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert not errors, f"Thread errors: {errors}"
    assert all(s == 200 for s in results)
    pool.close()


# ---------------------------------------------------------------------------
# Test 17 — PoolMetrics.reuse_rate formula
# ---------------------------------------------------------------------------


def test_pool_metrics_reuse_rate_formula():
    m = PoolMetrics()
    assert m.reuse_rate == 0.0  # no requests

    m.total_requests = 10
    m.reused_connections = 8
    assert abs(m.reuse_rate - 0.8) < 0.001


# ---------------------------------------------------------------------------
# Test 18 — PoolMetrics.to_dict() contains all keys
# ---------------------------------------------------------------------------


def test_pool_metrics_to_dict_keys():
    m = PoolMetrics(total_requests=5, reused_connections=3, new_connections=2, errors=0)
    d = m.to_dict()
    assert set(d.keys()) == {
        "total_requests",
        "reused_connections",
        "new_connections",
        "errors",
        "evicted_clients",
        "reuse_rate",
    }


# ---------------------------------------------------------------------------
# Test 19 — global pool singleton
# ---------------------------------------------------------------------------


def test_global_pool_singleton():
    reset_global_pool()
    p1 = get_global_pool()
    p2 = get_global_pool()
    assert p1 is p2
    reset_global_pool()


# ---------------------------------------------------------------------------
# Test 20 — reset_global_pool creates a fresh pool on next call
# ---------------------------------------------------------------------------


def test_global_pool_reset_creates_fresh():
    reset_global_pool()
    p1 = get_global_pool()
    reset_global_pool()
    p2 = get_global_pool()
    assert p1 is not p2
    reset_global_pool()


# ---------------------------------------------------------------------------
# Test 21 — ProxyServer has _connection_pool attribute
# ---------------------------------------------------------------------------


def test_proxy_server_has_connection_pool():
    server = ProxyServer(host="127.0.0.1", port=0)
    assert hasattr(server, "_connection_pool")
    assert isinstance(server._connection_pool, ConnectionPool)


# ---------------------------------------------------------------------------
# Test 22 — ProxyServer.health() includes connection_pool section
# ---------------------------------------------------------------------------


@pytest.mark.needs_proxy
def test_proxy_server_health_includes_pool_metrics():
    server = ProxyServer(host="127.0.0.1", port=28800)
    server.start(blocking=False)
    time.sleep(0.05)
    try:
        health = server.health()
        assert "connection_pool" in health
        cp = health["connection_pool"]
        assert "http2_enabled" in cp
        assert "active_providers" in cp
        assert "total_requests" in cp
        assert "reuse_rate" in cp
    finally:
        server.stop()


# ---------------------------------------------------------------------------
# Test 23 — ProxyServer.stop() closes the pool (active_providers cleared)
# ---------------------------------------------------------------------------


def test_proxy_server_stop_closes_pool(echo_server):
    server = ProxyServer(host="127.0.0.1", port=0)
    # Manually prime the pool with one client
    server._connection_pool._get_client("api.anthropic.com")
    assert len(server._connection_pool.active_providers) == 1
    server.stop()
    assert server._connection_pool.active_providers == []


# ---------------------------------------------------------------------------
# Test 24 — errors counter increments on bad URL
# ---------------------------------------------------------------------------


def test_pool_metrics_errors_on_bad_url():
    pool = ConnectionPool(PoolConfig(http2=False, connect_timeout=0.5))
    before = pool.metrics()["errors"]
    try:
        pool.request("GET", "http://127.0.0.1:1/unreachable")
    except Exception:
        pass
    after = pool.metrics()["errors"]
    assert after > before
    pool.close()


# ---------------------------------------------------------------------------
# Test 25 — _http2_available() returns bool
# ---------------------------------------------------------------------------


def test_http2_available_returns_bool():
    result = _http2_available()
    assert isinstance(result, bool)
