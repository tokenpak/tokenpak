# SPDX-License-Identifier: Apache-2.0
"""Install TokenPak skills into the Codex skills directory.

Skills are copied from the bundled ``skills/`` directory to
``~/.codex/skills/``.  The set of skills is discovered at runtime by
globbing for ``SKILL.md`` — no hardcoded enumeration (see
``feedback_always_dynamic.md``).  Uninstall uses the same glob so the
two halves can never drift.
"""

from __future__ import annotations

import shutil
from pathlib import Path

_BUNDLED_SKILLS = Path(__file__).parent / "skills"
_DEFAULT_TARGET = Path.home() / ".codex" / "skills"


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


def install_skills(target_dir: Path | None = None) -> list[Path]:
    """Copy bundled skills to the Codex skills directory.

    Existing tokenpak skills are replaced so updates propagate; other
    skills in the target directory are untouched.
    """
    target = target_dir or _DEFAULT_TARGET
    target.mkdir(parents=True, exist_ok=True)

    installed: list[Path] = []
    for name in bundled_skill_names():
        src = _BUNDLED_SKILLS / name
        dst = target / name
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)
        installed.append(dst)

    return installed


def list_installed_skills(target_dir: Path | None = None) -> list[str]:
    """Return the bundled skills currently present in the target dir."""
    target = target_dir or _DEFAULT_TARGET
    return [name for name in bundled_skill_names() if (target / name).exists()]


def uninstall_skills(target_dir: Path | None = None) -> list[str]:
    """Remove every bundled tokenpak skill from the target dir.

    Returns the names that were actually removed.
    """
    target = target_dir or _DEFAULT_TARGET
    removed: list[str] = []
    for name in bundled_skill_names():
        dst = target / name
        if dst.exists():
            shutil.rmtree(dst)
            removed.append(name)
    return removed
