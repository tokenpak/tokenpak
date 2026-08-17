# SPDX-License-Identifier: Apache-2.0
"""Session-economics non-self-metering proof: a real proxy subprocess, a stub upstream,
and a stop/restart on the SAME home and database.

Proves, with exact bytes rather than assertions of intent:
  1. the session-economics endpoint and every thin surface over it (status,
     dashboard, companion MCP) cause zero upstream calls and zero ledger
     mutation;
  2. under a frozen evaluation time, the canonical contract JSON and the
     completed-row input set are identical before and after a full process
     restart on the same home/database;
  3. repeating the original model request after the restart produces
     byte-identical provider-bound bytes — enabling the display changed
     nothing on the wire.

The calibrated-forecast follow-on must re-run this suite unchanged as its regression gate.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tests.proxy._proxy_subprocess import REPO_ROOT, ProxyProc, free_port


def _subprocess_extra_env() -> dict[str, str]:
    """Keep dependency resolution working when the helper overrides HOME.

    The subprocess env is built from scratch with a throwaway HOME, which
    silently drops the parent interpreter's *user* site-packages (they are
    HOME-keyed). Appending that directory to PYTHONPATH restores third-party
    deps for local dev hosts; PYTHONPATH entries do not process ``.pth``
    files, so no editable-install redirection can leak in. On CI (deps in
    the system environment) the entry is inert.
    """
    import site

    try:
        user_site = site.getusersitepackages()
    except Exception:
        user_site = ""
    path = str(REPO_ROOT)
    if user_site:
        path = f"{path}{__import__('os').pathsep}{user_site}"
    return {"PYTHONPATH": path}


_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"
_JSON_BODY = (_FIXTURES_DIR / "json_response_messages.json").read_bytes()

_SESSION = "sedr-exclusion-session-1"
# Frozen evaluation time in the future relative to any test run so idle time
# is large-but-valid and, above all, IDENTICAL across the restart.
_FROZEN_NOW = "2027-01-01T00:00:00+00:00"


class _RecordingUpstream(HTTPServer):
    """Stub upstream that records every provider-bound request body."""

    def __init__(self, port: int) -> None:
        super().__init__(("127.0.0.1", port), _RecordingHandler)
        self.bodies: list[bytes] = []


class _RecordingHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: object) -> None:
        pass

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b""
        self.server.bodies.append(raw)  # type: ignore[attr-defined]
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(_JSON_BODY)))
        self.end_headers()
        self.wfile.write(_JSON_BODY)


def _requests_rows(db_path: Path) -> list[tuple]:
    """The full completed-row input set the economics engine reads."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    try:
        return conn.execute("SELECT * FROM requests ORDER BY timestamp ASC, id ASC").fetchall()
    finally:
        conn.close()


