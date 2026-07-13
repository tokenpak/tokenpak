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
span of a single rename.

The prior copy of a replaced skill is retained as a timestamped generation
rather than deleted the instant it is superseded: ``os.replace`` only
rebinds a name, so a reader that opened the old directory before the swap
still holds that inode, and deleting it immediately would empty the inode
mid-``readdir``.  Retired generations and stale stage/backup leftovers from
a crashed prior install are reclaimed on a later launch once older than
:data:`_RECLAIM_MIN_AGE_S` — past any live enumeration — so cleanup never
races a reader and old generations are never retained indefinitely.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import tempfile
import threading
import time
from collections.abc import Iterator
from pathlib import Path

_BUNDLED_SKILLS = Path(__file__).parent / "skills"

# Tests may override these, but normal defaults are resolved at call time so a
# changed HOME cannot leak an import-time path into another launcher session.
_DEFAULT_TARGET: Path | None = None
_LEGACY_TARGET: Path | None = None

# The skill payloads live at the documented user discovery root, while every
# selected CODEX_HOME gets explicit references in its own config.toml.
_SKILLS_CONFIG_BEGIN = "# >>> tokenpak managed skills >>>"
_SKILLS_CONFIG_END = "# <<< tokenpak managed skills <<<"

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

# Minimum age (seconds) before a superseded skill generation or a leftover
# stage/backup dir is reclaimed. A directory enumeration (``opendir`` +
# ``readdir``) completes well within this window, so anything older than it
# cannot still be held by a live reader — reclaiming it can never empty a
# directory out from under an in-flight ``os.listdir``. Kept as a module
# attribute so tests can force immediate reclamation (set to 0).
_RECLAIM_MIN_AGE_S = 5.0


def _default_skills_root() -> Path:
    """Return Codex's canonical user-scope skills root."""
    return _DEFAULT_TARGET or (Path.home() / ".agents" / "skills")


def _legacy_skills_root() -> Path:
    """Return the pre-L3 path used only for cleanup and diagnostics."""
    return _LEGACY_TARGET or (Path.home() / ".codex" / "skills")


