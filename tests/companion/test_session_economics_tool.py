# SPDX-License-Identifier: Apache-2.0
"""Session-economics surface tests: the shared companion MCP tool (Claude/Codex parity).

One read-only tool on the shared server: registered in TOOLS (both launchers
use the same registry, which is the Codex-parity mechanism), bound to the
active-session marker by default, validating every payload through the
contract, and returning the canonical JSON encoding.
"""

from __future__ import annotations

import json

from tests.session_economics_fixtures import learning_payload, no_data_payload
from tokenpak.companion.mcp import tools as tools_mod
from tokenpak.core.contracts.session_economics import SessionEconomics


def _tool():
    return next(t for t in tools_mod.TOOLS if t.name == "session_economics")


def test_tool_registered_on_shared_server_registry():
    tool = _tool()
    assert tool.handler is tools_mod._handle_session_economics
    assert "session_id" in tool.input_schema["properties"]
    # Lean-profile discipline: advertised schemas ride every request; this
    # read-only surface must not join the lean core set.
    assert tool.core is False


def test_returns_canonical_contract_json(monkeypatch):
    payload = learning_payload()
    monkeypatch.setattr(tools_mod, "_proxy_post", lambda *a, **k: (200, payload))
    out = tools_mod._handle_session_economics(
        tools_mod.CompanionState(), {"session_id": "sess-fixture-1"}
    )
    assert out == SessionEconomics.from_dict(payload).to_json()


def test_explicit_session_id_wins_over_state(monkeypatch):
    seen = {}

    def _post(path, body, **k):
        seen["path"] = path
        seen["body"] = body
        return 200, learning_payload()

    monkeypatch.setattr(tools_mod, "_proxy_post", _post)
    state = tools_mod.CompanionState()
    state.session_id = "bound-session"
    tools_mod._handle_session_economics(state, {"session_id": "explicit-session"})
    assert seen["path"] == "/v1/messages/session-economics"
    assert seen["body"] == {"session_id": "explicit-session"}


def test_active_marker_binding_is_default(monkeypatch):
    seen = {}

    def _post(path, body, **k):
        seen["body"] = body
        return 200, learning_payload()

    monkeypatch.setattr(tools_mod, "_proxy_post", _post)
    state = tools_mod.CompanionState()
    state.session_id = "bound-session"
    tools_mod._handle_session_economics(state, {})
    assert seen["body"] == {"session_id": "bound-session"}


def test_no_binding_lets_proxy_default(monkeypatch):
    seen = {}

    def _post(path, body, **k):
        seen["body"] = body
        return 200, no_data_payload()

    monkeypatch.setattr(tools_mod, "_proxy_post", _post)
    monkeypatch.setattr(tools_mod, "current_session_id", lambda: "")
    state = tools_mod.CompanionState()
    tools_mod._handle_session_economics(state, {})
    # Empty body → proxy-owned default-session selection, no rival query here.
    assert seen["body"] == {}


def test_proxy_unreachable_is_honest(monkeypatch):
    monkeypatch.setattr(tools_mod, "_proxy_post", lambda *a, **k: (0, {"detail": "down"}))
    out = json.loads(
        tools_mod._handle_session_economics(tools_mod.CompanionState(), {})
    )
    assert out["error"] == "proxy_unreachable"


def test_invalid_payload_is_rejected_not_projected(monkeypatch):
    monkeypatch.setattr(
        tools_mod, "_proxy_post", lambda *a, **k: (200, {"schema_version": "bogus/9"})
    )
    out = json.loads(
        tools_mod._handle_session_economics(tools_mod.CompanionState(), {})
    )
    assert out["error"] == "invalid_session_economics_payload"


def test_error_status_passes_through(monkeypatch):
    monkeypatch.setattr(
        tools_mod,
        "_proxy_post",
        lambda *a, **k: (500, {"error": {"type": "api_error", "message": "x"}}),
    )
    out = json.loads(
        tools_mod._handle_session_economics(tools_mod.CompanionState(), {})
    )
    assert out["error"]["type"] == "api_error"
