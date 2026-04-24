# SPDX-License-Identifier: Apache-2.0
"""AnthropicOAuthBackend — session-mapper integration (v1.3.14, 2026-04-24).

The backend now:

  1. Invokes ``claude --output-format json`` to get a parseable result.
  2. Persists ``session_id`` from the CLI output to the session mapper
     keyed by ``(platform, external_session_id, provider)`` when the
     request carries a platform signal.
  3. On subsequent turns with the same ``(platform, external_id)``,
     invokes ``claude --resume <uuid>`` so multi-turn conversations
     share one Claude session.
  4. Falls back to ``--continue`` when no platform signal is present
     (preserves v1.3.13 behavior for direct callers).

Tests use a bash stub that emits the real CLI's JSON schema so we can
assert the argv + persist behavior without needing a live Claude
install. The session mapper uses a per-test SQLite file.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tokenpak.services.request import Request
from tokenpak.services.routing_service.backends.anthropic_oauth import (
    AnthropicOAuthBackend,
)
from tokenpak.services.routing_service.session_mapper import SessionMap


def _write_claude_stub(
    bin_dir: Path, session_id: str = "cli-uuid-001", argv_log: Path = None
) -> Path:
    """Emit JSON like the real CLI. Log argv to ``argv_log`` for assertions.

    ``argv_log`` must be on the filesystem because subprocess.run captures
    stderr into ``completed.stderr`` (the parent process doesn't see it).
    Pytest's ``capfd`` can't reach through that barrier, so we use a
    side-channel file the test can read back.
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / "claude"
    log_path = str(argv_log or (bin_dir / "argv.log"))
    path.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s\\n" "$@" >> "{log_path}"\n'
        f'cat <<JSON\n'
        f'{{"type":"result","subtype":"success","is_error":false,'
        f'"session_id":"{session_id}","result":"OK","stop_reason":"end_turn",'
        f'"usage":{{"input_tokens":12,"output_tokens":3,'
        f'"cache_creation_input_tokens":0,"cache_read_input_tokens":0}},'
        f'"modelUsage":{{"claude-sonnet-4-6":{{"inputTokens":12,"outputTokens":3}}}},'
        f'"total_cost_usd":0.0001}}\n'
        f'JSON\n'
    )
    path.chmod(0o755)
    return path


def _read_argv(bin_dir: Path) -> str:
    argv_log = bin_dir / "argv.log"
    return argv_log.read_text() if argv_log.exists() else ""


def _req(headers=None, body=None) -> Request:
    body = body or json.dumps(
        {"messages": [{"role": "user", "content": "ping"}]}
    ).encode()
    return Request(body=body, headers=headers or {})


@pytest.fixture
def isolated_mapper(tmp_path: Path, monkeypatch):
    """Swap the singleton mapper with a test-local one so db state is per-test."""
    mapper = SessionMap(db_path=tmp_path / "session_map.db")
    monkeypatch.setattr(
        "tokenpak.services.routing_service.session_mapper._singleton",
        mapper,
    )
    return mapper


@pytest.fixture(autouse=True)
def _clear_no_continue(monkeypatch):
    monkeypatch.delenv("TOKENPAK_OAUTH_NO_CONTINUE", raising=False)
    monkeypatch.delenv("TOKENPAK_SESSION_MAPPER", raising=False)


# ── First-turn persistence ─────────────────────────────────────────────


def test_first_turn_persists_session_id(tmp_path: Path, isolated_mapper):
    claude = _write_claude_stub(tmp_path, session_id="cli-uuid-AAA")
    backend = AnthropicOAuthBackend(claude_binary=str(claude))

    resp = backend.dispatch(_req(headers={"X-OpenClaw-Session": "oc-sess-1"}))
    assert resp.status == 200

    rec = isolated_mapper.get(
        scope="openclaw",
        external_id="oc-sess-1",
        provider="tokenpak-claude-code",
    )
    assert rec is not None
    assert rec.internal_id == "cli-uuid-AAA"


def test_first_turn_runs_without_resume_flag(tmp_path: Path, isolated_mapper):
    claude = _write_claude_stub(tmp_path)
    backend = AnthropicOAuthBackend(claude_binary=str(claude))
    backend.dispatch(_req(headers={"X-OpenClaw-Session": "oc-sess-1"}))
    argv = _read_argv(tmp_path)
    assert "--resume" not in argv
    assert "--continue" not in argv
    assert "--output-format" in argv and "json" in argv


# ── Subsequent-turn resume ────────────────────────────────────────────