def bundled_skill_names() -> list[str]:
    """Return the names of all skills shipped with this package.

    A directory under :data:`_BUNDLED_SKILLS` counts as a skill only if
    it contains a ``SKILL.md`` file.
    """
    if not _BUNDLED_SKILLS.exists():
        return []
    return sorted(
        p.name for p in _BUNDLED_SKILLS.iterdir() if p.is_dir() and (p / "SKILL.md").exists()
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
    """Reclaim aged retired generations and crash-leftover stage/backup dirs.

    Sweeps both ``target`` and its parent (where publish stages and retires)
    for ``_STAGE_PREFIX`` / ``_BACKUP_PREFIX`` entries, removing only those
    older than :data:`_RECLAIM_MIN_AGE_S`.  Called while holding the install
    lock.

    The age gate is what makes reclamation safe under concurrent readers: a
    retired skill generation (a directory a reader may have opened just
    before it was superseded) is kept until no reader could still be
    mid-``readdir`` of it, then removed — so cleanup never empties an inode
    out from under an in-flight ``os.listdir``, and generations are not
    retained forever.  A too-young entry is left for a later launch.
    Best-effort: an entry that cannot be removed (or stat'd) is skipped
    rather than aborting a normal launch over a harmless leftover.
    """
    now = time.time()
    for root in (target, target.parent):
        try:
            entries = list(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            if not (entry.name.startswith(_STAGE_PREFIX) or entry.name.startswith(_BACKUP_PREFIX)):
                continue
            try:
                age = now - entry.stat().st_mtime
            except OSError:
                continue
            if age < _RECLAIM_MIN_AGE_S:
                continue  # possibly still observable by a live reader — keep it
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

    A superseded ``dst`` is retired to a uniquely-named backup and left in
    place — NOT deleted here.  ``os.replace`` only rebinds the name, so a
    reader that opened the old ``dst`` before the swap still holds that
    inode; deleting it now would empty it mid-``readdir``.  The retired
    generation is reclaimed by a later launch's :func:`_sweep_stale_temp`
    once it is older than :data:`_RECLAIM_MIN_AGE_S`.
    """
    stage = Path(tempfile.mkdtemp(prefix=f"{_STAGE_PREFIX}{dst.name}-", dir=target.parent))
    # Retirement target for a superseded generation. Unique per publish
    # (reuse the stage's random suffix) so concurrent/repeated publishes
    # never collide on one backup path, and each ages out on its own.
    unique = stage.name.rsplit("-", 1)[-1]
    backup = target.parent / f"{_BACKUP_PREFIX}{dst.name}-{unique}"
    try:
        # Full copy into the staged sibling while it is invisible as dst.
        shutil.copytree(src, stage, dirs_exist_ok=True)
        moved_aside = False
        if dst.exists():
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
        # The retired generation (``backup``) is deliberately left for a
        # later _sweep_stale_temp — see the docstring (reader-vs-delete race).
    finally:
        # Clean the stage dir if it was not consumed by the swap. The stage
        # is never observable under a skill name, so removing it now is safe.
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
    target = target_dir or _default_skills_root()
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
    target = target_dir or _default_skills_root()
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
        targets = [_default_skills_root(), _legacy_skills_root()]

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
    legacy = _legacy_skills_root()
    return [name for name in bundled_skill_names() if (legacy / name).exists()]


def _split_managed_skill_config(content: str) -> tuple[str, str | None]:
    """Return config without our managed block and the prior block, if any.

    A lone marker fails closed instead of risking damage to a user-owned TOML
    file whose ownership boundary cannot be determined safely.
    """
    starts = content.count(_SKILLS_CONFIG_BEGIN)
    ends = content.count(_SKILLS_CONFIG_END)
    if starts != ends or starts > 1:
        raise ValueError("malformed TokenPak skills config markers")
    if starts == 0:
        return content, None

    start = content.index(_SKILLS_CONFIG_BEGIN)
    end = content.index(_SKILLS_CONFIG_END, start) + len(_SKILLS_CONFIG_END)
    # The installer owns exactly one separator newline before the marker and
    # one terminator newline after it.  Keeping those bytes inside the managed
    # region makes install -> uninstall restore user config byte-for-byte.
    managed_start = start - 1 if start > 0 and content[start - 1] == "\n" else start
    managed_end = end + 1 if content[end : end + 1] == "\n" else end
    return content[:managed_start] + content[managed_end:], content[managed_start:managed_end]


def _render_skill_config(skills_root: Path) -> str:
    lines = [_SKILLS_CONFIG_BEGIN]
    for name in bundled_skill_names():
        skill_dir = skills_root / name
        if not (skill_dir / "SKILL.md").is_file():
            continue
        lines.extend(
            (
                "[[skills.config]]",
                f"path = {json.dumps(str(skill_dir))}",
                "enabled = true",
                "",
            )
        )
    while lines[-1] == "":
        lines.pop()
    lines.append(_SKILLS_CONFIG_END)
    return "\n".join(lines)


def _write_private(path: Path, content: str) -> None:
    """Replace one selected-home config atomically with private permissions."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()


def _configure_skills(
    config_path: Path,
    *,
    skills_root: Path | None = None,
) -> list[Path]:
    """Reference installed TokenPak skills from one selected CODEX_HOME.

    Only an explicitly delimited ``[[skills.config]]`` block is owned. User
    configuration outside that block is preserved byte-for-byte except for a
    separating newline. The referenced paths are skill directories containing
    ``SKILL.md``, as required by Codex's config schema.
    """
    root = skills_root or _default_skills_root()
    existing = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
    _, prior = _split_managed_skill_config(existing)
    block = _render_skill_config(root)
    if prior is not None:
        managed = (
            ("\n" if prior.startswith("\n") else "")
            + block
            + ("\n" if prior.endswith("\n") else "")
        )
        content = existing.replace(prior, managed, 1)
    else:
        content = existing + ("\n" if existing else "") + block + "\n"
    _write_private(config_path, content)
    return [root / name for name in bundled_skill_names() if (root / name / "SKILL.md").is_file()]


def _configured_skill_paths(config_path: Path) -> list[Path]:
    """Return directory paths from TokenPak's managed config block."""
    if not config_path.exists():
        return []
    _, block = _split_managed_skill_config(config_path.read_text(encoding="utf-8"))
    if block is None:
        return []
    paths: list[Path] = []
    for line in block.splitlines():
        key, separator, value = line.partition("=")
        if not separator or key.strip() != "path":
            continue
        try:
            parsed = json.loads(value.strip())
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, str):
            paths.append(Path(parsed))
    return paths


def _clean_skills_config(config_path: Path) -> bool:
    """Remove only TokenPak's managed skill references from one config."""
    if not config_path.exists():
        return False
    existing = config_path.read_text(encoding="utf-8")
    base, block = _split_managed_skill_config(existing)
    if block is None:
        return False
    _write_private(config_path, base)
    return True