def _post_economics(proxy: ProxyProc, body: dict) -> tuple[int, dict, bytes]:
    import http.client

    payload = json.dumps(body).encode()
    conn = http.client.HTTPConnection("127.0.0.1", proxy.port, timeout=30)
    try:
        conn.request(
            "POST",
            "/v1/messages/session-economics",
            body=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()
    finally:
        conn.close()


@pytest.fixture()
def upstream():
    server = _RecordingUpstream(free_port())
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    yield server
    server.shutdown()
    t.join(timeout=2)


def test_restart_equality_and_default_surface_exclusion(upstream, monkeypatch):
    stub_url = f"http://127.0.0.1:{upstream.server_address[1]}"
    proxy = ProxyProc(stub_url, extra_env=_subprocess_extra_env())
    try:
        proxy.wait_ready()

        # -- original request through the proxy (captures provider bytes) --
        status, _, _ = proxy.post_message(
            "hello session economics",
            extra_headers={"X-TokenPak-Session": _SESSION},
        )
        assert status == 200
        assert proxy.wait_row_count(1) >= 1
        assert len(upstream.bodies) == 1
        provider_bytes_before = upstream.bodies[0]

        baseline_rows = _requests_rows(proxy.db_path)
        baseline_upstream_calls = len(upstream.bodies)

        # -- pre-restart canonical read under frozen evaluation time --
        code, _, econ_before = _post_economics(proxy, {"session_id": _SESSION, "now": _FROZEN_NOW})
        assert code == 200
        parsed = json.loads(econ_before)
        assert parsed["session"]["id"] == _SESSION
        assert parsed["schema_version"] == "session-economics/1"

        # ================= restart on the SAME home/database =================
        proxy.restart()

        # -- 1) endpoint read: value-identical canonical JSON --
        code, _, econ_after = _post_economics(proxy, {"session_id": _SESSION, "now": _FROZEN_NOW})
        assert code == 200
        assert json.loads(econ_after) == json.loads(econ_before)

        # -- 2) proxy-owned default-session selection (no explicit id) --
        code, headers, econ_default = _post_economics(proxy, {"now": _FROZEN_NOW})
        assert code == 200
        assert json.loads(econ_default)["session"]["id"] == _SESSION
        assert headers.get("X-TokenPak-Session-Selection") == ("latest completed ledger session")

        # -- 3) every thin surface, live against the same subprocess --
        from tokenpak.cli.commands import status as status_mod

        econ_obj, reason = status_mod._fetch_session_economics(proxy.base)
        assert reason == ""
        assert econ_obj is not None

        from tokenpak.cli.commands import dashboard as dashboard_mod

        dash_value = dashboard_mod._collect_session_economics(proxy.port)
        assert dash_value.get("unavailable") is None
        assert dash_value["session"]["id"] == _SESSION

        monkeypatch.setenv("TOKENPAK_PROXY_URL", proxy.base)
        from tokenpak.companion.mcp import tools as tools_mod

        mcp_out = tools_mod._handle_session_economics(
            tools_mod.CompanionState(), {"session_id": _SESSION}
        )
        assert json.loads(mcp_out)["session"]["id"] == _SESSION

        # -- non-self-metering: zero upstream calls, zero ledger mutation --
        assert len(upstream.bodies) == baseline_upstream_calls
        assert _requests_rows(proxy.db_path) == baseline_rows

        # -- 4) replaying the original request stays byte-identical --
        status, _, _ = proxy.post_message(
            "hello session economics",
            extra_headers={"X-TokenPak-Session": _SESSION},
        )
        assert status == 200
        assert len(upstream.bodies) == baseline_upstream_calls + 1
        assert upstream.bodies[-1] == provider_bytes_before
    finally:
        proxy.cleanup()


def test_frozen_now_rejects_garbage(upstream):
    stub_url = f"http://127.0.0.1:{upstream.server_address[1]}"
    proxy = ProxyProc(stub_url, extra_env=_subprocess_extra_env())
    try:
        proxy.wait_ready()
        code, _, body = _post_economics(proxy, {"now": "not-a-timestamp"})
        assert code == 400
        assert b"ISO-8601" in body
        code, _, body = _post_economics(proxy, {"now": "2027-01-01T00:00:00"})
        assert code == 400
        assert b"timezone" in body
    finally:
        proxy.cleanup()


def test_empty_ledger_default_selection_is_explicitly_absent(upstream):
    stub_url = f"http://127.0.0.1:{upstream.server_address[1]}"
    proxy = ProxyProc(stub_url, extra_env=_subprocess_extra_env())
    try:
        proxy.wait_ready()
        code, headers, body = _post_economics(proxy, {"now": _FROZEN_NOW})
        assert code == 200
        parsed = json.loads(body)
        # No completed rows exist: identity stays absent and states are words,
        # never invented zeros.
        assert parsed["session"]["id"] is None
        assert parsed["session"]["identity_state"] in {"no_data", "unavailable"}
        assert headers.get("X-TokenPak-Session-Selection") == (
            "no completed session rows exist yet"
        )
    finally:
        proxy.cleanup()
