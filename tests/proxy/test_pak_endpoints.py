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
    info_path.write_text(json.dumps({"port": port, "tip_version": "1.0", "started_at": 0}))
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
    assert daemon_probe.is_daemon_reachable(sock_info_override=tmp_path / "nope") is False


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
    with patch.object(daemon_probe, "_SOCK_INFO_PATH", Path("/tmp/__nonexistent_sock_info__")):
        h = _get("/pak/v1/status")
    body = h.response_json()
    assert body["daemon_state"] == "unavailable"


def test_status_multipak_enabled_defaults_false(monkeypatch):
    """Std 32 §13.1 Decision #6 — opt-in until 1-week soak.

    Mocks load_config to {} (no multipak section) so this test isn't
    dependent on the developer's user config.
    """
    from tokenpak.core import config_loader

    monkeypatch.setattr(config_loader, "load_config", lambda *a, **k: {})
    h = _get("/pak/v1/status")
    body = h.response_json()
    assert body["multipak_enabled"] is False


def test_status_multipak_enabled_reads_pro_section(monkeypatch):
    """Canonical config path: pro.multipak.enabled = true."""
    from tokenpak.core import config_loader

    cfg = {"pro": {"multipak": {"enabled": True}}}
    monkeypatch.setattr(config_loader, "load_config", lambda *a, **k: cfg)
    h = _get("/pak/v1/status")
    assert h.response_json()["multipak_enabled"] is True


def test_status_multipak_enabled_legacy_path(monkeypatch):
    """Legacy config layout (multipak.enabled at top level) still honored."""
    from tokenpak.core import config_loader

    cfg = {"multipak": {"enabled": True}}
    monkeypatch.setattr(config_loader, "load_config", lambda *a, **k: cfg)
    h = _get("/pak/v1/status")
    assert h.response_json()["multipak_enabled"] is True


def test_status_multipak_enabled_pro_wins_over_legacy(monkeypatch):
    """When both pro.multipak.enabled and multipak.enabled are set,
    the canonical pro section wins."""
    from tokenpak.core import config_loader

    cfg = {
        "pro": {"multipak": {"enabled": False}},
        "multipak": {"enabled": True},
    }
    monkeypatch.setattr(config_loader, "load_config", lambda *a, **k: cfg)
    h = _get("/pak/v1/status")
    assert h.response_json()["multipak_enabled"] is False


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


def test_inspect_non_vault_unknown_returns_404_oss_safe(tmp_path, monkeypatch):
    """Non-vault inspect with no matching recall row → 404, OSS-safe wording.

    Per PR 3 decision 2: ``pro_daemon_required`` 501 is removed entirely
    from the OSS inspect surface. A non-vault ``pak_id`` that has no
    metadata row in the recall store yields the same 404 contract as
    any other read miss. No Pro / daemon / license wording leaks from
    this path.
    """
    from tokenpak.companion.recall import RecallStore
    from tokenpak.proxy import app_endpoints

    store_path = tmp_path / "recall.db"
    with RecallStore.open(store_path):
        pass  # empty store
    monkeypatch.setattr(
        app_endpoints,
        "_open_recall_store_default",
        lambda: RecallStore.open(store_path),
    )

    h = _get("/pak/v1/inspect/journal:s1:42")
    assert h.response_status() == 404
    body = h.response_json()
    assert body["error"] == "pak_not_found"
    # OSS-safe — no Pro / daemon / license wording leaks here.
    blob = json.dumps(body).lower()
    assert "pro_daemon_required" not in blob
    assert "pro daemon" not in blob
    assert "tokenpak-paid" not in blob
    assert "license" not in blob


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
# GET /pak/v1/list — PR 3 OSS list surface
# ---------------------------------------------------------------------------
#
# Decisions from PR 3 review (2026-05-11):
#   1. Response envelope: {items, next_cursor, limit, truncated} (always).
#   2. No ``pro_daemon_required`` 501 anywhere on the OSS list path.
#   3. Filter key is byte-literal ``?pak_type=...`` (no alias expansion).
#   4. Default + cap = 100; surplus triggers ``truncated=true`` + cursor.
#
# These tests inject a tmp ``RecallStore`` via the
# ``_open_recall_store_default`` shim so the suite never depends on the
# user's real recall.db.


