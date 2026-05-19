"""Codex-side hook tests (audit deltas hooks #3 + hooks #5).

Mirrors the shape of tests/companion/test_hooks.py but targets the
Codex-side bash hooks under tokenpak/companion/codex/.

Covered audit deltas:

- hooks #3 — JSON hookSpecificOutput block on UserPromptSubmit budget
  block. Landed on PR-45 (commit ad968849d4); tests preserved here.
- hooks #5 — declarative event table at module top (no per-event
  function body). Asserted via the new
  ``test_hook_events_is_declarative_table`` set.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tokenpak.companion.codex import hooks as codex_hooks

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CODEX_BASH_HOOK = str(_REPO_ROOT / "tokenpak" / "companion" / "codex" / "hooks_pre_send.sh")

_SIX_FIELD_INPUT = {
    "session_id": "test-session-codex-hooks",
    "transcript_path": "",
    "cwd": "/tmp",
    "hook_event_name": "UserPromptSubmit",
    "model": "sonnet",
    "prompt": "hello",
}


def _make_transcript(path: Path, size_bytes: int = 5000) -> Path:
    content = json.dumps({"type": "user", "content": "hello world " * 10}) + "\n"
    line_size = len(content.encode())
    lines = max(1, size_bytes // line_size)
    path.write_text(content * lines)
    return path


def _run_codex_bash_hook(
    hook_input: dict,
    tmp_path: Path,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["TOKENPAK_COMPANION_ENABLED"] = "1"
    env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(tmp_path)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", _CODEX_BASH_HOOK],
        input=json.dumps(hook_input),
        capture_output=True,
        text=True,
        timeout=15,
        cwd=_REPO_ROOT,
        env=env,
    )


# ──────────────────────────────────────────────────────────────
# hooks #3 — pre-existing PR-45 contract, unchanged.
# ──────────────────────────────────────────────────────────────


def test_codex_bash_hook_block_outputs_json_decision(tmp_path):
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=200_000)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_codex_bash_hook(
        hook_input,
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0.0001"},
    )
    assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
    decision = json.loads(result.stdout.strip())
    assert decision["hookSpecificOutput"]["decision"] == "block"
    assert decision["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert "budget" in decision["hookSpecificOutput"]["reason"].lower()


def test_codex_bash_hook_block_stderr_preserved(tmp_path):
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=200_000)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_codex_bash_hook(
        hook_input,
        tmp_path=tmp_path,
        extra_env={"TOKENPAK_COMPANION_BUDGET": "0.0001"},
    )
    assert result.returncode == 2
    assert "budget" in result.stderr.lower()


def test_codex_bash_hook_allow_no_budget(tmp_path):
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=20_000)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_codex_bash_hook(hook_input, tmp_path=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == ""


# ──────────────────────────────────────────────────────────────
# hooks #5 — declarative event table at module top.
# ──────────────────────────────────────────────────────────────


def test_hook_events_is_declarative_table():
    """`_TOKENPAK_HOOK_EVENTS` lives at module top with current events.

    Adding a new event must be possible by appending to the table —
    no per-event code branch should be needed. Closes audit delta
    hooks #5.
    """
    table = codex_hooks._TOKENPAK_HOOK_EVENTS
    assert isinstance(table, dict)
    assert set(table.keys()) == {"UserPromptSubmit", "Stop"}
    for event, group in table.items():
        assert "hooks" in group, f"{event}: missing hooks key"
        assert isinstance(group["hooks"], list) and group["hooks"], f"{event}: hooks list empty"
        for entry in group["hooks"]:
            assert entry.get("type") == "command", f"{event}: non-command hook"
            assert codex_hooks.TOKENPAK_HOOK_MARKER in entry.get("command", ""), (
                f"{event}: command missing tokenpak marker"
            )


def test_tokenpak_hook_events_accessor_returns_table():
    """Back-compat accessor returns the same dict as the module constant."""
    assert codex_hooks._tokenpak_hook_events() is codex_hooks._TOKENPAK_HOOK_EVENTS


def test_generate_hooks_json_includes_table_events():
    """generate_hooks_json() emits Codex's documented shape for each event."""
    hooks_json = codex_hooks.generate_hooks_json()
    assert set(hooks_json["hooks"].keys()) == {"UserPromptSubmit", "Stop"}
    for event, groups in hooks_json["hooks"].items():
        assert isinstance(groups, list) and len(groups) == 1, event
        assert "hooks" in groups[0], event
