# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak._paths — home resolution and the under() contract.

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
# Home resolution order
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
# under() — allowed subdirs
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("subdir", ["templates", "companion", "pro"])
def test_under_allows_std33_subdirs(fake_home, subdir):
    assert _paths.under(subdir) == _paths.resolved_home() / subdir


def test_under_allows_adopted_subdir_paks(fake_home):
    # paks/ is in active use (pak.py) though not yet in the canonical layout.
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
    # The resolver contract sanctions under("file") for top-level files.
    assert _paths.under(fname) == _paths.resolved_home() / fname


# ---------------------------------------------------------------------------
# under() — fail-loud on unknown subdirs
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
# dispatch/ subdir (canonical layout amendment, approved 2026-05-20)
# ---------------------------------------------------------------------------


def test_under_allows_dispatch(fake_home):
    """dispatch/ is a canonical layout subdir."""
    assert _paths.under("dispatch") == _paths.resolved_home() / "dispatch"


def test_under_allows_dispatch_multi_segment(fake_home):
    assert _paths.under("dispatch", "runs.db") == (_paths.resolved_home() / "dispatch" / "runs.db")


# ---------------------------------------------------------------------------
# Legacy-tree resolution stability.
#
# The suite previously exercised ensure_home() only against a clean install, so
# nothing covered the case that matters most: a machine that already has state
# in ~/.tokenpak. Creating the canonical directory there used to flip home()
# onto an empty directory, and every read followed it — including the license,
# which meant activating Pro on an upgraded install read back as Free.
# ---------------------------------------------------------------------------


@pytest.fixture
def legacy_install(fake_home):
    """A home that already holds state in the legacy location."""
    legacy = fake_home / _paths.LEGACY_DIRNAME
    legacy.mkdir()
    (legacy / "config.yaml").write_text("mode: hybrid\n")
    (legacy / "license.json").write_text("{}\n")
    return legacy


def test_ensure_home_does_not_move_a_legacy_install(legacy_install):
    """ensure_home() must not relocate an install that already has state."""
    before = _paths.home()
    _paths.ensure_home()
    assert _paths.home() == before == legacy_install


def test_ensure_home_hardens_the_home_that_is_actually_used(legacy_install):
    """The 0700 guarantee has to land on the directory holding the secrets."""
    legacy_install.chmod(0o775)
    _paths.ensure_home()
    assert (legacy_install.stat().st_mode & 0o777) == 0o700


def test_reads_and_writes_agree_on_a_legacy_install(legacy_install):
    """A split resolver is what produced the proxy.pid and license defects."""
    assert _paths.home() == _paths.write_home() == legacy_install
    _paths.ensure_home()
    assert _paths.home() == _paths.write_home() == legacy_install


def test_empty_canonical_does_not_outrank_populated_legacy(legacy_install):
    """Existence is not evidence: an empty ~/.tpk means nothing lives there."""
    (legacy_install.parent / _paths.CANONICAL_DIRNAME).mkdir()
    assert _paths.home() == legacy_install
    assert _paths.under("license.json") == legacy_install / "license.json"


def test_populated_canonical_wins_over_legacy(legacy_install):
    """Once state exists canonically — after migration — canonical leads."""
    canonical = legacy_install.parent / _paths.CANONICAL_DIRNAME
    canonical.mkdir()
    (canonical / "config.yaml").write_text("mode: hybrid\n")
    assert _paths.home() == canonical
    assert _paths.write_home() == canonical


def test_fresh_install_still_starts_canonical(fake_home):
    """The original defect: a first-run marker must not pin a new install."""
    _paths.ensure_home()
    (_paths.write_home() / ".seen_intro").write_text("x")
    assert _paths.home() == fake_home / _paths.CANONICAL_DIRNAME
    assert _paths.write_home() == fake_home / _paths.CANONICAL_DIRNAME


def test_empty_legacy_only_still_follows_presence_order(fake_home):
    """The presence order is unchanged where neither home holds state."""
    (fake_home / _paths.LEGACY_DIRNAME).mkdir()
    assert _paths.home() == fake_home / _paths.LEGACY_DIRNAME
    # ...but nothing new is started there.
    assert _paths.write_home() == fake_home / _paths.CANONICAL_DIRNAME