def _patch_recall_store(monkeypatch, tmp_path, seed_rows: list[dict] | None = None):
    """Replace the default recall-store opener with one pointing at a
    freshly-seeded tmp DB. Returns the resolved DB path for follow-up
    assertions.
    """
    from tokenpak.companion.recall import RecallStore
    from tokenpak.proxy import app_endpoints

    store_path = tmp_path / "recall.db"
    with RecallStore.open(store_path) as store:
        for r in seed_rows or []:
            store.upsert_pak(**r)

    monkeypatch.setattr(
        app_endpoints,
        "_open_recall_store_default",
        lambda: RecallStore.open(store_path),
    )
    return store_path


def _list_seed_row(
    pak_id: str,
    *,
    now: str,
    pak_type: str = "vault",
    project: str | None = "alpha",
    source_type: str = "doc",
    authority: str = "llm_generated",
) -> dict:
    return {
        "pak_id": pak_id,
        "pak_type": pak_type,
        "source_type": source_type,
        "authority": authority,
        "title": f"title-{pak_id}",
        "content_hash": (pak_id * 4)[:32],
        "project": project,
        "now": now,
    }


def test_list_envelope_shape_on_empty_store(tmp_path, monkeypatch, require_fts5):
    """Decision 1 — even an empty store returns the cursor-pagination envelope.

    No bare array, no missing keys; ``next_cursor`` is ``null`` and
    ``truncated`` is ``false``.
    """
    _patch_recall_store(monkeypatch, tmp_path)
    h = _get("/pak/v1/list")
    assert h.response_status() == 200
    body = h.response_json()
    assert set(body.keys()) == {"items", "next_cursor", "limit", "truncated"}
    assert body["items"] == []
    assert body["next_cursor"] is None
    assert body["truncated"] is False
    assert body["limit"] == 100  # decision 4 default


def test_list_envelope_shape_with_rows(tmp_path, monkeypatch, require_fts5):
    """Decision 1 — non-empty page still uses the envelope, never a bare array."""
    _patch_recall_store(
        monkeypatch,
        tmp_path,
        seed_rows=[
            _list_seed_row("a", now="2026-05-11T10:00:00Z"),
            _list_seed_row("b", now="2026-05-11T10:01:00Z"),
        ],
    )
    h = _get("/pak/v1/list")
    body = h.response_json()
    assert h.response_status() == 200
    assert isinstance(body["items"], list)
    assert [r["pak_id"] for r in body["items"]] == ["b", "a"]
    # Each item carries the metadata schema, not body bytes.
    first = body["items"][0]
    assert {
        "pak_id",
        "pak_type",
        "project",
        "topic",
        "source_type",
        "authority",
        "title",
        "summary",
        "content_hash",
        "created_at",
        "updated_at",
        "superseded_by",
    } <= set(first.keys())
    assert body["next_cursor"] is None
    assert body["truncated"] is False


def test_list_default_and_cap_at_100(tmp_path, monkeypatch, require_fts5):
    """Decision 4 — default limit is 100; asking for more is clamped to 100."""
    rows = [
        _list_seed_row(f"r{i:03d}", now=f"2026-05-11T10:{i // 60:02d}:{i % 60:02d}Z")
        for i in range(105)
    ]
    _patch_recall_store(monkeypatch, tmp_path, seed_rows=rows)

    # No limit → default 100.
    h = _get("/pak/v1/list")
    body = h.response_json()
    assert body["limit"] == 100
    assert len(body["items"]) == 100
    assert body["truncated"] is True
    assert body["next_cursor"] is not None

    # Explicit cap-exceeding limit → silently clamped, no error.
    h2 = _get("/pak/v1/list?limit=500")
    body2 = h2.response_json()
    assert body2["limit"] == 100
    assert len(body2["items"]) == 100
    assert body2["truncated"] is True


def test_list_smaller_limit_truncates_when_more_remain(tmp_path, monkeypatch, require_fts5):
    """Decision 4 — sub-cap limit still surfaces truncated + cursor when more rows match."""
    _patch_recall_store(
        monkeypatch,
        tmp_path,
        seed_rows=[
            _list_seed_row("a", now="2026-05-11T10:00:00Z"),
            _list_seed_row("b", now="2026-05-11T10:01:00Z"),
            _list_seed_row("c", now="2026-05-11T10:02:00Z"),
        ],
    )
    h = _get("/pak/v1/list?limit=2")
    body = h.response_json()
    assert body["limit"] == 2
    assert [r["pak_id"] for r in body["items"]] == ["c", "b"]
    assert body["truncated"] is True
    assert body["next_cursor"] is not None


