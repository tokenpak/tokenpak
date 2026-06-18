# SPDX-License-Identifier: Apache-2.0
"""Tests for the companion session-marker bridge.

The pre_send hook (one process) persists the live session id to a run-dir
marker; the long-lived MCP server (another process) reads it back via
``current_session_id`` and binds ``CompanionState.session_id`` before each
tool dispatch. These tests cover both ends plus the hook entry point.
"""

from __future__ import annotations

import io
import json

from tokenpak.companion.hooks import pre_send
from tokenpak.companion.mcp import server as mcp_server
from tokenpak.companion.mcp.tools import CompanionState, current_session_id

# ---------------------------------------------------------------------------
# _write_session_marker (hook side)
# ---------------------------------------------------------------------------


def test_session_marker_written_atomically(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    pre_send._write_session_marker("sess-123")
    marker = tmp_path / "run" / "current-session"
    assert marker.exists()
    assert marker.read_text(encoding="utf-8") == "sess-123"
    # tmp+replace leaves no residue
    assert not (tmp_path / "run" / "current-session.tmp").exists()


def test_session_marker_strips_whitespace(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    pre_send._write_session_marker("  sess-xyz \n")
    assert (tmp_path / "run" / "current-session").read_text() == "sess-xyz"


def test_session_marker_overwrites_previous(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    pre_send._write_session_marker("sess-first")
    pre_send._write_session_marker("sess-second")
    assert (tmp_path / "run" / "current-session").read_text() == "sess-second"


def test_session_marker_never_raises(tmp_path, monkeypatch):
    """Marker write is best-effort: an unwritable dir must not raise."""
    blocker = tmp_path / "blocked"
    blocker.write_text("a file where the dir should be")
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(blocker))
    pre_send._write_session_marker("sess-x")  # must not raise


# ---------------------------------------------------------------------------
# pre_send hook entry point
# ---------------------------------------------------------------------------


def _run_hook(monkeypatch, payload: dict) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(payload)))
    return pre_send.main()


def test_session_marker_written_by_hook_main(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    rc = _run_hook(monkeypatch, {"session_id": "sess-hook", "prompt": "hi"})
    assert rc == 0
    assert (tmp_path / "run" / "current-session").read_text() == "sess-hook"


def test_session_marker_not_written_without_session_id(tmp_path, monkeypatch):
    """The anon-{pid} journal fallback is NOT a handoff identity — no marker."""
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    rc = _run_hook(monkeypatch, {"prompt": "hi"})
    assert rc == 0
    assert not (tmp_path / "run" / "current-session").exists()


def test_session_marker_not_written_when_companion_disabled(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    monkeypatch.setenv("TOKENPAK_COMPANION_ENABLED", "0")
    rc = _run_hook(monkeypatch, {"session_id": "sess-off", "prompt": "hi"})
    assert rc == 0
    assert not (tmp_path / "run" / "current-session").exists()


# ---------------------------------------------------------------------------
# current_session_id (MCP server side)
# ---------------------------------------------------------------------------


def test_session_marker_read_back(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    run = tmp_path / "run"
    run.mkdir()
    (run / "current-session").write_text("  sess-live \n", encoding="utf-8")
    assert current_session_id() == "sess-live"


def test_session_marker_absent_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    assert current_session_id() == ""


def test_session_marker_hook_to_reader_roundtrip(tmp_path, monkeypatch):
    """End-to-end: the hook writes, the reader sees the same id."""
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    pre_send._write_session_marker("sess-rt")
    assert current_session_id() == "sess-rt"


# ---------------------------------------------------------------------------
# MCP server per-dispatch binding
# ---------------------------------------------------------------------------


def _call_unknown_tool(state: CompanionState, sent: list) -> None:
    """Dispatch a tools/call; capture responses instead of writing stdout."""
    mcp_server._handle_tools_call(1, {"name": "no-such-tool", "arguments": {}}, state)


def test_session_marker_binds_state_on_dispatch(tmp_path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    run = tmp_path / "run"
    run.mkdir()
    (run / "current-session").write_text("sess-bound", encoding="utf-8")
    sent = []
    monkeypatch.setattr(mcp_server, "_send", lambda obj: sent.append(obj))

    state = CompanionState()
    assert state.session_id == ""
    _call_unknown_tool(state, sent)
    assert state.session_id == "sess-bound"


def test_session_marker_binding_refreshes_per_call(tmp_path, monkeypatch):
    """A new marker (e.g. after /clear starts a new session) is picked up on
    the next dispatch without restarting the server."""
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    run = tmp_path / "run"
    run.mkdir()
    sent = []
    monkeypatch.setattr(mcp_server, "_send", lambda obj: sent.append(obj))
    state = CompanionState()

    (run / "current-session").write_text("sess-one", encoding="utf-8")
    _call_unknown_tool(state, sent)
    assert state.session_id == "sess-one"

    (run / "current-session").write_text("sess-two", encoding="utf-8")
    _call_unknown_tool(state, sent)
    assert state.session_id == "sess-two"


def test_session_marker_missing_keeps_existing_binding(tmp_path, monkeypatch):
    """No marker → the previously-bound session id is retained, not cleared."""
    monkeypatch.setenv("TOKENPAK_COMPANION_JOURNAL_DIR", str(tmp_path))
    sent = []
    monkeypatch.setattr(mcp_server, "_send", lambda obj: sent.append(obj))
    state = CompanionState()
    state.session_id = "sess-kept"
    _call_unknown_tool(state, sent)
    assert state.session_id == "sess-kept"
