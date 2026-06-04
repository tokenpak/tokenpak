# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak._paths — Std 33 §2 resolution + §5 under() contract.

Covers P-PATHS-01a (resolver + fail-loud subdir enum) and P-PATHS-01b
(``dispatch`` subdir extension).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tokenpak import _paths


@pytest.fixture
def fake_home(tmp_path, monkeypatch):
    """Isolate HOME and clear the TOKENPAK_HOME override for each test."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.delenv(_paths.ENV_VAR, raising=False)
    return tmp_path


# ---------------------------------------------------------------------------
# Std 33 §2 resolution order
# ---------------------------------------------------------------------------


def test_resolved_home_env_override(fake_home, monkeypatch):
    override = fake_home / "sandbox-home"
    monkeypatch.setenv(_paths.ENV_VAR, str(override))
    assert _paths.resolved_home() == override
    assert _paths.home() == override  # alias parity


def test_resolved_home_canonical_when_present(fake_home):
    (fake_home / _paths.CANONICAL_DIRNAME).mkdir()
    assert _paths.resolved_home() == fake_home / _paths.CANONICAL_DIRNAME


def test_resolved_home_legacy_only_when_canonical_absent(fake_home):
    (fake_home / _paths.LEGACY_DIRNAME).mkdir()
    # canonical absent + legacy present -> legacy
    assert _paths.resolved_home() == fake_home / _paths.LEGACY_DIRNAME
    assert _paths.is_legacy() is True


def test_resolved_home_prefers_canonical_over_legacy(fake_home):
    (fake_home / _paths.CANONICAL_DIRNAME).mkdir()
    (fake_home / _paths.LEGACY_DIRNAME).mkdir()
    assert _paths.resolved_home() == fake_home / _paths.CANONICAL_DIRNAME
    assert _paths.is_legacy() is False


def test_resolved_home_canonical_when_neither_and_does_not_create(fake_home):
    canonical = fake_home / _paths.CANONICAL_DIRNAME
    assert _paths.resolved_home() == canonical
    assert not canonical.exists()  # read-only resolution


# ---------------------------------------------------------------------------
# ensure_home() — mode 0700, idempotent
# ---------------------------------------------------------------------------


def test_ensure_home_creates_with_mode_0700(fake_home):
    h = _paths.ensure_home()
    assert h.exists()
    assert (os.stat(h).st_mode & 0o777) == 0o700


def test_ensure_home_idempotent(fake_home):
    first = _paths.ensure_home()
    second = _paths.ensure_home()
    assert first == second
    assert second.exists()


# ---------------------------------------------------------------------------
# Std 33 §5 under() — allowed subdirs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subdir", ["templates", "companion", "pro"])
def test_under_allows_std33_subdirs(fake_home, subdir):
    assert _paths.under(subdir) == _paths.resolved_home() / subdir


def test_under_allows_adopted_subdir_paks(fake_home):
    # paks/ is in active use (pak.py) though not yet in Std 33 §3 text.
    assert _paths.under("paks") == _paths.resolved_home() / "paks"


def test_under_allows_multi_segment_under_subdir(fake_home):
    assert _paths.under("companion", "journal.db") == (
        _paths.resolved_home() / "companion" / "journal.db"
    )
    assert _paths.under("pro", "state", "multipak") == (
        _paths.resolved_home() / "pro" / "state" / "multipak"
    )


@pytest.mark.parametrize(
    "fname",
    ["config.json", "config.yaml", "license.json", "pricing.json", "alert_state.json"],
)
def test_under_allows_top_level_files(fake_home, fname):
    # Std 33 §5 sanctions under("file") for top-level files.
    assert _paths.under(fname) == _paths.resolved_home() / fname


# ---------------------------------------------------------------------------
# Std 33 §5 under() — fail-loud on unknown subdirs
# ---------------------------------------------------------------------------


def test_under_rejects_unknown_subdir(fake_home):
    with pytest.raises(ValueError, match="unknown TokenPak home subdir"):
        _paths.under("nonexistent")


def test_under_rejects_typod_subdir(fake_home):
    with pytest.raises(ValueError):
        _paths.under("compaion")  # typo of "companion"


def test_under_rejects_empty(fake_home):
    with pytest.raises(ValueError):
        _paths.under()


def test_under_does_not_create_directory(fake_home):
    p = _paths.under("companion")
    assert not p.exists()  # pure-path helper


# ---------------------------------------------------------------------------
# dispatch/ subdir (Std 33 §3 amendment, approved 2026-05-20)
# ---------------------------------------------------------------------------


def test_under_allows_dispatch(fake_home):
    """dispatch/ is a canonical layout subdir."""
    assert _paths.under("dispatch") == _paths.resolved_home() / "dispatch"


def test_under_allows_dispatch_multi_segment(fake_home):
    assert _paths.under("dispatch", "runs.db") == (
        _paths.resolved_home() / "dispatch" / "runs.db"
    )
