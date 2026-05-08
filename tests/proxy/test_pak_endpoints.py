# SPDX-License-Identifier: Apache-2.0
"""Offline contract tests for /pak/v1/* + daemon-probe (Std 32 §10).

Per Std 32 §10 every OSS-side hook gets daemon-present (mocked) and
daemon-absent path coverage. The daemon-absent path is what real users
hit (Pro daemon is opt-in install); daemon-present is mocked here via
sock-info file fixtures + a captive socket on a free local port.
"""

from __future__ import annotations

import io
import json
import socket
import threading
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from tokenpak.licensing import daemon_probe


# ---------------------------------------------------------------------------
# daemon_probe — sock-info parsing + reachability
# ---------------------------------------------------------------------------


@pytest.fixture
def sock_info(tmp_path):
    """Write a sock-info file pointing at a captive listener and return both."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    info_path = tmp_path / "daemon.sock-info"
    info_path.write_text(
        json.dumps({"port": port, "tip_version": "1.0", "started_at": 0})
    )
    yield info_path, port, sock
    try:
        sock.close()
    except OSError:
        pass


def test_state_unavailable_when_file_missing(tmp_path):
    missing = tmp_path / "nope.sock-info"
    assert daemon_probe.detect_daemon_state(sock_info_override=missing) == "unavailable"


def test_state_unavailable_when_file_malformed(tmp_path):
    bad = tmp_path / "bad.sock-info"
    bad.write_text("not json")
    assert daemon_probe.detect_daemon_state(sock_info_override=bad) == "unavailable"


def test_state_unavailable_when_port_missing(tmp_path):
    f = tmp_path / "sock"
    f.write_text(json.dumps({"tip_version": "1.0"}))
    assert daemon_probe.detect_daemon_state(sock_info_override=f) == "unavailable"


def test_state_unavailable_when_port_dead(tmp_path):
    """Sock-info points at an unreachable port → unavailable, not crash."""
    f = tmp_path / "sock"
    f.write_text(json.dumps({"port": 1, "tip_version": "1.0"}))
    assert daemon_probe.detect_daemon_state(sock_info_override=f) == "unavailable"


def test_state_active_when_port_listening(sock_info):
    info_path, _, _ = sock_info
    assert daemon_probe.detect_daemon_state(sock_info_override=info_path) == "active"


def test_is_reachable_true_when_active(sock_info):
    info_path, _, _ = sock_info
    assert daemon_probe.is_daemon_reachable(sock_info_override=info_path) is True


def test_is_reachable_false_when_file_missing(tmp_path):
    assert (
        daemon_probe.is_daemon_reachable(sock_info_override=tmp_path / "nope")
        is False
    )


def test_state_unavailable_for_invalid_port_range(tmp_path):
    """Ports outside 1-65535 are rejected without attempting connect."""
    f = tmp_path / "sock"
    f.write_text(json.dumps({"port": 70_000, "tip_version": "1.0"}))
    assert daemon_probe.detect_daemon_state(sock_info_override=f) == "unavailable"


# ---------------------------------------------------------------------------
# /pak/v1/* dispatch — handler stubs to avoid spinning up the proxy server
# ---------------------------------------------------------------------------


class _StubHandler:
    """Minimal BaseHTTPRequestHandler stand-in for offline endpoint tests."""

    def __init__(
        self,
        path: str,
        *,
        client_address: tuple = ("127.0.0.1", 0),
        body: bytes = b"",
        headers: dict | None = None,
    ):
        self.path = path
        self.client_address = client_address
        self.headers = headers or {}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self._status: int | None = None
        self._sent_headers: list[tuple[str, str]] = []
        self.server = None  # not consulted by /pak/v1 handlers

    def send_response(self, code: int) -> None:
        self._status = code

    def send_header(self, name: str, value: str) -> None:
        self._sent_headers.append((name, value))

    def end_headers(self) -> None:
        pass

    # Convenience accessors for assertions
    def response_status(self) -> int:
        assert self._status is not None, "no response sent"
        return self._status

    def response_json(self) -> Any:
        return json.loads(self.wfile.getvalue().decode("utf-8"))


def _get(path: str, *, client_ip: str = "127.0.0.1") -> _StubHandler:
    """Drive a GET through the dispatcher and return the populated handler."""
    from tokenpak.proxy.app_endpoints import try_handle_get

    h = _StubHandler(path, client_address=(client_ip, 12345))
    handled = try_handle_get(h)
    assert handled is True, f"dispatcher did not handle GET {path}"
    return h


def _post(path: str, body: bytes = b"{}", *, client_ip: str = "127.0.0.1") -> _StubHandler:
    from tokenpak.proxy.app_endpoints import try_handle_post

    h = _StubHandler(
        path,
        client_address=(client_ip, 12345),
        body=body,
        headers={"Content-Length": str(len(body))},
    )
    handled = try_handle_post(h)
    assert handled is True, f"dispatcher did not handle POST {path}"
    return h


# ---------------------------------------------------------------------------
# /pak/v1/status
# ---------------------------------------------------------------------------


def test_status_returns_200_with_required_fields():
    h = _get("/pak/v1/status")
    assert h.response_status() == 200
    body = h.response_json()
    # Required schema per Std 25 §3.4 + Std 32 §13.1 Decision #6
    for key in (
        "daemon_state",
        "multipak_enabled",
        "pak_store_present",
        "vault_paks_indexed",
        "promotion_candidates",
    ):
        assert key in body, f"missing key {key} in /pak/v1/status response"


def test_status_daemon_state_is_unavailable_by_default():
    """No daemon installed on this host (overwhelming common case)."""
    with patch.object(
        daemon_probe, "_SOCK_INFO_PATH", Path("/tmp/__nonexistent_sock_info__")
    ):
        h = _get("/pak/v1/status")
    body = h.response_json()
    assert body["daemon_state"] == "unavailable"


def test_status_multipak_enabled_defaults_false():
    """Std 32 §13.1 Decision #6 — opt-in until 1-week soak."""
    h = _get("/pak/v1/status")
    body = h.response_json()
    assert body["multipak_enabled"] is False


