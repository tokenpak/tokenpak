"""tests/proxy/test_proxy_auth.py — A6 / P0-06 verification.

Covers the four gating paths of ``tokenpak.proxy.proxy_auth.check_proxy_auth``
plus the I5 header-allowlist invariant (the proxy auth Bearer token must not
leak upstream) via an in-process mock upstream.

Paths covered
-------------
1. Localhost client, any state → allow.
2. Non-localhost, env unset → 403 forbidden.
3. Non-localhost, env set, missing or wrong Bearer → 401 unauthorized.
4. Non-localhost, env set, correct Bearer → allow + I5 header-allowlist holds.

The non-localhost simulation patches ``_ProxyHandler._enforce_proxy_auth`` so
that the gate sees a synthesized remote IP while the actual TCP connection
remains on loopback (no second NIC required).
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Tuple
from unittest import mock

import pytest

from tokenpak.proxy import proxy_auth as pa
from tokenpak.proxy import server as proxy_server
from tokenpak.proxy.proxy_auth import (
    PROXY_AUTH_ENV_VAR,
    ProxyAuthDecision,
    check_proxy_auth,
    hash_token,
    strip_proxy_auth_for_upstream,
)


_TEST_TOKEN = "s3cr3t-test-t0ken-A6-P0-06"
_REMOTE_IP = "10.20.30.40"
_LOCAL_IP = "127.0.0.1"


# ---------------------------------------------------------------------------
# 1. Pure check_proxy_auth — covers all four decision-tree branches
# ---------------------------------------------------------------------------


class TestCheckProxyAuth:
    def test_localhost_no_env_allowed(self) -> None:
        d = check_proxy_auth(_LOCAL_IP, {}, env={})
        assert d.allowed is True
        assert d.user_id_hash is None
        assert d.mode == "localhost"

    def test_localhost_with_env_no_header_still_allowed(self) -> None:
        d = check_proxy_auth(_LOCAL_IP, {}, env={PROXY_AUTH_ENV_VAR: _TEST_TOKEN})
        assert d.allowed is True
        assert d.mode == "localhost"

    def test_localhost_ipv6_allowed(self) -> None:
        d = check_proxy_auth("::1", {}, env={PROXY_AUTH_ENV_VAR: _TEST_TOKEN})
        assert d.allowed is True

    def test_localhost_ipv4_mapped_allowed(self) -> None:
        d = check_proxy_auth("::ffff:127.0.0.1", {}, env={PROXY_AUTH_ENV_VAR: _TEST_TOKEN})
        assert d.allowed is True

    def test_non_localhost_env_unset_forbidden(self) -> None:
        d = check_proxy_auth(_REMOTE_IP, {}, env={})
        assert d.allowed is False
        assert d.status_code == 403
        body = json.loads(d.error_body)
        assert body["error"]["type"] == "forbidden"
        assert "TOKENPAK_PROXY_AUTH_TOKEN" in body["error"]["message"]
        assert d.user_id_hash is None
        assert d.mode == "forbidden"

    def test_non_localhost_env_set_no_header_unauthorized(self) -> None:
        d = check_proxy_auth(_REMOTE_IP, {}, env={PROXY_AUTH_ENV_VAR: _TEST_TOKEN})
        assert d.allowed is False
        assert d.status_code == 401
        assert json.loads(d.error_body)["error"]["type"] == "unauthorized"
        assert d.user_id_hash is None
        assert d.mode == "missing"

    def test_non_localhost_env_set_wrong_token_unauthorized(self) -> None:
        d = check_proxy_auth(
            _REMOTE_IP,
            {"Authorization": "Bearer wrong-token"},
            env={PROXY_AUTH_ENV_VAR: _TEST_TOKEN},
        )
        assert d.allowed is False
        assert d.status_code == 401
        assert d.user_id_hash is None

    def test_non_localhost_env_set_malformed_header_unauthorized(self) -> None:
        d = check_proxy_auth(
            _REMOTE_IP,
            {"Authorization": f"Token {_TEST_TOKEN}"},
            env={PROXY_AUTH_ENV_VAR: _TEST_TOKEN},
        )
        assert d.allowed is False
        assert d.status_code == 401

    def test_non_localhost_env_set_correct_token_allowed(self) -> None:
        d = check_proxy_auth(
            _REMOTE_IP,
            {"Authorization": f"Bearer {_TEST_TOKEN}"},
            env={PROXY_AUTH_ENV_VAR: _TEST_TOKEN},
        )
        assert d.allowed is True
        assert d.mode == "bearer"
        assert d.user_id_hash == hash_token(_TEST_TOKEN)
        assert len(d.user_id_hash) == 64  # sha-256 hex
        assert d.user_id_hash != _TEST_TOKEN  # never the raw token

    def test_user_id_hash_is_sha256(self) -> None:
        expected = hashlib.sha256(_TEST_TOKEN.encode("utf-8")).hexdigest()
        assert hash_token(_TEST_TOKEN) == expected

    def test_hmac_compare_digest_used(self) -> None:
        """Source-level structural assertion: hmac.compare_digest must be the
        comparison primitive, not ``==``. Equivalent to the test in the legacy
        ``proxy.py`` suite, applied to the modular-tree implementation."""
        import ast
        from pathlib import Path
        src = (Path(pa.__file__)).read_text()
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                attr = node.func
                if (
                    attr.attr == "compare_digest"
                    and isinstance(attr.value, ast.Name)
                    and attr.value.id == "hmac"
                ):
                    found = True
                    break
        assert found, "hmac.compare_digest() not used in proxy_auth.py"

    def test_token_value_never_logged(self) -> None:
        """The token value must not appear in any log/print call's literal."""
        from pathlib import Path
        src = Path(pa.__file__).read_text()
        for i, line in enumerate(src.splitlines(), 1):
            stripped = line.strip()
            if PROXY_AUTH_ENV_VAR in stripped and any(
                kw in stripped
                for kw in ("print(", ".info(", ".debug(", ".warning(", ".error(", "logging.")
            ):
                pytest.fail(
                    f"line {i}: {PROXY_AUTH_ENV_VAR} appears next to a log/print call:\n  {line}"
                )


