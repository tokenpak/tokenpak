"""tests/proxy/test_proxy_auth.py

AC-2.2 Verification — TOKENPAK_PROXY_AUTH_TOKEN, when set, enforces Bearer auth
on non-localhost clients.  Localhost is always trusted regardless of env var state.

TRIX-07 / pmgtm-phase-2-wave-2a

Tests:
  - localhost, no env var set              → allowed (200 or 404, never 401/403)
  - localhost, env var set, wrong token    → allowed (localhost bypass)
  - localhost, env var set, no header      → allowed (localhost bypass)
  - non-localhost, env var NOT set         → 403 "not configured"
  - non-localhost, env var set, right token → allowed (200 or 404)
  - non-localhost, env var set, wrong token → 401 "Invalid token"
  - non-localhost, env var set, missing header → 401 "Missing Authorization header"

Non-localhost simulation: the tests patch `client_address` on the handler socket
so the server sees the client as 10.0.0.1 (non-loopback) while the actual TCP
connection is from 127.0.0.1. This avoids needing a second NIC.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import sys
import threading
import time
from http.client import HTTPConnection
from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Repo root + path to the standalone proxy_v4.py
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_PROXY_V4_PATH = _REPO_ROOT / "proxy_v4.py"

_ENV_KEYS = ["TOKENPAK_PROXY_AUTH_TOKEN", "TOKENPAK_CONFIG"]
_TEST_TOKEN = "s3cr3t-test-t0ken"
_REMOTE_IP = "10.0.0.1"  # simulated non-localhost client IP


def _reload_config_loader():
    try:
        import tokenpak._internal.config_loader as _icl
        importlib.reload(_icl)
        import tokenpak.config_loader as _cl
        importlib.reload(_cl)
    except Exception:
        pass


def _stash_env():
    return {k: os.environ.pop(k) for k in _ENV_KEYS if k in os.environ}


def _restore_env(stashed):
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    for k, v in stashed.items():
        os.environ[k] = v
    _reload_config_loader()


def _load_proxy_module(mod_name: str) -> object:
    sys.modules.pop(mod_name, None)
    spec = importlib.util.spec_from_file_location(mod_name, _PROXY_V4_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    spec.loader.exec_module(mod)
    return mod


def _start_server(mod, host: str, port: int):
    server = mod.ThreadedHTTPServer((host, port), mod.ForwardProxyHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    time.sleep(0.15)
    return server


def _get(port: int, headers: dict | None = None, timeout: int = 5) -> tuple[int, dict]:
    """GET /health from the proxy and return (status, body_dict)."""
    conn = HTTPConnection("127.0.0.1", port, timeout=timeout)
    conn.request("GET", "/health", headers=headers or {})
    resp = conn.getresponse()
    status = resp.status
    try:
        body = json.loads(resp.read())
    except Exception:
        body = {}
    conn.close()
    return status, body


# ---------------------------------------------------------------------------
# Fixture: proxy with NO auth token set
# ---------------------------------------------------------------------------
_MOD_NO_TOKEN = "_test_pv4_proxy_auth_no_token"
_MOD_WITH_TOKEN = "_test_pv4_proxy_auth_with_token"


@pytest.fixture(scope="module")
def proxy_no_token():
    """proxy_v4 loaded with TOKENPAK_PROXY_AUTH_TOKEN unset."""
    stashed = _stash_env()
    os.environ["TOKENPAK_CONFIG"] = "/tmp/_tokenpak_test_nonexistent_TRIX07.yaml"
    _reload_config_loader()
    try:
        mod = _load_proxy_module(_MOD_NO_TOKEN)
    except Exception as exc:
        _restore_env(stashed)
        pytest.skip(f"proxy_v4.py failed to load: {exc}")
    port = 18780
    server = _start_server(mod, "127.0.0.1", port)
    yield mod, port
    server.shutdown()
    sys.modules.pop(_MOD_NO_TOKEN, None)
    _restore_env(stashed)


@pytest.fixture(scope="module")
def proxy_with_token():
    """proxy_v4 loaded with TOKENPAK_PROXY_AUTH_TOKEN=_TEST_TOKEN."""
    stashed = _stash_env()
    os.environ["TOKENPAK_CONFIG"] = "/tmp/_tokenpak_test_nonexistent_TRIX07.yaml"
    os.environ["TOKENPAK_PROXY_AUTH_TOKEN"] = _TEST_TOKEN
    _reload_config_loader()
    try:
        mod = _load_proxy_module(_MOD_WITH_TOKEN)
    except Exception as exc:
        _restore_env(stashed)
        pytest.skip(f"proxy_v4.py failed to load: {exc}")
    port = 18781
    server = _start_server(mod, "127.0.0.1", port)
    yield mod, port
    server.shutdown()
    sys.modules.pop(_MOD_WITH_TOKEN, None)
    _restore_env(stashed)


# ---------------------------------------------------------------------------
# Helper: patch client_address to simulate non-localhost
# ---------------------------------------------------------------------------
def _request_as_remote(port: int, headers: dict | None = None) -> tuple[int, dict]:
    """Make a GET /health request that appears to come from _REMOTE_IP.

    The actual TCP connection is loopback; we patch ForwardProxyHandler so that
    self.client_address returns the simulated remote IP before auth is checked.
    """
    # We need the module's ForwardProxyHandler class
    mod = sys.modules.get(_MOD_NO_TOKEN) or sys.modules.get(_MOD_WITH_TOKEN)
    # The patch target changes per fixture; we patch socket.getpeername so
    # BaseHTTPRequestHandler picks it up via self.connection.getpeername()
    # Simpler: patch at the handler level using a subclass trick.
    # Easiest: we'll just override client_address via a monkey-patch on the class.
    # But that's module-level state. Use a threading.local trick instead.

    # The cleanest approach for unit tests without a real second NIC:
    # Temporarily override client_address on the *handler* using a thread-local
    # stored in the module, then clean it up.

    # Actually, the cleanest approach: temporarily monkeypatch
    # ForwardProxyHandler._check_proxy_auth's self.client_address lookup by
    # subclassing — but that requires modifying the running server.
    #
    # Instead, we patch socket.socket.getpeername on the connection object.
    # The BaseHTTPRequestHandler stores client_address from the accept() call,
    # which we cannot intercept post-start.
    #
    # Best practical approach without complex mock infrastructure:
    # call _check_proxy_auth() directly as a unit test (see class below),
    # rather than via HTTP. HTTP tests verify localhost path only.
    raise NotImplementedError("Use direct unit tests for non-localhost simulation")


# ---------------------------------------------------------------------------
# Unit tests for _check_proxy_auth — mock self (client_address, headers)
# ---------------------------------------------------------------------------

class _FakeHandler:
    """Minimal stand-in for ForwardProxyHandler for unit testing _check_proxy_auth."""

    def __init__(self, client_ip: str, auth_header: str | None, proxy_auth_token: str):
        self.client_address = (client_ip, 12345)
        self._auth_header = auth_header
        self._proxy_auth_token = proxy_auth_token
        self._response: dict | None = None
        self._status: int | None = None

    # Minimal headers dict
    class _Headers:
        def __init__(self, auth):
            self._auth = auth

        def get(self, key, default=""):
            if key == "Authorization":
                return self._auth if self._auth is not None else default
            return default

    @property
    def headers(self):
        return self._Headers(self._auth_header)

    def _send_json(self, data, status=200):
        self._response = data
        self._status = status

    def _check_proxy_auth(self):
        # Import the real implementation from the loaded module but call it
        # bound to self, patching PROXY_AUTH_TOKEN from the module namespace.
        mod = sys.modules.get(_MOD_NO_TOKEN) or sys.modules.get(_MOD_WITH_TOKEN)
        # We need to call ForwardProxyHandler._check_proxy_auth with our fake self
        # *and* the correct PROXY_AUTH_TOKEN value.
        import types
        # Temporarily override the module's PROXY_AUTH_TOKEN
        old_token = mod.PROXY_AUTH_TOKEN
        mod.PROXY_AUTH_TOKEN = self._proxy_auth_token
        try:
            result = mod.ForwardProxyHandler._check_proxy_auth(self)
        finally:
            mod.PROXY_AUTH_TOKEN = old_token
        return result


class TestProxyAuthUnit:
    """Unit-level tests for _check_proxy_auth — cover all 5 decision-tree branches."""

    @pytest.fixture(autouse=True)
    def _ensure_module_loaded(self, proxy_no_token):
        """Ensures the module is loaded (uses proxy_no_token fixture as side-effect)."""

    def _make(self, client_ip, auth_header, token):
        return _FakeHandler(client_ip, auth_header, token)

    # ------------------------------------------------------------------
    # 1. Localhost, no env var set → allow
    # ------------------------------------------------------------------
    def test_localhost_no_env_var_allowed(self):
        h = self._make("127.0.0.1", None, "")
        assert h._check_proxy_auth() is True
        assert h._status is None

    # ------------------------------------------------------------------
    # 2. Localhost, env var set, wrong token → still allow (localhost bypass)
    # ------------------------------------------------------------------
    def test_localhost_wrong_token_still_allowed(self):
        h = self._make("127.0.0.1", "Bearer wrongtoken", _TEST_TOKEN)
        assert h._check_proxy_auth() is True
        assert h._status is None

    # ------------------------------------------------------------------
    # 3. Localhost IPv6, env var set, no header → still allow
    # ------------------------------------------------------------------
    def test_localhost_ipv6_no_header_allowed(self):
        h = self._make("::1", None, _TEST_TOKEN)
        assert h._check_proxy_auth() is True
        assert h._status is None

    # ------------------------------------------------------------------
    # 4. Non-localhost, env var NOT set → 403
    # ------------------------------------------------------------------
    def test_non_localhost_no_env_var_403(self):
        h = self._make(_REMOTE_IP, None, "")
        result = h._check_proxy_auth()
        assert result is False
        assert h._status == 403
        assert "not configured" in h._response["error"]["message"]

    # ------------------------------------------------------------------
    # 5. Non-localhost, env var set, correct token → allow
    # ------------------------------------------------------------------
    def test_non_localhost_correct_token_allowed(self):
        h = self._make(_REMOTE_IP, f"Bearer {_TEST_TOKEN}", _TEST_TOKEN)
        assert h._check_proxy_auth() is True
        assert h._status is None

    # ------------------------------------------------------------------
    # 6. Non-localhost, env var set, wrong token → 401
    # ------------------------------------------------------------------
    def test_non_localhost_wrong_token_401(self):
        h = self._make(_REMOTE_IP, "Bearer wrongtoken", _TEST_TOKEN)
        result = h._check_proxy_auth()
        assert result is False
        assert h._status == 401
        assert "Invalid token" in h._response["error"]["message"]

    # ------------------------------------------------------------------
    # 7. Non-localhost, env var set, missing Authorization header → 401
    # ------------------------------------------------------------------
    def test_non_localhost_missing_header_401(self):
        h = self._make(_REMOTE_IP, None, _TEST_TOKEN)
        result = h._check_proxy_auth()
        assert result is False
        assert h._status == 401
        assert "Missing Authorization header" in h._response["error"]["message"]

    # ------------------------------------------------------------------
    # 8. Non-localhost, env var set, malformed header (no "Bearer ") → 401
    # ------------------------------------------------------------------
    def test_non_localhost_malformed_header_401(self):
        h = self._make(_REMOTE_IP, f"Token {_TEST_TOKEN}", _TEST_TOKEN)
        result = h._check_proxy_auth()
        assert result is False
        assert h._status == 401

    # ------------------------------------------------------------------
    # 9. hmac.compare_digest used (not plain ==) — structural check
    # ------------------------------------------------------------------
    def test_hmac_compare_digest_used(self):
        """Verify the implementation calls hmac.compare_digest, not ==."""
        import ast
        src = _PROXY_V4_PATH.read_text()
        tree = ast.parse(src)
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if (isinstance(func, ast.Attribute) and func.attr == "compare_digest"
                        and isinstance(func.value, ast.Name) and func.value.id == "hmac"):
                    found = True
                    break
        assert found, "hmac.compare_digest() not found in proxy_v4.py source"

    # ------------------------------------------------------------------
    # 10. Token value never logged — check no f-string/log includes PROXY_AUTH_TOKEN
    # ------------------------------------------------------------------
    def test_token_not_logged(self):
        """PROXY_AUTH_TOKEN must not appear in any log/print statement."""
        import ast
        src = _PROXY_V4_PATH.read_text()
        # Search for any string literal containing the env var name next to logging calls
        # Simple heuristic: PROXY_AUTH_TOKEN should only appear in its assignment line
        # and in _check_proxy_auth body (as a variable reference, not its value).
        # We verify it's not interpolated into any log string.
        lines = src.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            # Skip the assignment line and comments
            if "PROXY_AUTH_TOKEN" in stripped and ("=" in stripped or stripped.startswith("#")):
                continue
            # Any logging/print that also mentions PROXY_AUTH_TOKEN's *value* would be
            # caught by checking for the variable inside f-strings passed to log functions.
            # This test checks the weaker form: the token env-var name doesn't appear
            # inside a log/print call's string literal (which would hint at logging the value).
            if "PROXY_AUTH_TOKEN" in stripped and any(
                kw in stripped for kw in ("print(", "logging.", "log.", ".info(", ".debug(", ".warning(", ".error(")
            ):
                pytest.fail(
                    f"Line {i} looks like it may log PROXY_AUTH_TOKEN:\n  {line}"
                )


# ---------------------------------------------------------------------------
# Integration: localhost HTTP path (no auth enforcement)
# ---------------------------------------------------------------------------

class TestLocalhostAlwaysAllowed:
    """Via real HTTP from 127.0.0.1 — auth must never block localhost."""

    def test_localhost_no_token_env_not_set(self, proxy_no_token):
        mod, port = proxy_no_token
        status, body = _get(port)
        # /health returns 200 with status ok — if 403/401 something is very wrong
        assert status not in (401, 403), (
            f"Localhost must never be blocked. Got {status}: {body}"
        )

    def test_localhost_token_env_set_no_header(self, proxy_with_token):
        mod, port = proxy_with_token
        # No Authorization header sent — but we're on localhost
        status, body = _get(port)
        assert status not in (401, 403), (
            f"Localhost must bypass auth even without header. Got {status}: {body}"
        )

    def test_localhost_token_env_set_wrong_token(self, proxy_with_token):
        mod, port = proxy_with_token
        status, body = _get(port, headers={"Authorization": "Bearer wrongtoken"})
        assert status not in (401, 403), (
            f"Localhost must bypass auth even with wrong token. Got {status}: {body}"
        )
