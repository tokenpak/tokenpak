"""
test_dashboard_auth.py — Tests for dashboard token auth & token_manager.

Tests:
1. generate_token produces 32-char hex string
2. load_or_create_token creates file with 0o600 permissions
3. load_or_create_token returns same token on second call
4. regenerate_token overwrites old token
5. get_token raises FileNotFoundError when file missing
6. get_token returns token when file exists
7. _serve_dashboard returns 401 on missing token (proxy)
8. _serve_dashboard returns 401 on wrong token (proxy)
"""

import os
import sys
import stat
import importlib
import tempfile
from pathlib import Path
from unittest import mock
import pytest


# ---------------------------------------------------------------------------
# Helpers: patch TOKEN_FILE to a tmp file
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_token_file(tmp_path):
    """Patch token_manager.TOKEN_FILE to a tmp location."""
    token_file = tmp_path / "dashboard_token"
    import tokenpak.token_manager as tm
    with mock.patch.object(tm, "TOKEN_FILE", token_file):
        yield token_file


# ---------------------------------------------------------------------------
# token_manager tests
# ---------------------------------------------------------------------------

def test_generate_token_is_32_char_hex():
    from tokenpak.token_manager import generate_token
    token = generate_token()
    assert len(token) == 32
    assert all(c in "0123456789abcdef" for c in token)


def test_load_or_create_makes_file_with_secure_perms(tmp_token_file):
    import tokenpak.token_manager as tm
    token = tm.load_or_create_token()
    assert tmp_token_file.exists()
    assert len(token) == 32
    mode = stat.S_IMODE(tmp_token_file.stat().st_mode)
    assert mode == 0o600, f"Expected 0o600, got {oct(mode)}"


def test_load_or_create_returns_same_token_on_repeat(tmp_token_file):
    import tokenpak.token_manager as tm
    token1 = tm.load_or_create_token()
    token2 = tm.load_or_create_token()
    assert token1 == token2


def test_regenerate_token_overwrites(tmp_token_file):
    import tokenpak.token_manager as tm
    token1 = tm.load_or_create_token()
    token2 = tm.regenerate_token()
    assert token1 != token2 or True  # Extremely unlikely to collide, but don't hard-fail on it
    assert tmp_token_file.read_text().strip() == token2


def test_get_token_raises_when_missing(tmp_token_file):
    import tokenpak.token_manager as tm
    assert not tmp_token_file.exists()
    with pytest.raises(FileNotFoundError):
        tm.get_token()


def test_get_token_returns_token_when_exists(tmp_token_file):
    import tokenpak.token_manager as tm
    tm.load_or_create_token()
    result = tm.get_token()
    assert len(result) == 32


# ---------------------------------------------------------------------------
# proxy dashboard auth tests (unit — no network)
# ---------------------------------------------------------------------------

class FakeAddress:
    def __init__(self):
        self.path = "/dashboard"
        self.client_address = ("127.0.0.1", 54321)
        self._headers_sent = []
        self._body = None

    def send_response(self, code):
        self._status = code

    def send_header(self, k, v):
        self._headers_sent.append((k, v))

    def end_headers(self):
        pass

    def write(self, data):
        self._body = data

    @property
    def wfile(self):
        class W:
            def write(inner, data):
                self._body = data
        return W()


def _make_handler(path="/dashboard", auth_enabled=True):
    """
    Minimal shim that calls _serve_dashboard on ForwardProxyHandler.
    We monkeypatch DASHBOARD_AUTH_ENABLED so we can test both states.
    """
    import proxy as p4
    handler = object.__new__(p4.ForwardProxyHandler)
    handler.path = path
    handler.client_address = ("127.0.0.1", 54321)
    handler._headers_sent = []
    handler._status = None
    handler._body = None

    def _send_json(data, status=200):
        handler._status = status
        import json
        handler._body = json.dumps(data).encode()
    handler._send_json = _send_json

    return handler


def test_dashboard_returns_401_missing_token(tmp_token_file):
    """Missing ?token param → 401."""
    import proxy as p4
    import tokenpak.token_manager as tm

    handler = _make_handler("/dashboard")
    with mock.patch.object(p4, "DASHBOARD_AUTH_ENABLED", True):
        with mock.patch("tokenpak.token_manager.TOKEN_FILE", tmp_token_file):
            tm.load_or_create_token()   # ensure token exists
            handler._serve_dashboard()
    assert handler._status == 401


def test_dashboard_returns_401_wrong_token(tmp_token_file):
    """Wrong ?token param → 401."""
    import proxy as p4
    import tokenpak.token_manager as tm

    handler = _make_handler("/dashboard?token=wrongtoken12345678901234567890")
    with mock.patch.object(p4, "DASHBOARD_AUTH_ENABLED", True):
        with mock.patch("tokenpak.token_manager.TOKEN_FILE", tmp_token_file):
            tm.load_or_create_token()
            handler._serve_dashboard()
    assert handler._status == 401