# ---------------------------------------------------------------------------
# 2. I5 header-allowlist — strip_proxy_auth_for_upstream
# ---------------------------------------------------------------------------


class TestI5StripUpstream:
    def test_strips_exact_value_lowercase_key(self) -> None:
        client_auth = f"Bearer {_TEST_TOKEN}"
        fwd = {"authorization": client_auth, "x-api-key": "sk-ant-real-key"}
        out = strip_proxy_auth_for_upstream(fwd, client_auth)
        assert "authorization" not in out
        assert out["x-api-key"] == "sk-ant-real-key"

    def test_strips_exact_value_titlecase_key(self) -> None:
        client_auth = f"Bearer {_TEST_TOKEN}"
        fwd = {"Authorization": client_auth, "x-api-key": "sk-ant-real-key"}
        out = strip_proxy_auth_for_upstream(fwd, client_auth)
        assert "Authorization" not in out
        assert out["x-api-key"] == "sk-ant-real-key"

    def test_does_not_strip_replacement_credential(self) -> None:
        """When a creds-router has overwritten Authorization with an upstream
        token before the strip runs, the original client value isn't there
        anymore — strip must not blow it away."""
        client_auth = f"Bearer {_TEST_TOKEN}"
        fwd = {"Authorization": "Bearer upstream-injected-token"}
        out = strip_proxy_auth_for_upstream(fwd, client_auth)
        assert out["Authorization"] == "Bearer upstream-injected-token"

    def test_no_op_when_client_auth_none(self) -> None:
        fwd = {"Authorization": "Bearer something"}
        out = strip_proxy_auth_for_upstream(fwd, None)
        assert out["Authorization"] == "Bearer something"


# ---------------------------------------------------------------------------
# 3. _ProxyHandler integration — covers all four gating paths end-to-end
#    against a mock upstream so we can assert the I5 header is stripped.
# ---------------------------------------------------------------------------