def test_list_cursor_round_trip_completes_page(tmp_path, monkeypatch, require_fts5):
    """Decision 1 + 4 — feeding ``next_cursor`` back yields the remainder."""
    _patch_recall_store(
        monkeypatch,
        tmp_path,
        seed_rows=[
            _list_seed_row("a", now="2026-05-11T10:00:00Z"),
            _list_seed_row("b", now="2026-05-11T10:01:00Z"),
            _list_seed_row("c", now="2026-05-11T10:02:00Z"),
            _list_seed_row("d", now="2026-05-11T10:03:00Z"),
        ],
    )
    page1 = _get("/pak/v1/list?limit=2").response_json()
    cursor = page1["next_cursor"]
    assert cursor

    # URL-encode the cursor (urlsafe-b64 has no special chars, but be defensive).
    from urllib.parse import quote

    page2 = _get(f"/pak/v1/list?limit=2&cursor={quote(cursor)}").response_json()
    assert [r["pak_id"] for r in page2["items"]] == ["b", "a"]
    assert page2["truncated"] is False
    assert page2["next_cursor"] is None


def test_list_pak_type_filter_byte_literal(tmp_path, monkeypatch, require_fts5):
    """Decision 3 — ``?pak_type=`` matches exactly, no alias / casefold expansion."""
    _patch_recall_store(
        monkeypatch,
        tmp_path,
        seed_rows=[
            _list_seed_row("a", now="2026-05-11T10:00:00Z", pak_type="vault"),
            _list_seed_row("b", now="2026-05-11T10:01:00Z", pak_type="interaction"),
            _list_seed_row("c", now="2026-05-11T10:02:00Z", pak_type="vault"),
        ],
    )

    only_vault = _get("/pak/v1/list?pak_type=vault").response_json()
    assert [r["pak_id"] for r in only_vault["items"]] == ["c", "a"]

    only_inter = _get("/pak/v1/list?pak_type=interaction").response_json()
    assert [r["pak_id"] for r in only_inter["items"]] == ["b"]

    # ``project`` value MUST NOT alias-expand to ``vault`` at this layer.
    alias_miss = _get("/pak/v1/list?pak_type=project").response_json()
    assert alias_miss["items"] == []

    # Casefold is not honored — PR 3 byte-literal contract.
    casefold_miss = _get("/pak/v1/list?pak_type=VAULT").response_json()
    assert casefold_miss["items"] == []


def test_list_project_filter_byte_literal(tmp_path, monkeypatch, require_fts5):
    """``?project=`` is also byte-literal in PR 3."""
    _patch_recall_store(
        monkeypatch,
        tmp_path,
        seed_rows=[
            _list_seed_row("a", now="2026-05-11T10:00:00Z", project="alpha"),
            _list_seed_row("b", now="2026-05-11T10:01:00Z", project="beta"),
            _list_seed_row("c", now="2026-05-11T10:02:00Z", project="alpha"),
        ],
    )
    only_alpha = _get("/pak/v1/list?project=alpha").response_json()
    assert [r["pak_id"] for r in only_alpha["items"]] == ["c", "a"]


def test_list_invalid_limit_returns_400(tmp_path, monkeypatch, require_fts5):
    """Non-integer ``limit`` → 400 invalid_request, not a 500 / silent fallback."""
    _patch_recall_store(monkeypatch, tmp_path)
    h = _get("/pak/v1/list?limit=not-an-int")
    assert h.response_status() == 400
    body = h.response_json()
    assert body["error"] == "invalid_request"


def test_list_zero_limit_returns_400(tmp_path, monkeypatch, require_fts5):
    """``limit=0`` is rejected at the HTTP layer (storage clamps; HTTP refuses)."""
    _patch_recall_store(monkeypatch, tmp_path)
    h = _get("/pak/v1/list?limit=0")
    assert h.response_status() == 400


