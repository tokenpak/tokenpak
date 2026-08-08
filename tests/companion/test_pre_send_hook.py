# SPDX-License-Identifier: Apache-2.0
"""Conditional prior-work hint coverage for the lean pre-send hook."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

HOOK = Path(__file__).parents[2] / "tokenpak" / "companion" / "hooks" / "pre_send.sh"
HINT = "Prior work is referenced; retrieve native memory or journal/Paks before answering."


def _run_hook(tmp_path: Path, prompt: str, *, seed_store: bool) -> subprocess.CompletedProcess[str]:
    journal_dir = tmp_path / "companion"
    journal_dir.mkdir()
    if seed_store:
        (journal_dir / "journal.db").write_bytes(b"non-empty")
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text("{}")
    payload = {
        "transcript_path": str(transcript),
        "session_id": "session-a",
        "model": "fixture-model",
        "prompt": prompt,
    }
    env = {
        **os.environ,
        "TOKENPAK_COMPANION_JOURNAL_DIR": str(journal_dir),
        "TOKENPAK_COMPANION_SHOW_COST": "0",
        "TOKENPAK_COMPANION_BUDGET": "0",
    }
    return subprocess.run(
        ["bash", str(HOOK)],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )


def test_prior_work_reference_with_store_emits_one_short_hint(tmp_path):
    result = _run_hook(tmp_path, "What did we decide in the previous session?", seed_store=True)
    assert result.returncode == 0
    assert result.stdout.strip() == HINT
    assert len(HINT.split()) <= 25


def test_multiline_prior_work_reference_with_store_emits_hint(tmp_path):
    result = _run_hook(tmp_path, "Use the notes below.\nSee the previous session.", seed_store=True)
    assert result.returncode == 0
    assert result.stdout.strip() == HINT


def test_prior_work_reference_without_store_is_silent(tmp_path):
    result = _run_hook(tmp_path, "Use the prior decision", seed_store=False)
    assert result.returncode == 0
    assert result.stdout == ""


def test_unrelated_prompt_with_store_is_silent(tmp_path):
    result = _run_hook(tmp_path, "Explain this function", seed_store=True)
    assert result.returncode == 0
    assert result.stdout == ""


def test_generic_decision_word_with_store_is_silent(tmp_path):
    result = _run_hook(tmp_path, "Explain decision trees", seed_store=True)
    assert result.returncode == 0
    assert result.stdout == ""


def test_no_match_path_adds_no_search_subprocess():
    script = HOOK.read_text()
    hint_block = script[
        script.index("# A prior-work reference") : script.index("# Token estimation")
    ]
    assert "find " not in hint_block
    assert "grep " not in hint_block
