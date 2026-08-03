# SPDX-License-Identifier: Apache-2.0
"""Tests for the PakLine v0 statusline script (statusline/pakline.sh).

Runs the script as a subprocess — pipes Claude Code statusLine JSON to stdin
and asserts on the rendered line. No Claude Code required.

PakLine v0 renders only fields sourceable truthfully today:
    📦 <task> · $<cost> · context <state> · active <duration>
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
_PAKLINE = str(_REPO_ROOT / "tokenpak" / "companion" / "statusline" / "pakline.sh")

pytestmark = pytest.mark.skipif(shutil.which("jq") is None, reason="PakLine requires jq")


def _run(payload, tmp_path: Path, extra_env: dict | None = None):
    env = os.environ.copy()
    env["TOKENPAK_COMPANION_JOURNAL_DIR"] = str(tmp_path)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", _PAKLINE],
        input=payload if isinstance(payload, str) else json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=10,
        env=env,
    )


def _seed_title(tmp_path: Path, session_id: str, text: str) -> None:
    d = tmp_path / "titles"
    d.mkdir(parents=True, exist_ok=True)
    (d / session_id).write_text(text + "\n")


# --------------------------------------------------------------------------- #
# Task field
# --------------------------------------------------------------------------- #


def test_task_from_title_state_file(tmp_path):
    _seed_title(tmp_path, "s1", "Proposal analysis")
    r = _run({"session_id": "s1", "cost": {"total_cost_usd": 0.18}}, tmp_path)
    assert r.returncode == 0
    assert r.stdout.startswith("📦 Proposal analysis")


def test_task_falls_back_to_model_when_no_title(tmp_path):
    r = _run(
        {
            "session_id": "none",
            "model": {"display_name": "Opus 4.8"},
            "cost": {"total_cost_usd": 0},
        },
        tmp_path,
    )
    assert "📦 Opus 4.8" in r.stdout


def test_task_last_resort_is_session(tmp_path):
    r = _run({"cost": {"total_cost_usd": 0}}, tmp_path)
    assert "📦 session" in r.stdout


# --------------------------------------------------------------------------- #
# Cost / context / duration
# --------------------------------------------------------------------------- #


def test_cost_formatted_two_decimals(tmp_path):
    _seed_title(tmp_path, "s", "X")
    r = _run({"session_id": "s", "cost": {"total_cost_usd": 0.1823}}, tmp_path)
    assert "$0.18" in r.stdout


def test_context_state_ok_and_high(tmp_path):
    _seed_title(tmp_path, "s", "X")
    ok = _run({"session_id": "s", "cost": {"total_cost_usd": 0}}, tmp_path)
    assert "context OK" in ok.stdout
    hi = _run(
        {"session_id": "s", "cost": {"total_cost_usd": 0}, "exceeds_200k_tokens": True},
        tmp_path,
    )
    assert "context high" in hi.stdout


def test_no_fabricated_percentage(tmp_path):
    _seed_title(tmp_path, "s", "X")
    r = _run({"session_id": "s", "cost": {"total_cost_usd": 0}}, tmp_path)
    assert "%" not in r.stdout  # v0 is state-only, never a fake context %


def test_duration_humanized(tmp_path):
    _seed_title(tmp_path, "s", "X")
    r = _run(
        {"session_id": "s", "cost": {"total_cost_usd": 0, "total_duration_ms": 754000}},
        tmp_path,
    )
    assert "active 12m" in r.stdout


def test_duration_omitted_when_zero(tmp_path):
    _seed_title(tmp_path, "s", "X")
    r = _run({"session_id": "s", "cost": {"total_cost_usd": 0, "total_duration_ms": 0}}, tmp_path)
    assert "active" not in r.stdout


# --------------------------------------------------------------------------- #
# Robustness
# --------------------------------------------------------------------------- #


def test_disabled_env_renders_nothing(tmp_path):
    r = _run(
        {"session_id": "s", "cost": {"total_cost_usd": 1}},
        tmp_path,
        extra_env={"TOKENPAK_COMPANION_PAKLINE": "0"},
    )
    assert r.stdout == ""


def test_garbage_input_degrades_silently(tmp_path):
    r = _run("not json at all", tmp_path)
    assert r.returncode == 0
    assert r.stderr == ""  # no errors leak to the statusline


def test_empty_input_no_crash(tmp_path):
    r = _run("", tmp_path)
    assert r.returncode == 0
    assert r.stderr == ""