def test_subsequent_turn_uses_resume(tmp_path: Path, isolated_mapper):
    claude = _write_claude_stub(tmp_path, session_id="cli-uuid-BBB")
    backend = AnthropicOAuthBackend(claude_binary=str(claude))

    # Pre-seed the mapping as if turn 1 already happened.
    isolated_mapper.set(
        scope="openclaw",
        external_id="oc-sess-9",
        provider="tokenpak-claude-code",
        internal_id="cli-uuid-BBB",
    )

    backend.dispatch(_req(headers={"X-OpenClaw-Session": "oc-sess-9"}))
    argv = _read_argv(tmp_path)
    assert "--resume" in argv
    assert "cli-uuid-BBB" in argv
    # --continue must NOT be set simultaneously.
    assert "--continue" not in argv


# ── No-platform fallback to --continue ────────────────────────────────


def test_no_platform_signal_uses_continue_default(tmp_path: Path, isolated_mapper):
    claude = _write_claude_stub(tmp_path)
    backend = AnthropicOAuthBackend(claude_binary=str(claude))
    backend.dispatch(_req(headers={}))  # no X-OpenClaw-Session
    argv = _read_argv(tmp_path)
    assert "--continue" in argv
    assert "--resume" not in argv


def test_no_platform_signal_with_opt_out_drops_continue(
    tmp_path: Path, isolated_mapper, monkeypatch
):
    monkeypatch.setenv("TOKENPAK_OAUTH_NO_CONTINUE", "1")
    claude = _write_claude_stub(tmp_path)
    backend = AnthropicOAuthBackend(claude_binary=str(claude))
    backend.dispatch(_req(headers={}))
    argv = _read_argv(tmp_path)
    assert "--continue" not in argv
    assert "--resume" not in argv


# ── Session mapper opt-out preserves first-turn semantic ──────────────


def test_session_mapper_disabled_treats_as_first_turn(
    tmp_path: Path, isolated_mapper, monkeypatch
):
    monkeypatch.setenv("TOKENPAK_SESSION_MAPPER", "0")
    claude = _write_claude_stub(tmp_path)
    backend = AnthropicOAuthBackend(claude_binary=str(claude))
    backend.dispatch(_req(headers={"X-OpenClaw-Session": "oc-sess-1"}))
    argv = _read_argv(tmp_path)
    # Origin is detected but mapper returns None, so first-turn path —
    # no --resume, no --continue.
    assert "--resume" not in argv


# ── Usage tokens are forwarded ────────────────────────────────────────


def test_response_forwards_real_usage_tokens(tmp_path: Path, isolated_mapper):
    claude = _write_claude_stub(tmp_path)
    backend = AnthropicOAuthBackend(claude_binary=str(claude))
    resp = backend.dispatch(_req(headers={"X-OpenClaw-Session": "oc-sess-1"}))
    data = json.loads(resp.body)
    # Stub emits input_tokens=12, output_tokens=3.
    assert data["usage"]["input_tokens"] == 12
    assert data["usage"]["output_tokens"] == 3


def test_response_returns_result_text(tmp_path: Path, isolated_mapper):
    claude = _write_claude_stub(tmp_path)
    backend = AnthropicOAuthBackend(claude_binary=str(claude))
    resp = backend.dispatch(_req(headers={"X-OpenClaw-Session": "oc-sess-1"}))
    data = json.loads(resp.body)
    assert data["content"][0]["text"] == "OK"
    assert data["content"][0]["type"] == "text"


def test_response_model_from_cli_modelusage(tmp_path: Path, isolated_mapper):
    claude = _write_claude_stub(tmp_path)
    backend = AnthropicOAuthBackend(claude_binary=str(claude))
    resp = backend.dispatch(_req(headers={"X-OpenClaw-Session": "oc-sess-1"}))
    data = json.loads(resp.body)
    assert data["model"] == "claude-sonnet-4-6"


# ── Graceful degradation: non-JSON stdout ─────────────────────────────


def test_non_json_stdout_falls_back_to_text(tmp_path: Path, isolated_mapper):
    """If the CLI emits plain text (old version / unexpected path), the
    backend still wraps it as a Messages response — just with zero
    tokens + no session persistence."""
    claude_path = tmp_path / "claude"
    claude_path.write_text(
        "#!/usr/bin/env bash\n"
        'echo "plain text response"\n'
    )
    claude_path.chmod(0o755)
    backend = AnthropicOAuthBackend(claude_binary=str(claude_path))
    resp = backend.dispatch(_req(headers={"X-OpenClaw-Session": "oc-sess-1"}))
    assert resp.status == 200
    data = json.loads(resp.body)
    assert "plain text response" in data["content"][0]["text"]
    # No session persisted (no session_id in stdout).
    rec = isolated_mapper.get(
        "openclaw", "oc-sess-1", "tokenpak-claude-code"
    )
    assert rec is None