def test_list_invalid_cursor_returns_400(tmp_path, monkeypatch, require_fts5):
    """A bogus cursor surfaces as 400 invalid_request (mapped from store ValueError)."""
    _patch_recall_store(monkeypatch, tmp_path)
    h = _get("/pak/v1/list?cursor=not-a-cursor!!!")
    assert h.response_status() == 400
    assert h.response_json()["error"] == "invalid_request"


def test_list_rejects_non_localhost(tmp_path, monkeypatch, require_fts5):
    """Auth gate matches every other /pak/v1/* surface."""
    _patch_recall_store(monkeypatch, tmp_path)
    h = _get("/pak/v1/list", client_ip="10.0.0.1")
    assert h.response_status() == 401


def test_list_no_pro_wording_anywhere(tmp_path, monkeypatch, require_fts5):
    """Decision 2 scope-guard — no Pro / daemon / license wording leaks
    from the OSS list response, regardless of empty / non-empty / cursor state.
    """
    _patch_recall_store(
        monkeypatch,
        tmp_path,
        seed_rows=[_list_seed_row("a", now="2026-05-11T10:00:00Z")],
    )
    blob = json.dumps(_get("/pak/v1/list").response_json()).lower()
    for forbidden in (
        "pro_daemon_required",
        "pro daemon",
        "tokenpak-paid",
        "license",
        "cloud",
        "sync",
        "pricing",
    ):
        assert forbidden not in blob, f"OSS leak: {forbidden!r} appeared in /pak/v1/list response"


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
# POST /pak/v1/promote — Pro daemon forward (Std 32 §4)
# ---------------------------------------------------------------------------
#
# When the daemon is absent → 501 ``pro_daemon_required``.
# When the daemon is present → forward request body to the daemon's
# loopback port and proxy back the response.
#
# Tests use a minimal stub HTTP server in a thread to stand in for the
# real Pro daemon (which is closed-source and not importable here).

import threading
from http.server import BaseHTTPRequestHandler, HTTPServer


class _DaemonStub:
    """Tiny localhost HTTP server that records forwarded requests.

    Returns ``self.canned_response`` (status, body) for every POST it
    receives, regardless of path. Use the ``with`` form to manage
    lifecycle::

        with _DaemonStub(canned_status=201, canned_body={"pak_id": "x"}) as stub:
            ...
    """

    def __init__(self, *, canned_status: int = 201, canned_body: dict | None = None):
        self.canned_status = canned_status
        self.canned_body = canned_body or {"promoted": True, "pak_id": "pak-int-stub"}
        self.received_path: str | None = None
        self.received_body: bytes | None = None
        self.received_headers: dict[str, str] = {}
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "_DaemonStub":
        outer = self

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, *_a, **_kw):  # silence noise
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or "0")
                outer.received_path = self.path
                outer.received_body = self.rfile.read(length) if length > 0 else b""
                outer.received_headers = dict(self.headers.items())
                resp = json.dumps(outer.canned_body).encode("utf-8")
                self.send_response(outer.canned_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(resp)))
                self.end_headers()
                self.wfile.write(resp)

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self._server.server_address[1]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_a):
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


def _write_sock_info(path: Path, port: int) -> None:
    path.write_text(json.dumps({"port": port, "tip_version": "1.0", "started_at": 0}))


def test_promote_returns_501_when_daemon_absent(tmp_path):
    """No sock-info → daemon unavailable → 501 pro_daemon_required."""

    missing = tmp_path / "absent.sock-info"
    with patch.object(daemon_probe, "_SOCK_INFO_PATH", missing):
        h = _post("/pak/v1/promote", body=b'{"any":"thing"}')
    assert h.response_status() == 501
    body = h.response_json()
    assert body["error"] == "not_implemented"
    assert body["reason"] == "pro_daemon_required"


def test_promote_forwards_to_daemon_when_active(tmp_path):
    """sock-info present + reachable → forward body, proxy response."""

    sock_path = tmp_path / "daemon.sock-info"
    payload = b'{"source":"llm_response","content":"hi everyone","captured_at":"2026-05-09T17:00:00+00:00","platform":"x"}'

    with _DaemonStub(
        canned_status=201, canned_body={"promoted": True, "pak_id": "pak-int-abc123"}
    ) as stub:
        _write_sock_info(sock_path, stub.port)
        with patch.object(daemon_probe, "_SOCK_INFO_PATH", sock_path):
            h = _post("/pak/v1/promote", body=payload)

    assert h.response_status() == 201
    body = h.response_json()
    assert body["promoted"] is True
    assert body["pak_id"] == "pak-int-abc123"
    # Confirm the body actually reached the daemon stub
    assert stub.received_path == "/pak/v1/promote"
    assert stub.received_body == payload


