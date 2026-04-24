# SPDX-License-Identifier: Apache-2.0
"""AnthropicOAuthBackend — `claude --continue` session continuity (Part 2b).

Kevin's 2026-04-24 ratification: the OAuth backend passes `--continue`
to the `claude` CLI so every OpenClaw / companion-path request resumes
the last session on this machine instead of opening a fresh one per
request. Single-agent semantics were explicitly accepted.

Tests use a fake `claude` binary (bash stub) that echoes its argv so we
can assert `--continue` made it into the subprocess call.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tokenpak.services.request import Request
from tokenpak.services.routing_service.backends.anthropic_oauth import (
    AnthropicOAuthBackend,
)


def _write_fake_claude(bin_dir: Path) -> Path:
    """Write a bash stub that prints its argv then exits 0."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / "claude"
    path.write_text(
        "#!/usr/bin/env bash\n"
        'printf "ARGV: %s\\n" "$@"\n'
    )
    path.chmod(0o755)
    return path


def _body() -> bytes:
    return json.dumps(
        {"messages": [{"role": "user", "content": "ping"}]}
    ).encode()


@pytest.fixture(autouse=True)
def _clear_opt_out(monkeypatch):
    # Baseline: opt-out flag not set — backend should pass --continue.
    monkeypatch.delenv("TOKENPAK_OAUTH_NO_CONTINUE", raising=False)


def test_oauth_backend_passes_continue_flag(tmp_path: Path):
    claude = _write_fake_claude(tmp_path)
    be = AnthropicOAuthBackend(claude_binary=str(claude))
    resp = be.dispatch(Request(body=_body(), headers={}))
    assert resp.status == 200
    stdout = resp.body.decode()
    # Body is JSON-wrapped; the CLI's ARGV echo appears inside content[0].text.
    data = json.loads(stdout)
    cli_output = data["content"][0]["text"]
    assert "--continue" in cli_output
    assert "--print" in cli_output
    assert "ping" in cli_output


def test_oauth_backend_opt_out_env_drops_continue(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TOKENPAK_OAUTH_NO_CONTINUE", "1")
    claude = _write_fake_claude(tmp_path)
    be = AnthropicOAuthBackend(claude_binary=str(claude))
    resp = be.dispatch(Request(body=_body(), headers={}))
    assert resp.status == 200
    data = json.loads(resp.body.decode())
    cli_output = data["content"][0]["text"]
    assert "--continue" not in cli_output
    assert "--print" in cli_output


def test_oauth_backend_missing_binary_returns_502():
    be = AnthropicOAuthBackend(claude_binary="/nonexistent/path/to/claude")
    resp = be.dispatch(Request(body=_body(), headers={}))
    assert resp.status == 502
    assert b"backend_unavailable" in resp.body
