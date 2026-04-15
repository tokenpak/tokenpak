# SPDX-License-Identifier: Apache-2.0
"""Install TokenPak skills into the Codex skills directory.

Skills are copied from the bundled ``skills/`` directory to
``~/.codex/skills/``.  Existing TokenPak skills are overwritten
to ensure they stay up to date; non-TokenPak skills are untouched.
"""

from __future__ import annotations

import shutil
from pathlib import Path


_BUNDLED_SKILLS = Path(__file__).parent / "skills"

# All TokenPak skill folder names
_SKILL_NAMES = [
    "tokenpak-start-session",
    "tokenpak-load-memory",
    "tokenpak-budget-aware-implementation",
    "tokenpak-large-refactor-mode",
    "tokenpak-retrospective",
]


def install_skills(target_dir: Path | None = None) -> list[Path]:
    """Copy bundled skills to the Codex skills directory.

    Args:
        target_dir: Override for the skills directory.  Defaults to
                    ``~/.codex/skills/``.

    Returns:
        List of paths to installed skill directories.
    """
    if target_dir is None:
        target_dir = Path.home() / ".codex" / "skills"

    target_dir.mkdir(parents=True, exist_ok=True)

    installed: list[Path] = []
    for name in _SKILL_NAMES:
        src = _BUNDLED_SKILLS / name
        dst = target_dir / name

        if not src.exists():
            continue

        # Replace existing TokenPak skill (keeps it updated)
        if dst.exists():
            shutil.rmtree(dst)

        shutil.copytree(src, dst)
        installed.append(dst)

    return installed


def list_installed_skills(target_dir: Path | None = None) -> list[str]:
    """Return names of installed TokenPak skills."""
    if target_dir is None:
        target_dir = Path.home() / ".codex" / "skills"

    return [
        name for name in _SKILL_NAMES
        if (target_dir / name).exists()
    ]