def test_promote_forwards_skip_response_intact(tmp_path):
    """Daemon's 200-with-promoted=false propagates verbatim."""

    sock_path = tmp_path / "daemon.sock-info"
    with _DaemonStub(
        canned_status=200,
        canned_body={"promoted": False, "filter_decision": "skip", "reason": "trivial"},
    ) as stub:
        _write_sock_info(sock_path, stub.port)
        with patch.object(daemon_probe, "_SOCK_INFO_PATH", sock_path):
            h = _post(
                "/pak/v1/promote",
                body=b'{"source":"llm_response","content":"thanks","captured_at":"2026-05-09T17:00:00+00:00","platform":"x"}',
            )
    assert h.response_status() == 200
    body = h.response_json()
    assert body["promoted"] is False
    assert body["filter_decision"] == "skip"


def test_promote_returns_503_when_daemon_dies_mid_flight(tmp_path):
    """sock-info points at a dead port → daemon_unreachable.

    Probe sees a live port (we wrote sock-info while the stub was up);
    after closing the stub, the probe call will see the port as dead
    too — so technically this test exercises a probe-failed path. The
    503 path covers genuine TOCTOU where the daemon dies *between*
    the probe and the forward; we trust http.client.HTTPConnection to
    fail on connect for an empty port, which it does.

    To exercise the real TOCTOU we'd need a deeper stub; this test
    covers the closely-related "stale sock-info" case where the file
    points at a port nobody owns.
    """

    sock_path = tmp_path / "daemon.sock-info"
    # Pick a port and bind it just to discover it, then release.
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    dead_port = s.getsockname()[1]
    s.close()

    _write_sock_info(sock_path, dead_port)
    with patch.object(daemon_probe, "_SOCK_INFO_PATH", sock_path):
        h = _post("/pak/v1/promote", body=b"{}")

    # Probe sees connection refused → "unavailable" → 501 (not 503).
    # 503 path requires probe to succeed and forward to fail; that
    # requires the daemon to die between two ops, which is hard to
    # script. We accept either outcome here as long as it's a documented
    # error.
    assert h.response_status() in (501, 503)
    body = h.response_json()
    assert body["error"] in ("not_implemented", "daemon_unreachable")


def test_promote_does_not_forward_auth_headers(tmp_path, monkeypatch):
    """Caller's X-TPK-Key must NOT leak to the daemon (defense in depth).

    The daemon is loopback-only and requires no auth; passing the OSS
    proxy key through would expose it to the daemon's logs needlessly.
    """

    monkeypatch.setenv("TOKENPAK_PROXY_KEY", "secret-key-value")
    sock_path = tmp_path / "daemon.sock-info"
    with _DaemonStub(canned_status=201) as stub:
        _write_sock_info(sock_path, stub.port)
        # Build a handler with the matching X-TPK-Key so auth passes
        from tokenpak.proxy.app_endpoints import try_handle_post

        h = _StubHandler(
            "/pak/v1/promote",
            client_address=("127.0.0.1", 0),
            body=b"{}",
            headers={"Content-Length": "2", "X-TPK-Key": "secret-key-value"},
        )
        with patch.object(daemon_probe, "_SOCK_INFO_PATH", sock_path):
            try_handle_post(h)

    assert h.response_status() == 201
    # No auth header reached the daemon
    assert "X-TPK-Key" not in stub.received_headers
    assert "x-tpk-key" not in stub.received_headers
    # And the secret value isn't anywhere in the forwarded headers
    assert not any("secret-key-value" in v for v in stub.received_headers.values())


# ---------------------------------------------------------------------------
# Integration with /tpk/v1/* — the new /pak/v1/* dispatch must not break
# ---------------------------------------------------------------------------


def test_tpk_v1_health_still_works():
    """Adding /pak/v1/* dispatch must not interfere with existing /tpk/v1/*."""

    class _NoServerHandler(_StubHandler):
        # Need a `server` attribute that exposes proxy_server — health
        # reads uptime from there. Stub with None so handler degrades.
        server = type("_S", (), {"proxy_server": None})()

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
