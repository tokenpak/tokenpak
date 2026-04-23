# SPDX-License-Identifier: Apache-2.0
"""A6 (PM/GTM v2 Phase 0): proxy-level auth middleware tests.

Non-localhost access to a running `tokenpak serve` was previously ungated.
A6 adds an opt-in middleware controlled by TOKENPAK_PROXY_AUTH_TOKEN that
enforces `X-TokenPak-Auth: <token>` on non-localhost clients.

The four required paths:

  1. localhost client → allow (backwards compat).
  2. non-localhost + env unset → 403 forbidden.
  3. non-localhost + env set + missing/wrong token → 401 unauthorized.
  4. non-localhost + env set + correct token → allow; X-TokenPak-Auth is
     stripped from self.headers (I5 belt-and-suspenders).

Also asserts I5: the Authorization-equivalent proxy auth header is NOT
in PERMITTED_HEADERS_PROXY (so the SC+1 I5 invariant test will catch any
leak automatically).

Traces to v2 M-A6 (Axis A public-surface truth) per
~/vault/02_COMMAND_CENTER/initiatives/2026-04-23-tokenpak-pm-gtm-readiness-v2/.
"""

from __future__ import annotations

import io
import json
from email.message import Message
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.core.contracts.permitted_headers import PERMITTED_HEADERS_PROXY
from tokenpak.proxy.server import _ProxyHandler


def _make_handler(client_ip: str, headers: dict[str, str]) -> _ProxyHandler:
    """Build a `_ProxyHandler` instance sufficient for exercising `_auth_gate`.

    We bypass __init__ (which would try to read/write a real socket) and wire
    only the attributes `_auth_gate` + `_send_json_error` touch.
    """
    handler = _ProxyHandler.__new__(_ProxyHandler)
    handler.client_address = (client_ip, 0)

    # HTTPMessage is a subclass of email.message.Message; both respond to
    # `.get()` and `del msg[name]` which is all `_auth_gate` uses.
    msg = Message()
    for key, value in headers.items():
        msg[key] = value
    handler.headers = msg

    # `_send_json_error` writes to these.
    handler.wfile = io.BytesIO()
    # BaseHTTPRequestHandler's send_* methods need a mutable state bag;
    # replace with MagicMock so they are no-ops we can inspect afterward.
    handler.send_response = MagicMock()
    handler.send_header = MagicMock()
    handler.end_headers = MagicMock()
    return handler


def _sent_status(handler: _ProxyHandler) -> int | None:
    """Extract the integer status code passed to send_response, or None."""
    if handler.send_response.call_args is None:
        return None
    return handler.send_response.call_args.args[0]


# ---------------------------------------------------------------------------
# Path 1: localhost bypass
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("localhost_ip", ["127.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_localhost_always_allowed_even_without_env(monkeypatch, localhost_ip):
    """A6 Path 1: localhost clients bypass auth regardless of env state."""
    monkeypatch.delenv("TOKENPAK_PROXY_AUTH_TOKEN", raising=False)

    handler = _make_handler(localhost_ip, headers={})
    assert handler._auth_gate() is True
    assert handler.send_response.call_args is None, (
        "localhost must not trigger a status response from the auth gate"
    )


def test_localhost_allowed_even_with_env_set(monkeypatch):
    """A6 Path 1: localhost bypass holds even when env + header are configured."""
    monkeypatch.setenv("TOKENPAK_PROXY_AUTH_TOKEN", "s3kr3t")
    handler = _make_handler("127.0.0.1", headers={})
    assert handler._auth_gate() is True
    assert handler.send_response.call_args is None


# ---------------------------------------------------------------------------
# Path 2: non-localhost + env unset → 403
# ---------------------------------------------------------------------------


def test_non_localhost_without_env_returns_403(monkeypatch):
    """A6 Path 2: env var unset on non-localhost → 403 with typed error."""
    monkeypatch.delenv("TOKENPAK_PROXY_AUTH_TOKEN", raising=False)

    handler = _make_handler("192.0.2.42", headers={})
    assert handler._auth_gate() is False
    assert _sent_status(handler) == 403

    body = handler.wfile.getvalue()
    payload = json.loads(body)
    assert payload["error"]["type"] == "forbidden"
    assert "TOKENPAK_PROXY_AUTH_TOKEN" in payload["error"]["message"]


# ---------------------------------------------------------------------------
# Path 3: non-localhost + env set + bad/missing header → 401
# ---------------------------------------------------------------------------


def test_non_localhost_env_set_missing_header_returns_401(monkeypatch):
    """A6 Path 3a: env set + header absent → 401."""
    monkeypatch.setenv("TOKENPAK_PROXY_AUTH_TOKEN", "s3kr3t")

    handler = _make_handler("192.0.2.42", headers={})
    assert handler._auth_gate() is False
    assert _sent_status(handler) == 401

    payload = json.loads(handler.wfile.getvalue())
    assert payload["error"]["type"] == "unauthorized"
    assert "X-TokenPak-Auth" in payload["error"]["message"]


def test_non_localhost_env_set_wrong_header_returns_401(monkeypatch):
    """A6 Path 3b: env set + wrong token → 401 (timing-safe compare used)."""
    monkeypatch.setenv("TOKENPAK_PROXY_AUTH_TOKEN", "s3kr3t")

    handler = _make_handler("192.0.2.42", headers={"X-TokenPak-Auth": "wrong-token"})
    assert handler._auth_gate() is False
    assert _sent_status(handler) == 401


# ---------------------------------------------------------------------------
# Path 4: non-localhost + env set + correct token → allow + strip
# ---------------------------------------------------------------------------


def test_non_localhost_env_set_correct_header_allows_and_strips(monkeypatch):
    """A6 Path 4: env set + correct token → allow; header is stripped (I5)."""
    monkeypatch.setenv("TOKENPAK_PROXY_AUTH_TOKEN", "s3kr3t")

    handler = _make_handler(
        "192.0.2.42",
        headers={"X-TokenPak-Auth": "s3kr3t", "Authorization": "Bearer upstream-key"},
    )
    assert handler._auth_gate() is True

    # No 401/403 emitted on success.
    assert handler.send_response.call_args is None

    # Identity populated.
    assert hasattr(handler, "_tokenpak_user_id")
    assert isinstance(handler._tokenpak_user_id, str)
    assert len(handler._tokenpak_user_id) == 16

    # I5: X-TokenPak-Auth stripped so no downstream code can forward it.
    assert handler.headers.get("X-TokenPak-Auth") is None

    # Upstream credential (Authorization) is preserved — only the tokenpak
    # proxy-auth header is stripped. A6 must never clobber upstream creds.
    assert handler.headers.get("Authorization") == "Bearer upstream-key"


# ---------------------------------------------------------------------------
# I5 invariant alignment
# ---------------------------------------------------------------------------


def test_x_tokenpak_auth_not_in_upstream_allowlist():
    """A6 / I5 alignment: X-TokenPak-Auth must not be in PERMITTED_HEADERS_PROXY.

    The SC+1 I5 header-allowlist conformance test asserts outbound headers
    ⊆ PERMITTED_HEADERS_PROXY. If `X-TokenPak-Auth` ever leaked upstream —
    through passthrough, compression, or a future refactor — I5 would flag
    it. This test is a static guard: even if the I5 runtime test drifts,
    the static set must not admit this header.
    """
    assert "x-tokenpak-auth" not in PERMITTED_HEADERS_PROXY, (
        "SECURITY: X-TokenPak-Auth must never be on the upstream allowlist. "
        "It carries the proxy's auth token and leaking it is a credential "
        "disclosure bug."
    )