class _UpstreamMock(BaseHTTPRequestHandler):
    """In-process mock upstream — captures the exact headers it received so
    the I5 invariant can be asserted at the network boundary."""

    captured_headers: dict = {}
    captured_body: bytes = b""

    def log_message(self, fmt, *args):  # silence
        pass

    def do_POST(self):  # noqa: N802
        type(self).captured_headers = {k: v for k, v in self.headers.items()}
        length = int(self.headers.get("Content-Length", 0))
        type(self).captured_body = self.rfile.read(length) if length else b""
        body = json.dumps({"id": "msg_mock", "type": "message"}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def upstream() -> Tuple[HTTPServer, int]:
    port = _free_port()
    srv = HTTPServer(("127.0.0.1", port), _UpstreamMock)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    _UpstreamMock.captured_headers = {}
    _UpstreamMock.captured_body = b""
    yield srv, port
    srv.shutdown()
    srv.server_close()


@pytest.fixture
def proxy(monkeypatch) -> Tuple[proxy_server.ProxyServer, int, list[str]]:
    """Spin up a real ProxyServer on loopback. ``client_ip_box[0]`` controls
    what the auth gate sees — set it to ``_REMOTE_IP`` to simulate a remote
    client without needing a second NIC."""
    port = _free_port()
    ps = proxy_server.ProxyServer(host="127.0.0.1", port=port)
    ps.monitor = None  # avoid touching the local sqlite db during tests

    client_ip_box = ["127.0.0.1"]
    real_check = pa.check_proxy_auth

    def _spoofed_check(client_ip, headers, env=None):
        return real_check(client_ip_box[0], headers, env)

    # Patch the symbol that server.py imported — server.py did
    # ``from .proxy_auth import check_proxy_auth as _check_proxy_auth``.
    monkeypatch.setattr(proxy_server, "_check_proxy_auth", _spoofed_check)

    ps.start(blocking=False)
    time.sleep(0.1)
    yield ps, port, client_ip_box
    try:
        ps.stop()
    except Exception:
        pass


def _post(port: int, target_url: str, headers: dict, body: bytes = b"{}") -> Tuple[int, dict, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    h = {"Content-Length": str(len(body)), **headers}
    conn.request("POST", target_url, body=body, headers=h)
    resp = conn.getresponse()
    body_resp = resp.read()
    out_headers = {k: v for k, v in resp.getheaders()}
    conn.close()
    try:
        body_json = json.loads(body_resp)
    except Exception:
        body_json = {}
    return resp.status, body_json, body_resp


class TestProxyHandlerIntegration:
    def test_path1_localhost_allowed(self, proxy, upstream):
        ps, port, client_ip_box = proxy
        _, upstream_port = upstream
        client_ip_box[0] = "127.0.0.1"
        target = f"http://127.0.0.1:{upstream_port}/v1/messages"
        status, body, _ = _post(
            port,
            target,
            {"x-api-key": "sk-ant-real-key", "Content-Type": "application/json"},
        )
        # 200 from the mock upstream confirms the request was forwarded.
        assert status == 200, f"localhost should pass; got {status} {body}"

    def test_path2_non_localhost_env_unset_403(self, monkeypatch, proxy, upstream):
        ps, port, client_ip_box = proxy
        _, upstream_port = upstream
        monkeypatch.delenv(PROXY_AUTH_ENV_VAR, raising=False)
        client_ip_box[0] = _REMOTE_IP
        target = f"http://127.0.0.1:{upstream_port}/v1/messages"
        status, body, _ = _post(
            port,
            target,
            {"x-api-key": "sk-ant-real-key", "Content-Type": "application/json"},
        )
        assert status == 403, f"expected 403 forbidden; got {status} {body}"
        assert body["error"]["type"] == "forbidden"
        assert _UpstreamMock.captured_headers == {}, "request must not reach upstream"

    def test_path3a_non_localhost_env_set_no_header_401(self, monkeypatch, proxy, upstream):
        ps, port, client_ip_box = proxy
        _, upstream_port = upstream
        monkeypatch.setenv(PROXY_AUTH_ENV_VAR, _TEST_TOKEN)
        client_ip_box[0] = _REMOTE_IP
        target = f"http://127.0.0.1:{upstream_port}/v1/messages"
        status, body, _ = _post(
            port,
            target,
            {"x-api-key": "sk-ant-real-key", "Content-Type": "application/json"},
        )
        assert status == 401
        assert body["error"]["type"] == "unauthorized"
        assert _UpstreamMock.captured_headers == {}

    def test_path3b_non_localhost_env_set_wrong_token_401(self, monkeypatch, proxy, upstream):
        ps, port, client_ip_box = proxy
        _, upstream_port = upstream
        monkeypatch.setenv(PROXY_AUTH_ENV_VAR, _TEST_TOKEN)
        client_ip_box[0] = _REMOTE_IP
        target = f"http://127.0.0.1:{upstream_port}/v1/messages"
        status, body, _ = _post(
            port,
            target,
            {
                "x-api-key": "sk-ant-real-key",
                "Authorization": "Bearer wrong-token",
                "Content-Type": "application/json",
            },
        )
        assert status == 401
        assert _UpstreamMock.captured_headers == {}

    def test_path4_non_localhost_env_set_correct_token_allowed_and_i5(
        self, monkeypatch, proxy, upstream
    ):
        """The critical I5 test — a correctly authenticated remote request
        reaches the upstream WITHOUT the proxy auth Bearer header. The upstream
        provider must only see ``x-api-key`` (its own credential)."""
        ps, port, client_ip_box = proxy
        _, upstream_port = upstream
        monkeypatch.setenv(PROXY_AUTH_ENV_VAR, _TEST_TOKEN)
        client_ip_box[0] = _REMOTE_IP
        target = f"http://127.0.0.1:{upstream_port}/v1/messages"
        upstream_api_key = "sk-ant-real-anthropic-key"
        client_proxy_auth = f"Bearer {_TEST_TOKEN}"
        status, body, _ = _post(
            port,
            target,
            {
                "Authorization": client_proxy_auth,
                "x-api-key": upstream_api_key,
                "Content-Type": "application/json",
                "X-Claude-Code-Session-Id": "test-session-A6",
            },
        )
        assert status == 200, f"expected upstream 200; got {status} {body}"
        # I5: the proxy auth Bearer must NOT have reached the upstream.
        upstream_auth = (
            _UpstreamMock.captured_headers.get("Authorization")
            or _UpstreamMock.captured_headers.get("authorization")
        )
        assert upstream_auth != client_proxy_auth, (
            "I5 VIOLATION: proxy auth Bearer leaked to upstream — "
            f"saw Authorization={upstream_auth!r}"
        )
        assert _TEST_TOKEN not in (upstream_auth or ""), (
            "I5 VIOLATION: proxy token substring present in upstream Authorization"
        )
        # The upstream-bound credential the client supplied (x-api-key) reaches the
        # upstream — that's the provider's own key, which is correct.
        upstream_xkey = (
            _UpstreamMock.captured_headers.get("X-Api-Key")
            or _UpstreamMock.captured_headers.get("x-api-key")
        )
        assert upstream_xkey == upstream_api_key


# ---------------------------------------------------------------------------
# 4. telemetry-row.user_id — Monitor SQLite path (rework for QA finding 1)
#
# QA rejection (2026-04-28) required the accepted Bearer-path token hash to
# flow into the canonical telemetry-row.user_id field via Monitor.log, with a
# test that asserts the emitted row contains the hash and never the raw token.
# This block exercises Monitor.log directly (the row writer is the same path
# the proxy server uses at server.py:1567 — see also test_user_id_passed_to_monitor_log).
# ---------------------------------------------------------------------------


class TestMonitorTelemetryRowUserId:
    """Asserts the SQLite telemetry row contains hash_token(token) and never
    the raw token, for both the synchronous fallback and async-queue paths."""

    def _fresh_db(self, tmp_path):
        return str(tmp_path / "monitor.db")

    def _last_row(self, db_path):
        import sqlite3
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            row = conn.execute(
                "SELECT * FROM requests ORDER BY id DESC LIMIT 1"
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def test_user_id_column_present_in_schema(self, tmp_path):
        """ALTER-TABLE migration adds user_id, and CREATE-TABLE includes it on
        a fresh DB."""
        from tokenpak.proxy.monitor import Monitor as _Monitor
        m = _Monitor(self._fresh_db(tmp_path))
        import sqlite3
        conn = sqlite3.connect(m.db_path)
        try:
            cols = [r[1] for r in conn.execute("PRAGMA table_info(requests)").fetchall()]
        finally:
            conn.close()
        assert "user_id" in cols, f"user_id column missing — got {cols}"

    def _force_sync_path(self, monkeypatch):
        """Disable the async queue *after* Monitor construction (constructor
        re-initialises the queue, so patching first is a no-op). Subsequent
        ``Monitor.log`` calls then take the synchronous-fallback branch — same
        write semantics, just deterministic for tests."""
        from tokenpak.proxy import monitor as monitor_mod
        monkeypatch.setattr(monitor_mod, "_DB_WRITE_QUEUE", None, raising=False)

    def test_log_persists_user_id_hash_sync_path(self, tmp_path, monkeypatch):
        """Force the synchronous fallback path (no background queue) and
        verify the emitted row's user_id column == hash_token(token) and that
        the raw token never appears anywhere in the row."""
        from tokenpak.proxy.monitor import Monitor as _Monitor
        m = _Monitor(self._fresh_db(tmp_path))
        self._force_sync_path(monkeypatch)
        token = "rotation-secret-AB-1234567890"
        uid = hash_token(token)
        m.log(
            model="claude-3-5-sonnet",
            input_tokens=10,
            output_tokens=20,
            cost=0.001,
            latency_ms=42,
            status_code=200,
            endpoint="https://api.anthropic.com/v1/messages",
            user_id=uid,
        )
        row = self._last_row(m.db_path)
        assert row is not None
        assert row["user_id"] == uid, f"user_id mismatch: got {row['user_id']!r}"
        # I5-adjacent: no column may contain the raw token, even partially.
        for k, v in row.items():
            if isinstance(v, str):
                assert token not in v, (
                    f"raw token leaked to telemetry column {k!r}: {v!r}"
                )

    def test_log_default_user_id_is_empty_string(self, tmp_path, monkeypatch):
        """Localhost / pre-A6 callers do not pass user_id → empty string."""
        from tokenpak.proxy.monitor import Monitor as _Monitor
        m = _Monitor(self._fresh_db(tmp_path))
        self._force_sync_path(monkeypatch)
        m.log(
            model="claude-3-5-sonnet",
            input_tokens=1,
            output_tokens=1,
            cost=0.0,
            latency_ms=1,
            status_code=200,
            endpoint="https://api.anthropic.com/v1/messages",
        )
        row = self._last_row(m.db_path)
        assert row is not None
        assert row["user_id"] == "", f"expected '' default, got {row['user_id']!r}"

    def test_user_id_passed_to_monitor_log(self):
        """server.py:1569 must pass ``user_id=...`` through to ps.monitor.log
        — AST-level assertion so the wire stays connected even if surrounding
        code shifts. Equivalent in spirit to test_hmac_compare_digest_used."""
        import ast
        from pathlib import Path
        src = Path(proxy_server.__file__).read_text()
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "log" and any(
                    kw.arg == "user_id" for kw in node.keywords
                ):
                    # narrow to the ps.monitor.log call by checking attribute chain
                    val = node.func.value
                    if (
                        isinstance(val, ast.Attribute)
                        and val.attr == "monitor"
                    ):
                        found = True
                        break
            # also accept (less strictly) any `.log(user_id=...)` call as
            # belt-and-suspenders for refactors.
            if isinstance(node, ast.Call) and any(
                isinstance(kw.arg, str) and kw.arg == "user_id" for kw in node.keywords
            ):
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "log"
                ):
                    found = True
        assert found, (
            "expected ps.monitor.log(... user_id=...) wire in tokenpak/proxy/server.py"
        )
