"""Codex-side hook tests (audit delta hooks #3 + scaffold for L2 wiring).

Mirrors the shape of tests/companion/test_hooks.py but targets the
Codex-side bash hook at tokenpak/companion/codex/hooks_pre_send.sh.

The pre-existing test_hooks.py::test_bash_hook_block_outputs_json_decision
asserts JSON-block emission against the *Claude-side* pre_send.sh; this
file asserts the same contract against the *Codex-side* hooks_pre_send.sh
(audit delta hooks #3: "Bash UserPromptSubmit hook does not emit the JSON
hookSpecificOutput block on budget block").
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CODEX_BASH_HOOK = str(
    _REPO_ROOT / "tokenpak" / "companion" / "codex" / "hooks_pre_send.sh"
)

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


def test_codex_bash_hook_block_outputs_json_decision(tmp_path):
    """Codex-side bash hook emits hookSpecificOutput JSON on budget block.

    Audit delta hooks #3 (L1 audit 2026-05-19, P1-CODEX-COMPANION-TIP-L2):
    closes the gap where stderr+exit 2 was emitted but the structured
    block-decision shape was missing.
    """
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
    """Codex-side bash hook keeps stderr + exit 2 alongside the JSON block."""
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
    """No budget → no block, no JSON, exit 0."""
    transcript_path = tmp_path / "session.jsonl"
    _make_transcript(transcript_path, size_bytes=20_000)
    hook_input = dict(_SIX_FIELD_INPUT)
    hook_input["transcript_path"] = str(transcript_path)
    result = _run_codex_bash_hook(hook_input, tmp_path=tmp_path)
    assert result.returncode == 0
    assert result.stdout.strip() == ""
