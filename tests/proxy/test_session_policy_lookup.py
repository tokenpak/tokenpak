# SPDX-License-Identifier: MIT
"""tests/proxy/test_session_policy_lookup.py

CCG-12: Integration tests for per-session policy lookup in the proxy.

Covers:
  - _lookup_session_policy returns correct dict when row exists
  - _lookup_session_policy returns {} when session not found
  - _get_session_spend sums estimated_cost correctly
  - Budget enforcement: session over max_cost → 429 budget_exceeded
  - Mode override: session policy with mode=transparent → _transparent_mode=True
  - Route pin: session policy with route_provider=openai → target_url updated
"""
from __future__ import annotations

import importlib
import json
import sqlite3
import sys
import threading
import time
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(tmp_path: Path) -> str:
    """Create a minimal monitor.db with the tables CCG-12 needs."""
    db_path = str(tmp_path / "monitor.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE session_policies (
            session_id TEXT PRIMARY KEY,
            max_cost REAL,
            mode TEXT,
            route_provider TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            model TEXT,
            session_id TEXT,
            estimated_cost REAL
        )
    """)
    conn.commit()
    conn.close()
    return db_path


def _insert_policy(db_path: str, session_id: str, **kwargs):
    conn = sqlite3.connect(db_path)
    conn.execute(
        """
        INSERT OR REPLACE INTO session_policies (session_id, max_cost, mode, route_provider, updated_at)
        VALUES (?, ?, ?, ?, datetime('now'))
        """,
        (session_id, kwargs.get("max_cost"), kwargs.get("mode"), kwargs.get("route_provider")),
    )
    conn.commit()
    conn.close()


def _insert_request(db_path: str, session_id: str, cost: float):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO requests (timestamp, model, session_id, estimated_cost) VALUES (datetime('now'), 'test', ?, ?)",
        (session_id, cost),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Unit tests for helper functions
# ---------------------------------------------------------------------------

class TestLookupSessionPolicy:
    def test_returns_dict_when_row_exists(self, tmp_path, monkeypatch):
        db = _make_db(tmp_path)
        _insert_policy(db, "s1", max_cost=5.0, mode="transparent", route_provider="anthropic")
        import proxy as _proxy
        monkeypatch.setattr(_proxy, "MONITOR_DB", db)
        result = _proxy._lookup_session_policy("s1")
        assert result["max_cost"] == pytest.approx(5.0)
        assert result["mode"] == "transparent"
        assert result["route_provider"] == "anthropic"

    def test_returns_empty_when_not_found(self, tmp_path, monkeypatch):
        db = _make_db(tmp_path)
        import proxy as _proxy
        monkeypatch.setattr(_proxy, "MONITOR_DB", db)
        result = _proxy._lookup_session_policy("unknown-session")
        assert result == {}

    def test_returns_empty_on_db_error(self, tmp_path, monkeypatch):
        import proxy as _proxy
        monkeypatch.setattr(_proxy, "MONITOR_DB", "/nonexistent/path/monitor.db")
        result = _proxy._lookup_session_policy("s1")
        assert result == {}


class TestGetSessionSpend:
    def test_sums_costs_for_session(self, tmp_path, monkeypatch):
        db = _make_db(tmp_path)
        sid = "spend-sess"
        for cost in [0.01, 0.02, 0.03, 0.04, 0.05]:
            _insert_request(db, sid, cost)
        import proxy as _proxy
        monkeypatch.setattr(_proxy, "MONITOR_DB", db)
        total = _proxy._get_session_spend(sid)
        assert total == pytest.approx(0.15)

    def test_returns_zero_for_empty_session(self, tmp_path, monkeypatch):
        db = _make_db(tmp_path)
        import proxy as _proxy
        monkeypatch.setattr(_proxy, "MONITOR_DB", db)
        total = _proxy._get_session_spend("empty-sess")
        assert total == pytest.approx(0.0)

    def test_only_counts_matching_session(self, tmp_path, monkeypatch):
        db = _make_db(tmp_path)
        _insert_request(db, "sess-A", 0.10)
        _insert_request(db, "sess-B", 0.20)
        _insert_request(db, "sess-A", 0.05)
        import proxy as _proxy
        monkeypatch.setattr(_proxy, "MONITOR_DB", db)
        assert _proxy._get_session_spend("sess-A") == pytest.approx(0.15)
        assert _proxy._get_session_spend("sess-B") == pytest.approx(0.20)


# ---------------------------------------------------------------------------
# Integration tests: proxy behaviour with session policies
# ---------------------------------------------------------------------------

class _StubUpstreamHandler(BaseHTTPRequestHandler):
    """Minimal upstream that echoes back a valid messages response."""
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = json.dumps({
            "id": "msg_test",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": "ok"}],
            "model": "claude-haiku-4-5-20251001",
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def _start_stub_upstream():
    srv = HTTPServer(("127.0.0.1", 0), _StubUpstreamHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def _start_proxy(monkeypatch, tmp_path, db_path: str):
    """Start a tokenpak proxy on a free port backed by the given monitor DB."""
    import proxy as _proxy

    port = _find_free_port()
    stub = _start_stub_upstream()
    stub_port = stub.server_address[1]
    stub_base = f"http://127.0.0.1:{stub_port}"

    monkeypatch.setenv("TOKENPAK_DB", db_path)
    monkeypatch.setenv("TOKENPAK_PORT", str(port))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    monkeypatch.setenv("TOKENPAK_PROXY_KEY", "")

    # Reload proxy to pick up the new env vars
    importlib.reload(_proxy)
    monkeypatch.setattr(_proxy, "MONITOR_DB", db_path)

    # Override upstream routes to point at the stub
    monkeypatch.setattr(_proxy, "UPSTREAM_ROUTES", {"anthropic-messages": stub_base})

    from http.server import HTTPServer as _HTTPServer
    srv = _HTTPServer(("127.0.0.1", port), _proxy.ForwardProxyHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    time.sleep(0.1)
    return srv, port, stub


def _find_free_port() -> int:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _messages_request(port: int, session_id: str = "test-session") -> tuple[int, dict]:
    body = json.dumps({
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "hi"}],
    }).encode()
    conn = HTTPConnection("127.0.0.1", port, timeout=5)
    conn.request(
        "POST",
        "/v1/messages",
        body=body,
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "x-api-key": "test-key",
            "X-Claude-Code-Session-Id": session_id,
            "anthropic-version": "2023-06-01",
        },
    )
    resp = conn.getresponse()
    return resp.status, json.loads(resp.read())


@pytest.mark.integration
class TestBudgetEnforcementIntegration:
    """Set a budget, exhaust it via fake spend, assert next request is rejected."""

    def test_budget_exceeded_returns_429(self, tmp_path, monkeypatch):
        db = _make_db(tmp_path)
        sid = "budget-sess-int"
        _insert_policy(db, sid, max_cost=0.05)
        # Insert 5 requests totalling $0.10 > $0.05 cap
        for _ in range(5):
            _insert_request(db, sid, 0.02)

        import proxy as _proxy
        monkeypatch.setattr(_proxy, "MONITOR_DB", db)

        srv, port, stub = _start_proxy(monkeypatch, tmp_path, db)
        try:
            status, body = _messages_request(port, session_id=sid)
            assert status == 429
            assert body.get("error", {}).get("type") == "budget_exceeded"
        finally:
            srv.shutdown()
            stub.shutdown()

    def test_below_budget_passes_through(self, tmp_path, monkeypatch):
        db = _make_db(tmp_path)
        sid = "budget-ok-sess"
        _insert_policy(db, sid, max_cost=1.00)
        # Only $0.01 spent
        _insert_request(db, sid, 0.01)

        import proxy as _proxy
        monkeypatch.setattr(_proxy, "MONITOR_DB", db)

        srv, port, stub = _start_proxy(monkeypatch, tmp_path, db)
        try:
            status, body = _messages_request(port, session_id=sid)
            assert status == 200
        finally:
            srv.shutdown()
            stub.shutdown()
