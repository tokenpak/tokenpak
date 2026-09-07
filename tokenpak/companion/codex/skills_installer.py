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
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Callable, cast

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

# Exact SKILL.md payloads shipped immediately before ownership-aware upgrades.
# A pre-record install had no manifest, so content identity is the only safe
# evidence that a same-name directory is an unmodified TokenPak copy. Append a
# digest when a shipped body changes; never infer ownership from the name/path.
_KNOWN_SHIPPED_SKILL_MD_SHA256: dict[str, frozenset[str]] = {
    "tokenpak-budget-aware-implementation": frozenset(
        {"f7c0eb1e1341cd60f99035f08ded8d3323e2a8bd8687e025b6dfd864b63bae5a"}
    ),
    "tokenpak-large-refactor-mode": frozenset(
        {
            "1be837f78a802c5185efd3f68804904836a34a39e00a8356e78cf0a5b2e0bf4f",
            "8bed320ff9c21c2aaaeadf4b87eaefd83cb5d19d502aba91277cbe72b356a4b8",
        }
    ),
    "tokenpak-load-memory": frozenset(
        {"a2b97424bc25ffd77551685db2d1cee5afda40df252725db405ac5eb764d9f75"}
    ),
    "tokenpak-retrospective": frozenset(
        {"3d49bb508e3a74bd2937cc9a1540a18dfb9859819d7fe07dd3ff055f26446507"}
    ),
    "tokenpak-start-session": frozenset(
        {"1d2671a22fd111c296b092fb97779ed9e2ffb6bebabbd18d810d18fcdc8c844a"}
    ),
}


def _tree_digest(path: Path) -> str | None:
    """Return a deterministic digest for a regular-file tree.

    Symlinks and non-file entries fail closed because following them could make
    a user-owned target look like package-owned content.
    """
    if not path.is_dir() or path.is_symlink():
        return None
    digest = hashlib.sha256()
    try:
        entries = sorted(path.rglob("*"), key=lambda item: item.relative_to(path).as_posix())
        if any(
            entry.is_symlink() or (not entry.is_dir() and not entry.is_file()) for entry in entries
        ):
            return None
        for entry in entries:
            relative = entry.relative_to(path).as_posix().encode("utf-8")
            digest.update(b"d" if entry.is_dir() else b"f")
            digest.update(relative)
            digest.update(b"\0")
            if entry.is_file():
                digest.update(entry.read_bytes())
                digest.update(b"\0")
    except OSError:
        return None
    return digest.hexdigest()


def _is_known_shipped_copy(path: Path, name: str) -> bool:
    """True only for an exact current or recorded TokenPak skill tree."""
    if path.is_symlink():
        return False
    source = _BUNDLED_SKILLS / name
    installed_digest = _tree_digest(path)
    source_digest = _tree_digest(source)
    if installed_digest is not None and installed_digest == source_digest:
        return True

    # The recorded pre-upgrade bodies contained exactly one regular file.
    try:
        entries = list(path.iterdir())
        skill_file = path / "SKILL.md"
        if (
            len(entries) == 1
            and entries[0] == skill_file
            and skill_file.is_file()
            and not skill_file.is_symlink()
        ):
            body_digest = hashlib.sha256(skill_file.read_bytes()).hexdigest()
            return body_digest in _KNOWN_SHIPPED_SKILL_MD_SHA256.get(name, ())
    except OSError:
        return False
    return False


def _report_conflict(action: str, path: Path) -> None:
    warnings.warn(
        f"TokenPak {action} skipped customized or unknown skill copy: {path}",
        RuntimeWarning,
        stacklevel=3,
    )


def _same_location(left: Path, right: Path) -> bool:
    """Return whether two roots resolve to the same filesystem location."""
    try:
        return left.samefile(right)
    except OSError:
        return left.resolve(strict=False) == right.resolve(strict=False)


def _report_reconciliation_blocked(legacy: Path, canonical: Path) -> None:
    warnings.warn(
        "TokenPak legacy reconciliation preserved managed copy "
        f"{legacy}: canonical destination is customized or unknown: {canonical}",
        RuntimeWarning,
        stacklevel=3,
    )


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
    release: Callable[[], None] | None = None
    try:
        try:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX)

            def release() -> None:  # noqa: E306 - local release closure
                fcntl.flock(fd, fcntl.LOCK_UN)
        except ImportError:
            try:
                import msvcrt

                locking = cast(Callable[[int, int, int], None], getattr(msvcrt, "locking"))
                lock_blocking = cast(int, getattr(msvcrt, "LK_LOCK"))
                unlock = cast(int, getattr(msvcrt, "LK_UNLCK"))
                locking(fd, lock_blocking, 1)

                def release() -> None:  # noqa: E306 - local release closure
                    os.lseek(fd, 0, os.SEEK_SET)
                    locking(fd, unlock, 1)
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

    Existing skills are replaced only when their content exactly matches the
    current bundle or a recorded shipped generation. Same-name customized or
    unknown directories are preserved and reported. The publish is safe
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
                if dst.exists() or dst.is_symlink():
                    if not _is_known_shipped_copy(dst, name):
                        _report_conflict("upgrade", dst)
                        continue
                    if _tree_digest(dst) == _tree_digest(src):
                        installed.append(dst)
                        continue
                installed.append(_publish_skill(src, dst, target))

            # Supported upgrades move demonstrably managed copies out of the
            # pre-discovery path after the canonical copies are ready. A
            # customized legacy copy remains in place with a visible conflict.
            if target_dir is None:
                legacy = _legacy_skills_root()
                if not _same_location(legacy, target):
                    for name in bundled_skill_names():
                        old = legacy / name
                        if not (old.exists() or old.is_symlink()):
                            continue
                        if not _is_known_shipped_copy(old, name):
                            _report_conflict("legacy reconciliation", old)
                            continue
                        canonical = target / name
                        if not _is_known_shipped_copy(canonical, name):
                            _report_reconciliation_blocked(old, canonical)
                            continue
                        shutil.rmtree(old)
    return installed


def list_installed_skills(target_dir: Path | None = None) -> list[str]:
    """Return demonstrably managed bundled skills in the target dir."""
    target = target_dir or _default_skills_root()
    return [name for name in bundled_skill_names() if _is_known_shipped_copy(target / name, name)]


def uninstall_skills(target_dir: Path | None = None) -> list[str]:
    """Remove demonstrably managed TokenPak skills from the target dir(s).

    When ``target_dir`` is omitted, sweeps both the canonical
    ``~/.agents/skills`` path AND the pre-L3 legacy ``~/.codex/skills``
    location so users migrating off the old path are cleaned up in one pass.
    Same-name customized or unknown copies are preserved and reported. Returns
    the names that were actually removed (deduped, in bundled order).
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
            if dst.exists() or dst.is_symlink():
                if not _is_known_shipped_copy(dst, name):
                    _report_conflict("uninstall", dst)
                    continue
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
        if not _is_known_shipped_copy(skill_dir, name):
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
    return [root / name for name in list_installed_skills(root)]


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
