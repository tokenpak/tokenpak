"""Install-drift detector — catches the dist-info shadow class of bug.

On 2026-04-22 a stale ``tokenpak-1.1.0.dist-info`` in
``~/.local/lib/python3.12/site-packages`` shadowed the canonical
editable install and made Claude Code's hook fail with
``ImportError: cannot import name '__version__' from 'tokenpak'``. That
whole class of problem (multiple ``tokenpak/`` locations on sys.path,
bare repo root treated as a namespace package, stale finders) gets
caught here.

The detector is called from ``tokenpak doctor --claude-code`` and is
also safe to run as part of ``make check`` to fail CI if a duplicate
install sneaks in.

No side effects. Returns a :class:`DriftReport`; the caller decides
how to present findings and whether to fail loudly.
"""

from __future__ import annotations

import logging
import site
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class DriftReport:
    """Result of :func:`detect_install_drift`.

    ``locations`` is the complete list of ``tokenpak/`` directories
    (or ``tokenpak.egg-info``) that Python CAN reach. When more than
    one resolves on sys.path, that's the shadow-install class of bug.

    ``dist_infos`` is the list of ``tokenpak-*.dist-info`` directories
    visible to pip. Multiple dist-info entries from different versions
    usually means a stale editable install wasn't cleaned up.
    """

    locations: list[Path] = field(default_factory=list)
    dist_infos: list[Path] = field(default_factory=list)
    cwd_is_repo_root: bool = False
    has_shadow: bool = False
    messages: list[str] = field(default_factory=list)


def _candidate_paths() -> Iterable[Path]:
    """Directories Python searches when resolving ``tokenpak``."""
    seen: set[str] = set()
    for p in sys.path:
        if not p:
            # Empty entry = current working directory.
            cwd = Path.cwd()
            key = str(cwd.resolve())
            if key not in seen:
                seen.add(key)
                yield cwd
            continue
        key = str(Path(p).resolve())
        if key in seen:
            continue
        seen.add(key)
        yield Path(p)
    # site-packages dirs pip installs into.
    try:
        for sp in site.getsitepackages():
            key = str(Path(sp).resolve())
            if key not in seen:
                seen.add(key)
                yield Path(sp)
    except Exception:  # noqa: BLE001
        pass
    try:
        user_sp = site.getusersitepackages()
        key = str(Path(user_sp).resolve())
        if key not in seen:
            seen.add(key)
            yield Path(user_sp)
    except Exception:  # noqa: BLE001
        pass


def detect_install_drift() -> DriftReport:
    """Scan every sys.path entry for a ``tokenpak/`` or dist-info."""
    report = DriftReport()
    import os

    for base in _candidate_paths():
        if not base.is_dir():
            continue
        # Bare tokenpak/ dir.
        pkg_dir = base / "tokenpak"
        if pkg_dir.is_dir():
            init = pkg_dir / "__init__.py"
            if init.exists():
                report.locations.append(pkg_dir)
            else:
                # Namespace-contribution directory — the exact class of
                # bug that trips up `python -m tokenpak.…` when cwd
                # happens to be the repo root.
                report.locations.append(pkg_dir)
                report.messages.append(
                    f"namespace-package collision risk: {pkg_dir} has no __init__.py"
                )
        # dist-info entries.
        try:
            for entry in base.iterdir():
                name = entry.name.lower()
                if (
                    entry.is_dir()
                    and name.startswith("tokenpak-")
                    and name.endswith(".dist-info")
                ):
                    report.dist_infos.append(entry)
        except (OSError, PermissionError):
            continue

    # cwd is the repo root if it contains pyproject.toml + tokenpak/.
    cwd = Path.cwd()
    if (cwd / "pyproject.toml").exists() and (cwd / "tokenpak").is_dir():
        report.cwd_is_repo_root = True

    # Shadow detection: >1 real tokenpak locations is always trouble.
    real_locations = [p for p in report.locations if (p / "__init__.py").exists()]
    if len(real_locations) > 1:
        report.has_shadow = True
        report.messages.append(
            f"{len(real_locations)} tokenpak/ locations reachable via sys.path "
            f"— pick one + remove the rest: "
            + ", ".join(str(p) for p in real_locations)
        )
    if len(report.dist_infos) > 1:
        # Multiple dist-info entries aren't always shadow (wheel + editable
        # finder is valid), but if versions differ it almost certainly is.
        versions = {p.name.split("-")[1] for p in report.dist_infos if "-" in p.name}
        if len(versions) > 1:
            report.has_shadow = True
            report.messages.append(
                f"multiple tokenpak versions installed: {sorted(versions)} — "
                "uninstall the older ones"
            )

    # cwd=repo-root classification (the exact trap that hit Claude Code's hook).
    if report.cwd_is_repo_root and os.environ.get("CLAUDECODE") == "1":
        report.messages.append(
            "cwd is the tokenpak repo root and CLAUDECODE=1 — "
            "Claude Code hooks may resolve ``tokenpak`` to this namespace "
            "directory and shadow the editable install. Use "
            "`python -P -m ...` in hook commands to suppress cwd insertion."
        )

    return report


__all__ = ["DriftReport", "detect_install_drift"]
