# SPDX-License-Identifier: Apache-2.0
"""Skills installer must write to the canonical $HOME/.agents/skills path.

Background: pre-L3 installs landed at ``~/.codex/skills``, which Codex
does not scan (its discovery paths are ``.agents/skills`` and
``$HOME/.agents/skills``).  Those installs were effectively dead.  This
test pins the canonical target and the defensive dual-path uninstall.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from tokenpak.companion.codex import skills_installer as si

# ---------------------------------------------------------------------------
# Runtime default — spec-canonical user path
# ---------------------------------------------------------------------------


def test_default_target_is_agents_skills_not_codex_skills():
    assert si._default_skills_root() == Path.home() / ".agents" / "skills"
    assert si._default_skills_root() != Path.home() / ".codex" / "skills"


def test_legacy_target_is_pre_l3_codex_skills_path():
    assert si._legacy_skills_root() == Path.home() / ".codex" / "skills"


# ---------------------------------------------------------------------------
# install_skills uses the new path when no target_dir is provided
# ---------------------------------------------------------------------------


def test_install_skills_writes_to_explicit_target(tmp_path: Path):
    target = tmp_path / "agents_skills"
    installed = si.install_skills(target_dir=target)
    assert installed, "expected at least one bundled skill"
    for path in installed:
        assert path.parent == target
        assert (path / "SKILL.md").exists()


def test_install_skills_default_target_is_dot_agents(monkeypatch, tmp_path: Path):
    """When called with no target, install_skills() must land under the
    canonical ``$HOME/.agents/skills`` location."""
    fake_home = tmp_path / "fakehome"
    fake_home.mkdir()
    monkeypatch.setenv("HOME", str(fake_home))
    # An override keeps this test independent from the process user's home.
    new_default = fake_home / ".agents" / "skills"
    monkeypatch.setattr(si, "_DEFAULT_TARGET", new_default)
    installed = si.install_skills()
    assert installed
    for path in installed:
        assert str(path).startswith(str(new_default)), (
            f"skill {path} not under canonical default {new_default}"
        )


# ---------------------------------------------------------------------------
# uninstall_skills sweeps BOTH the new and legacy paths by default
# ---------------------------------------------------------------------------


def test_uninstall_skills_sweeps_both_targets(monkeypatch, tmp_path: Path):
    new_target = tmp_path / "agents" / "skills"
    legacy_target = tmp_path / "codex" / "skills"
    monkeypatch.setattr(si, "_DEFAULT_TARGET", new_target)
    monkeypatch.setattr(si, "_LEGACY_TARGET", legacy_target)

    # Plant skills at BOTH locations as if a user upgraded from pre-L3.
    si.install_skills(target_dir=new_target)
    si.install_skills(target_dir=legacy_target)
    assert any(new_target.iterdir())
    assert any(legacy_target.iterdir())

    removed = si.uninstall_skills()
    # Every bundled skill should appear exactly once in `removed`, and
    # both target trees should be empty of bundled skills.
    assert sorted(removed) == sorted(si.bundled_skill_names())
    for name in si.bundled_skill_names():
        assert not (new_target / name).exists()
        assert not (legacy_target / name).exists()


def test_uninstall_skills_with_explicit_target_does_not_sweep_legacy(monkeypatch, tmp_path: Path):
    new_target = tmp_path / "agents" / "skills"
    legacy_target = tmp_path / "codex" / "skills"
    monkeypatch.setattr(si, "_DEFAULT_TARGET", new_target)
    monkeypatch.setattr(si, "_LEGACY_TARGET", legacy_target)

    si.install_skills(target_dir=new_target)
    si.install_skills(target_dir=legacy_target)

    # Explicit target_dir disables the defensive dual-sweep — the caller
    # asked for a specific directory and gets only that.
    si.uninstall_skills(target_dir=new_target)
    assert not any(new_target.iterdir())
    assert any(legacy_target.iterdir()), "legacy path should be untouched"


# ---------------------------------------------------------------------------
# orphaned_legacy_skills surfaces pre-L3 installs for doctor reporting
# ---------------------------------------------------------------------------


def test_orphaned_legacy_skills_empty_when_nothing_at_legacy_path(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(si, "_LEGACY_TARGET", tmp_path / "nowhere")
    assert si._orphaned_legacy_skills() == []


def test_orphaned_legacy_skills_lists_installed_pre_l3_skills(monkeypatch, tmp_path: Path):
    legacy_target = tmp_path / "codex_skills"
    monkeypatch.setattr(si, "_LEGACY_TARGET", legacy_target)
    si.install_skills(target_dir=legacy_target)

    orphans = si._orphaned_legacy_skills()
    assert orphans == si.bundled_skill_names(), (
        "every bundled skill installed at the legacy path should appear "
        "as an orphan until uninstall + reinstall"
    )


# ---------------------------------------------------------------------------
# Atomic / concurrent install hardening (regression-repair packet req #3):
# a racing pair of launcher starts must never leave a half-copied or
# missing skill, and crash leftovers must be swept without failing.
# ---------------------------------------------------------------------------


def _bundled_dir_with(tmp_path: Path, names: list[str]) -> Path:
    bundled = tmp_path / "bundled"
    for name in names:
        skill = bundled / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"# {name}\n")
        # A second file so a half-copy would be observable as an
        # incomplete tree, not just a missing SKILL.md.
        (skill / "body.md").write_text("payload\n" * 50)
    return bundled


def test_install_leaves_no_temp_or_backup_dirs(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(si, "_BUNDLED_SKILLS", _bundled_dir_with(tmp_path, ["a", "b"]))
    target = tmp_path / "agents" / "skills"
    si.install_skills(target_dir=target)
    # The published directory Codex scans holds only real skills — no
    # stage/backup bookkeeping (the lock sentinel lives in the parent).
    leftovers = [
        p.name
        for p in target.iterdir()
        if p.name.startswith(si._STAGE_PREFIX) or p.name.startswith(si._BACKUP_PREFIX)
    ]
    assert leftovers == [], f"unexpected temp/backup dirs: {leftovers}"
    assert sorted(p.name for p in target.iterdir()) == ["a", "b"]


def test_install_sweeps_stale_stage_and_backup(monkeypatch, tmp_path: Path):
    import os

    monkeypatch.setattr(si, "_BUNDLED_SKILLS", _bundled_dir_with(tmp_path, ["a"]))
    target = tmp_path / "agents" / "skills"
    target.mkdir(parents=True)
    stale_stage = target / f"{si._STAGE_PREFIX}a-4242"
    stale_stage.mkdir()
    (stale_stage / "junk").write_text("x")
    stale_backup = target / f"{si._BACKUP_PREFIX}a-4242"
    stale_backup.mkdir()
    # Leftovers from a *crashed prior* install are old; age them past the
    # reclamation gate so a normal launch sweeps them.
    old = time.time() - (si._RECLAIM_MIN_AGE_S + 60)
    for stale in (stale_stage, stale_backup):
        os.utime(stale, (old, old))

    si.install_skills(target_dir=target)

    assert not stale_stage.exists(), "stale stage dir was not swept"
    assert not stale_backup.exists(), "stale backup dir was not swept"
    assert (target / "a" / "SKILL.md").exists()


def test_recent_retired_generation_is_not_reclaimed_but_aged_one_is(monkeypatch, tmp_path: Path):
    """The age gate keeps a freshly-retired generation (a reader may still
    hold it) and reclaims one older than the threshold."""
    import os

    monkeypatch.setattr(si, "_BUNDLED_SKILLS", _bundled_dir_with(tmp_path, ["a"]))
    target = tmp_path / "agents" / "skills"
    target.mkdir(parents=True)

    young = target / f"{si._BACKUP_PREFIX}a-young"
    young.mkdir()
    (young / "SKILL.md").write_text("# a\n")
    aged = target / f"{si._BACKUP_PREFIX}a-aged"
    aged.mkdir()
    old = time.time() - (si._RECLAIM_MIN_AGE_S + 60)
    os.utime(aged, (old, old))

    si.install_skills(target_dir=target)

    assert young.exists(), "a recently-retired generation must be retained"
    assert not aged.exists(), "an aged retired generation must be reclaimed"
    assert (target / "a" / "SKILL.md").exists()


def test_concurrent_installs_never_expose_partial_skill(monkeypatch, tmp_path: Path):
    import threading

    monkeypatch.setattr(si, "_BUNDLED_SKILLS", _bundled_dir_with(tmp_path, ["a", "b", "c"]))
    target = tmp_path / "agents" / "skills"
    target.mkdir(parents=True)

    import os

    errors: list[BaseException] = []
    stop = threading.Event()
    observed_partial: list[tuple[str, list[str]]] = []

    def _installer() -> None:
        try:
            for _ in range(8):
                si.install_skills(target_dir=target)
        except BaseException as exc:  # noqa: BLE001 - surfaced to the test
            errors.append(exc)

    def _reader() -> None:
        # Continuously read the published tree.  A skill dir may be briefly
        # ABSENT during the atomic rename swap (acceptable per the design),
        # but it must NEVER be observed present-but-incomplete: a single
        # ``listdir`` snapshot of ``target/name`` either fails (mid-swap
        # absence) or returns the fully-populated tree.
        while not stop.is_set():
            for name in ("a", "b", "c"):
                try:
                    entries = set(os.listdir(target / name))
                except FileNotFoundError:
                    continue  # briefly absent during the rename swap — fine
                if not {"SKILL.md", "body.md"} <= entries:
                    observed_partial.append((name, sorted(entries)))
            # Yield so a slow filesystem cannot let this observation loop
            # starve the serialized installer threads indefinitely.
            time.sleep(0.0005)

    installers = [threading.Thread(target=_installer) for _ in range(4)]
    reader = threading.Thread(target=_reader)
    reader.start()
    for t in installers:
        t.start()
    for t in installers:
        t.join()
    stop.set()
    reader.join()

    assert not errors, f"install raised under concurrency: {errors}"
    assert observed_partial == [], f"reader saw half-published skills: {observed_partial}"
    for name in ("a", "b", "c"):
        assert (target / name / "SKILL.md").exists()
        assert (target / name / "body.md").exists()
