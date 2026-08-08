# SPDX-License-Identifier: Apache-2.0
"""Conditional prior-work hint coverage for the lean pre-send hook."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from tokenpak.companion.journal.store import JournalStore

HOOK = Path(__file__).parents[2] / "tokenpak" / "companion" / "hooks" / "pre_send.sh"
HINT = "Prior work is referenced; retrieve native memory or journal/Paks before answering."


def _run_hook(tmp_path: Path, prompt: str, *, store_state: str) -> subprocess.CompletedProcess[str]:
    """Run the hook against a journal dir in one of four real store states.

    ``absent``            — no journal.db at all
    ``initialized_empty`` — real schema created, zero entries
    ``with_entry``        — real schema plus one journal entry
    ``capsules_only``     — no journal.db, one Pak file under capsules/
    """
    journal_dir = tmp_path / "companion"
    journal_dir.mkdir()
    if store_state in {"initialized_empty", "with_entry"}:
        store = JournalStore(journal_dir / "journal.db")
        store.start_session("session-a", project_dir=str(tmp_path), model="fixture-model")
        if store_state == "with_entry":
            store.add_entry("session-a", "decision", "recorded a real journal entry")
    elif store_state == "capsules_only":
        capsules = journal_dir / "capsules"
        capsules.mkdir()
        (capsules / "session-a.md").write_text("# Pak\nprior work content\n")
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


def test_prior_work_reference_with_entry_emits_one_short_hint(tmp_path):
    result = _run_hook(
        tmp_path, "What did we decide in the previous session?", store_state="with_entry"
    )
    assert result.returncode == 0
    assert result.stdout.strip() == HINT
    assert len(HINT.split()) <= 25


def test_multiline_prior_work_reference_with_entry_emits_hint(tmp_path):
    result = _run_hook(
        tmp_path, "Use the notes below.\nSee the previous session.", store_state="with_entry"
    )
    assert result.returncode == 0
    assert result.stdout.strip() == HINT


def test_initialized_empty_journal_is_silent(tmp_path):
    """Schema initialization alone is an empty store — the hint must not fire."""
    result = _run_hook(
        tmp_path, "What did we decide in the previous session?", store_state="initialized_empty"
    )
    assert result.returncode == 0
    assert result.stdout == ""


def test_prior_work_reference_without_store_is_silent(tmp_path):
    result = _run_hook(tmp_path, "Use the prior decision", store_state="absent")
    assert result.returncode == 0
    assert result.stdout == ""


def test_capsules_only_store_emits_hint(tmp_path):
    result = _run_hook(tmp_path, "Continue the prior migration", store_state="capsules_only")
    assert result.returncode == 0
    assert result.stdout.strip() == HINT


def test_unrelated_prompt_with_entry_is_silent(tmp_path):
    result = _run_hook(tmp_path, "Explain this function", store_state="with_entry")
    assert result.returncode == 0
    assert result.stdout == ""


def test_generic_decision_word_with_entry_is_silent(tmp_path):
    result = _run_hook(tmp_path, "Explain decision trees", store_state="with_entry")
    assert result.returncode == 0
    assert result.stdout == ""


def test_first_entry_writes_nonempty_marker(tmp_path):
    db_path = tmp_path / "journal.db"
    store = JournalStore(db_path)
    marker = tmp_path / "journal.db.nonempty"
    assert not marker.exists(), "schema init alone must not create the marker"
    store.add_entry("session-a", "decision", "first real entry")
    assert marker.exists(), "first entry must create the marker"


def test_no_match_path_adds_no_search_subprocess():
    script = HOOK.read_text()
    hint_block = script[
        script.index("# A prior-work reference") : script.index("# Token estimation")
    ]
    assert "find " not in hint_block
    assert "grep " not in hint_block