# ---------------------------------------------------------------------------
# The read/write split must not let a leftover directory capture an install.
#
# Keying resolution on content fixed one bug and opened another: if *any* file
# counts as state, then anything that writes into a stale ~/.tokenpak converts
# a canonical install to a legacy one, permanently. These pin both halves —
# what counts as state, and that writes never go through the read resolver.
# ---------------------------------------------------------------------------


def test_stray_file_does_not_make_a_home(fake_home):
    """A Finder visit or an editor lockfile is not an installation."""
    canonical = fake_home / _paths.CANONICAL_DIRNAME
    canonical.mkdir()
    (canonical / ".DS_Store").write_bytes(b"\x00")
    legacy = fake_home / _paths.LEGACY_DIRNAME
    legacy.mkdir()
    (legacy / "license.json").write_text("{}")

    assert _paths.home() == legacy
    assert _paths.write_home() == legacy


def test_stray_file_does_not_capture_a_canonical_install(fake_home):
    """The same rule in the other direction — junk in legacy takes nothing."""
    canonical = fake_home / _paths.CANONICAL_DIRNAME
    canonical.mkdir()
    (canonical / "config.yaml").write_text("mode: hybrid\n")
    legacy = fake_home / _paths.LEGACY_DIRNAME
    legacy.mkdir()
    (legacy / "some-unrelated-file").write_text("x")

    assert _paths.home() == canonical
    assert _paths.write_home() == canonical


def test_empty_legacy_never_captures_new_writes(fake_home):
    """A leftover empty ~/.tokenpak must not decide where new state goes."""
    (fake_home / _paths.LEGACY_DIRNAME).mkdir()

    assert _paths.write_home() == fake_home / _paths.CANONICAL_DIRNAME
    assert _paths.write_under("license.json") == (
        fake_home / _paths.CANONICAL_DIRNAME / "license.json"
    )


def test_write_under_never_returns_legacy_for_a_new_install(fake_home):
    (fake_home / _paths.LEGACY_DIRNAME).mkdir()
    for name in ("license.json", "config.yaml", "proxy.pid"):
        assert _paths.write_under(name).parent == fake_home / _paths.CANONICAL_DIRNAME


def test_write_under_follows_an_existing_legacy_install(fake_home):
    legacy = fake_home / _paths.LEGACY_DIRNAME
    legacy.mkdir()
    (legacy / "config.yaml").write_text("mode: hybrid\n")
    assert _paths.write_under("license.json") == legacy / "license.json"


def test_write_under_honours_the_override(fake_home, monkeypatch):
    override = fake_home / "sandbox"
    monkeypatch.setenv(_paths.ENV_VAR, str(override))
    assert _paths.write_under("license.json") == override / "license.json"


def test_write_under_is_fail_loud_like_under(fake_home):
    with pytest.raises(ValueError):
        _paths.write_under("compaion")


# ---------------------------------------------------------------------------
# The migration advisory has to agree with resolution, or the users this
# rescues are never told they are on legacy paths.
# ---------------------------------------------------------------------------


def test_migration_advisory_fires_for_a_rescued_legacy_install(fake_home):
    legacy = fake_home / _paths.LEGACY_DIRNAME
    legacy.mkdir()
    (legacy / "config.yaml").write_text("mode: hybrid\n")
    (fake_home / _paths.CANONICAL_DIRNAME).mkdir()  # empty — the rescued shape

    assert _paths.home() == legacy
    assert _paths.is_legacy_active() is True
    assert _paths.needs_migration() is True


def test_migration_advisory_silent_on_a_canonical_install(fake_home):
    canonical = fake_home / _paths.CANONICAL_DIRNAME
    canonical.mkdir()
    (canonical / "config.yaml").write_text("mode: hybrid\n")

    assert _paths.is_legacy_active() is False
    assert _paths.needs_migration() is False


def test_empty_legacy_that_reads_resolve_to_is_still_worth_migrating(fake_home):
    """The one shape where reads and writes disagree — say so.

    A leftover empty ``~/.tokenpak`` is where reads land (presence order) while
    writes correctly start canonical. Nothing is lost, but the two resolvers
    disagree until it is moved or removed, so the advisory fires.
    """
    (fake_home / _paths.LEGACY_DIRNAME).mkdir()

    assert _paths.home() == fake_home / _paths.LEGACY_DIRNAME
    assert _paths.write_home() == fake_home / _paths.CANONICAL_DIRNAME
    assert _paths.needs_migration() is True
    assert _paths.is_legacy_active() is True
