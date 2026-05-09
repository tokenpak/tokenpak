# SPDX-License-Identifier: MIT
"""Tests for tokenpak.agentic.state_collector."""

from __future__ import annotations


import pytest
pytest.importorskip("tokenpak.agentic.state_collector", reason="module not available in current build")
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.agentic.state_collector import (
    SCHEMA_VERSION,
    STALE_THRESHOLD_SECONDS,
    EnvState,
    FileState,
    GitState,
    ServiceState,
    StateCollector,
    StructuredState,
    TestState,
    _run,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_collector(cwd=None, known_good_env=None) -> StateCollector:
    return StateCollector(cwd=cwd or os.getcwd(), known_good_env=known_good_env or {})


# ── Test 1: Git state collected correctly in a real repo ─────────────────────


def test_collect_git_state_in_real_repo(tmp_path):
    """Git collector returns branch + tracks uncommitted files in a real repo."""
    # Init a git repo. TSR-05ac: must pass `-c user.email/-c user.name` so the
    # fixture is self-contained — CI runners ship without a global git config,
    # so without these flags `git commit` aborts with "Author identity unknown"
    # and HEAD never gets created. The collector then reports branch=None
    # because `git rev-parse HEAD` fails with "ambiguous argument 'HEAD'".
    os.system(
        f"git init {tmp_path} -q && cd {tmp_path} && "
        f"git -c user.email=test@example.com -c user.name=Test "
        f"commit --allow-empty -m 'init' -q"
    )

    collector = StateCollector(cwd=str(tmp_path))
    state = collector.collect_git_state()

    assert state.available is True
    assert state.branch is not None
    assert isinstance(state.uncommitted_count, int)
    assert state.error is None


# ── Test 2: Handles missing git repo gracefully ───────────────────────────────


def test_collect_git_state_no_repo(tmp_path):
    """Git collector degrades gracefully when not inside a git repo."""
    # tmp_path has no git repo
    collector = StateCollector(cwd=str(tmp_path))
    state = collector.collect_git_state()

    assert state.available is False
    assert state.error is not None
    assert "not a git repository" in state.error


# ── Test 3: Combined state is compact (<500 tokens) ──────────────────────────


def test_combined_state_is_compact(tmp_path):
    """StructuredState.to_json() fits within 500-token budget (~2000 chars)."""
    collector = _make_collector(cwd=str(tmp_path))
    state = collector.collect_all()

    token_estimate = state.token_estimate()
    assert token_estimate < 500, (
        f"State exceeds 500-token budget: {token_estimate} tokens\n"
        f"JSON: {state.to_json()[:300]}..."
    )


# ── Test 4: Stale state detection ─────────────────────────────────────────────


def test_stale_state_detection():
    """StructuredState.is_stale() returns True for states older than threshold."""
    # Fresh state — not stale
    fresh = StructuredState(collected_at=time.time())
    assert fresh.is_stale() is False

    # Old state — stale
    old_ts = time.time() - STALE_THRESHOLD_SECONDS - 1
    stale = StructuredState(collected_at=old_ts)
    assert stale.is_stale() is True

    # Custom threshold
    recent = StructuredState(collected_at=time.time() - 10)
    assert recent.is_stale(threshold=5) is True
    assert recent.is_stale(threshold=30) is False


# ── Test 5: Schema validation (round-trip) ────────────────────────────────────


def test_structured_state_schema_round_trip():
    """StructuredState serialises to dict and round-trips correctly."""
    original = StructuredState(
        git=GitState(branch="main", uncommitted_count=2, available=True),
        services=ServiceState(running_processes=["nginx"], open_ports=[8080]),
        env=EnvState(vars={"HOME": "/home/user"}, drift_keys=[]),
        files=FileState(recently_changed=["src/foo.py"]),
        tests=TestState(total=10, passed=9, failed=1, failing_tests=["test_bar"]),
        errors=["services: timeout"],
    )

    d = original.to_dict()
    assert d["schema_version"] == SCHEMA_VERSION
    assert d["git"]["branch"] == "main"
    assert d["services"]["running_processes"] == ["nginx"]
    assert d["tests"]["failing_tests"] == ["test_bar"]
    assert "errors" in d

    # from_dict round-trip
    restored = StructuredState.from_dict(d)
    assert restored.git.branch == "main"
    assert restored.services.open_ports == [8080]
    assert restored.tests.failed == 1


# ── Test 6: Env collector captures watched vars ───────────────────────────────


def test_collect_env_state_captures_vars():
    """Env collector captures watched keys that are set in the environment."""
    with patch.dict(os.environ, {"HOME": "/home/testuser", "USER": "testuser"}):
        collector = _make_collector()
        state = collector.collect_env_state()

    assert state.available is True
    assert "HOME" in state.vars
    assert state.vars["HOME"] == "/home/testuser"


# ── Test 7: Drift detection ───────────────────────────────────────────────────


def test_collect_env_state_drift_detection():
    """Drift keys are reported when current env differs from known-good baseline."""
    known_good = {"HOME": "/home/expected"}
    with patch.dict(os.environ, {"HOME": "/home/different"}):
        collector = StateCollector(known_good_env=known_good)
        state = collector.collect_env_state()

    assert "HOME" in state.drift_keys


# ── Test 8: collect_all returns StructuredState with schema version ───────────


def test_collect_all_returns_structured_state(tmp_path):
    """collect_all() returns a StructuredState with correct schema version."""
    collector = _make_collector(cwd=str(tmp_path))
    state = collector.collect_all()

    assert isinstance(state, StructuredState)
    assert state.schema_version == SCHEMA_VERSION
    assert isinstance(state.errors, list)
    assert isinstance(state.git, GitState)
    assert isinstance(state.services, ServiceState)
    assert isinstance(state.env, EnvState)
    assert isinstance(state.files, FileState)
    assert isinstance(state.tests, TestState)


# ── Test 9: Test state reads pytest cache ─────────────────────────────────────


def test_collect_test_state_reads_pytest_cache(tmp_path):
    """TestState collector reads failing tests from .pytest_cache/lastfailed."""
    cache_dir = tmp_path / ".pytest_cache" / "v" / "cache"
    cache_dir.mkdir(parents=True)
    lastfailed = {"tests/test_foo.py::test_bar": True, "tests/test_baz.py::test_qux": True}
    (cache_dir / "lastfailed").write_text(json.dumps(lastfailed))

    collector = StateCollector(cwd=str(tmp_path))
    state = collector.collect_test_state()

    assert state.available is True
    assert len(state.failing_tests) == 2
    assert "tests/test_foo.py::test_bar" in state.failing_tests


# ── Test 10: _run handles command-not-found gracefully ───────────────────────


def test_run_handles_missing_command():
    """_run returns non-zero rc and error string when command is missing."""
    rc, out, err = _run(["this_command_does_not_exist_12345"])
    assert rc != 0
    assert "not found" in err or err != ""


