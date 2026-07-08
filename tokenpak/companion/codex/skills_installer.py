# SPDX-License-Identifier: Apache-2.0
"""Install TokenPak skills into the Codex skills directory.

Skills are copied from the bundled ``skills/`` directory to the user
skill-discovery path Codex actually scans: ``$HOME/.agents/skills``
(spec: https://developers.openai.com/docs/guides/tools-skills). The set
of skills is discovered at runtime by globbing for ``SKILL.md`` — no
hardcoded enumeration (see ``feedback_always_dynamic.md``).  Uninstall
sweeps both the canonical path AND the pre-L3 legacy ``~/.codex/skills``
location so users upgrading from earlier installs don't leave orphans.

Install is hardened against concurrent launcher starts (two ``tokenpak
codex`` invocations racing on the same target directory): the operation
is serialized with a TokenPak-owned interprocess lock, each skill is
staged in full inside a unique temp sibling and then published with fast
renames — so a reader (Codex scanning the directory) never sees a
half-copied skill and only ever sees an installed skill absent for the
span of a single rename.  Stale stage/backup leftovers from a crashed
prior install are swept defensively without failing a normal launch.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
import threading
from collections.abc import Iterator
from pathlib import Path

_BUNDLED_SKILLS = Path(__file__).parent / "skills"
# Canonical user-scope path per Codex skill-discovery spec.
_DEFAULT_TARGET = Path.home() / ".agents" / "skills"
# Pre-L3 install path. Kept for defensive uninstall + doctor orphan reporting.
_LEGACY_TARGET = Path.home() / ".codex" / "skills"

# Transient dirs created during an atomic publish. Hidden + prefixed so
# they are never mistaken for a skill (which must be a directory holding
# ``SKILL.md``) and are cheap to identify and sweep on the next install.
_STAGE_PREFIX = ".tokenpak-stage-"
_BACKUP_PREFIX = ".tokenpak-backup-"
# Suffix for the sentinel whose whole-file advisory lock serializes
# concurrent installs. The sentinel lives in the target's PARENT (named
# ``.<target>.tokenpak-install.lock``) so the skills directory Codex
# scans stays free of TokenPak bookkeeping and an uninstall leaves it
# genuinely empty.
_LOCK_SUFFIX = ".tokenpak-install.lock"
_THREAD_LOCK = threading.RLock()


def bundled_skill_names() -> list[str]:
    """Return the names of all skills shipped with this package.

    A directory under :data:`_BUNDLED_SKILLS` counts as a skill only if
    it contains a ``SKILL.md`` file.
    """
    if not _BUNDLED_SKILLS.exists():
        return []
    return sorted(
        p.name
        for p in _BUNDLED_SKILLS.iterdir()
        if p.is_dir() and (p / "SKILL.md").exists()
    )


@contextlib.contextmanager
def _install_lock(target: Path) -> Iterator[None]:
    """Serialize concurrent :func:`install_skills` calls across processes.

    Takes an advisory whole-file lock on a sentinel under ``target`` so
    two launcher starts publishing into the same directory cannot
    interleave their rename swaps and clobber each other.  The lock is
    released when the descriptor closes — including on process death — so
    a crashed installer never wedges the next launch.  On platforms
    without ``fcntl``/``msvcrt`` the guard degrades to a no-op: a single
    installer is still correct; only the cross-process race protection is
    lost, which is acceptable for a best-effort provisioning step.
    """
    lock_path = target.parent / f".{target.name}{_LOCK_SUFFIX}"
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    release = None
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)

            def release() -> None:  # noqa: E306 - local release closure
                fcntl.flock(fd, fcntl.LOCK_UN)
        except ImportError:
            try:
                import msvcrt

                msvcrt.locking(fd, msvcrt.LK_LOCK, 1)

                def release() -> None:  # noqa: E306 - local release closure
                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            except (ImportError, OSError):
                release = None
        yield
    finally:
        if release is not None:
            with contextlib.suppress(Exception):
                release()
        with contextlib.suppress(OSError):
            os.close(fd)


def _sweep_stale_temp(target: Path) -> None:
    """Remove leftover stage/backup dirs from a crashed prior install.

    Called while holding the install lock, so any such directory is
    guaranteed stale (no live install is using it).  Best-effort: an
    entry that cannot be removed is skipped rather than aborting a normal
    launch over a harmless leftover.
    """
    for root in (target, target.parent):
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith(_STAGE_PREFIX) or entry.name.startswith(_BACKUP_PREFIX):
                with contextlib.suppress(OSError):
                    if entry.is_dir():
                        shutil.rmtree(entry)
                    else:
                        entry.unlink()


def _publish_skill(src: Path, dst: Path, target: Path) -> Path:
    """Stage ``src`` in a temp sibling and swap it into ``dst`` atomically.

    The caller holds the install lock.  ``src`` is copied in full into a
    uniquely-named staging directory before it is ever visible at
    ``dst`` (so a reader never observes a half-written skill); the staged
    copy is then renamed into place.  Any existing ``dst`` is moved aside
    first, so the only window in which ``dst`` is absent is between two
    rename syscalls.  On a failed swap the prior ``dst`` is restored so a
    launch never strands the user with a missing skill.
    """
    stage = Path(tempfile.mkdtemp(prefix=f"{_STAGE_PREFIX}{dst.name}-", dir=target.parent))
    backup = target.parent / f"{_BACKUP_PREFIX}{dst.name}-{os.getpid()}"
    try:
        # Full copy into the staged sibling while it is invisible as dst.
        shutil.copytree(src, stage, dirs_exist_ok=True)
        moved_aside = False
        if dst.exists():
            with contextlib.suppress(OSError):
                if backup.exists():
                    shutil.rmtree(backup)
            os.replace(dst, backup)
            moved_aside = True
        try:
            os.replace(stage, dst)
        except OSError:
            # Swap failed — restore the prior skill if we moved it aside.
            if moved_aside:
                with contextlib.suppress(OSError):
                    os.replace(backup, dst)
            raise
        if moved_aside:
            with contextlib.suppress(OSError):
                shutil.rmtree(backup)
    finally:
        # Clean the stage dir if it was not consumed by the swap.
        with contextlib.suppress(OSError):
            if stage.exists():
                shutil.rmtree(stage)
    return dst


def install_skills(target_dir: Path | None = None) -> list[Path]:
    """Copy bundled skills to the Codex skills directory, atomically.

    Existing tokenpak skills are replaced so updates propagate; other
    skills in the target directory are untouched.  The publish is safe
    under concurrent launcher starts: the whole operation is serialized
    with an interprocess lock, and each skill is fully staged in a temp
    sibling then swapped into place — so a reader never observes a
    half-copied or long-missing skill.
    """
    target = target_dir or _DEFAULT_TARGET
    target.mkdir(parents=True, exist_ok=True)

    installed: list[Path] = []
    with _THREAD_LOCK:
        with _install_lock(target):
            _sweep_stale_temp(target)
            for name in bundled_skill_names():
                src = _BUNDLED_SKILLS / name
                dst = target / name
                installed.append(_publish_skill(src, dst, target))
    return installed


def list_installed_skills(target_dir: Path | None = None) -> list[str]:
    """Return the bundled skills currently present in the target dir."""
    target = target_dir or _DEFAULT_TARGET
    return [name for name in bundled_skill_names() if (target / name).exists()]


def uninstall_skills(target_dir: Path | None = None) -> list[str]:
    """Remove every bundled tokenpak skill from the target dir(s).

    When ``target_dir`` is omitted, sweeps both the canonical
    ``~/.agents/skills`` path AND the pre-L3 legacy ``~/.codex/skills``
    location so users migrating off the old path are cleaned up in one
    pass.  Returns the names that were actually removed (deduped, in
    bundled order).
    """
    if target_dir is not None:
        targets = [target_dir]
    else:
        targets = [_DEFAULT_TARGET, _LEGACY_TARGET]

    removed: list[str] = []
    for name in bundled_skill_names():
        was_removed = False
        for target in targets:
            dst = target / name
            if dst.exists():
                shutil.rmtree(dst)
                was_removed = True
        if was_removed:
            removed.append(name)
    return removed


def _orphaned_legacy_skills() -> list[str]:
    """Return bundled-skill names still installed at the pre-L3 legacy path.

    Doctor surfaces these as a WARN so users can clean them up
    explicitly; we do not auto-migrate (a user may have customized a
    skill in place, and a silent overwrite would clobber the edit).

    Kept underscore-private: this is an internal doctor/uninstall helper,
    not part of the released public API surface recorded in
    ``_snapshots/public-api.json``.
    """
    return [name for name in bundled_skill_names() if (_LEGACY_TARGET / name).exists()]