def test_status_rejects_non_localhost():
    h = _get("/pak/v1/status", client_ip="10.0.0.1")
    assert h.response_status() == 401


# ---------------------------------------------------------------------------
# /pak/v1/inspect/<pak-id>
# ---------------------------------------------------------------------------


def test_inspect_empty_pak_id_returns_400():
    h = _get("/pak/v1/inspect/")
    # path = "/pak/v1/inspect/" matches the prefix; pak_id is empty.
    assert h.response_status() == 400


def test_inspect_non_vault_returns_501_not_implemented():
    h = _get("/pak/v1/inspect/journal:s1:42")
    assert h.response_status() == 501
    body = h.response_json()
    assert body["error"] == "not_implemented"
    assert body["reason"] == "pro_daemon_required"
    assert body["daemon_state"] == "unavailable"


def test_inspect_vault_returns_404_when_block_not_indexed():
    """Vault subtype reaches the adapter but the block id isn't in the index
    fixture — adapter returns 404 (correct UX per Std 32 §5.3 no-result path)."""

    class _EmptyVaultIndex:
        blocks = {}

    with patch(
        "tokenpak.proxy.vault_bridge.get_vault_index",
        return_value=_EmptyVaultIndex(),
    ):
        h = _get("/pak/v1/inspect/vault:fake%23deadbeef")
    assert h.response_status() == 404
    assert h.response_json()["error"] == "pak_not_found"


def test_inspect_vault_returns_pak_when_indexed(tmp_path):
    """Daemon-absent happy path — Vault Paks served by OSS adapter alone."""
    cf = tmp_path / "block.txt"
    cf.write_text("data")

    class _StubVaultIndex:
        blocks = {
            "fake#deadbeef": {
                "block_id": "fake#deadbeef",
                "source_path": "/home/sue/tokenpak/README.md",
                "raw_tokens": 100,
                "_content_file": str(cf),
                "file_type": "text",
            }
        }

    with patch(
        "tokenpak.proxy.vault_bridge.get_vault_index",
        return_value=_StubVaultIndex(),
    ):
        # `#` is a URL fragment delimiter — clients must percent-encode it
        # as `%23`. The handler unquotes via urllib.parse.unquote.
        h = _get("/pak/v1/inspect/vault:fake%23deadbeef")
    assert h.response_status() == 200
    body = h.response_json()
    assert body["pak_id"] == "vault:fake#deadbeef"
    assert body["pak_type"] == "vault"
    assert body["authority"] == "file_source"
    assert body["status"] == "proposed"


def test_inspect_unknown_endpoint_returns_404():
    h = _get("/pak/v1/inspectoid/x")
    assert h.response_status() == 404


# ---------------------------------------------------------------------------
# POST /pak/v1/recall
# ---------------------------------------------------------------------------


def test_recall_always_returns_501_in_phase_1():
    h = _post("/pak/v1/recall", body=b'{"query":"anything"}')
    assert h.response_status() == 501
    body = h.response_json()
    assert body["error"] == "not_implemented"
    assert body["reason"] == "pro_daemon_required"


def test_recall_rejects_non_localhost():
    h = _post("/pak/v1/recall", body=b"{}", client_ip="10.0.0.1")
    assert h.response_status() == 401


def test_unknown_pak_post_returns_404():
    h = _post("/pak/v1/nonsense", body=b"{}")
    assert h.response_status() == 404


# ---------------------------------------------------------------------------
# Integration with /tpk/v1/* — the new /pak/v1/* dispatch must not break
# ---------------------------------------------------------------------------


def test_tpk_v1_health_still_works():
    """Adding /pak/v1/* dispatch must not interfere with existing /tpk/v1/*."""

    class _NoServerHandler(_StubHandler):
        # Need a `server` attribute that exposes proxy_server — health
        # reads uptime from there. Stub with None so handler degrades.
        server = type(
            "_S", (), {"proxy_server": None}
        )()

    from tokenpak.proxy.app_endpoints import try_handle_get

    h = _NoServerHandler("/tpk/v1/health", client_address=("127.0.0.1", 12345))
    handled = try_handle_get(h)
    assert handled is True
    assert h.response_status() == 200


def test_non_pak_non_tpk_path_falls_through():
    """The dispatcher only handles its two namespaces; everything else returns False."""
    from tokenpak.proxy.app_endpoints import try_handle_get

    h = _StubHandler("/health", client_address=("127.0.0.1", 12345))
    assert try_handle_get(h) is False
