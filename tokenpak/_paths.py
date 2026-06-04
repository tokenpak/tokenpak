# SPDX-License-Identifier: Apache-2.0
"""Canonical on-disk path resolver for TokenPak.

Single source of truth for where TokenPak stores user state, system
state, and Pro daemon coordination files. New code MUST route through
this module rather than building ``Path.home() / ".tokenpak"`` ad hoc.

Resolution order:
    1. ``TOKENPAK_HOME`` env var (operator override, e.g. for sandboxes)
    2. ``~/.tpk/`` (canonical default — Glossary 08 §TPK)
    3. ``~/.tokenpak/`` (legacy fallback, only when ``~/.tpk/`` is absent
       AND the legacy directory exists — preserves zero-touch upgrade)

Layout:
    <home>/
        config.{json,yaml}      user config (config commands)
        license.json            license store (licensing module)
        debug.log               doctor/diagnostics log
        index.json              vault index
        templates/              user templates
        fleet.yaml              fleet manifest
        pinned_blocks.json      retain pins
        requests.jsonl          request log
        telemetry.db            telemetry store
        monitor.db              request ledger
        companion/              companion subsystem state
        pro/                    Pro daemon coordination (sock-info, state)

The resolver is deliberately read-only — it does not create directories.
Callsites that need a directory must call ``ensure_home()`` (creates
``<home>/`` with mode 0700) or build their own ensure-step.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

CANONICAL_DIRNAME = ".tpk"
LEGACY_DIRNAME = ".tokenpak"
ENV_VAR = "TOKENPAK_HOME"

# Canonical TokenPak home layout subdirectories. ``under()`` fail-loud rejects
# any extension-less first segment that is not in this set (or _ADOPTED_SUBDIRS),
# per the always-dynamic fail-loud principle: do not silently accept unknown
# subdirs (a typo'd subdir would otherwise create junk state). New subdirs are
# added here when the canonical layout is amended.
_STD_33_SUBDIRS: frozenset[str] = frozenset(
    {
        "templates",
        "companion",
        "pro",
        # dispatch/ — TokenPak Dispatch runtime state (runs.db, artifacts/,
        # tmp/, overlays/). Added per the canonical layout amendment of
        # 2026-05-20.
        "dispatch",
    }
)

# Subdirs in active use that are not (yet) enumerated in the canonical layout.
# Tracked separately so the drift is visible and can be reconciled into the
# canonical layout rather than silently blessed. ``paks/`` is written by
# tokenpak/cli/commands/pak.py and predates the resolver's fail-loud contract.
_ADOPTED_SUBDIRS: frozenset[str] = frozenset(
    {
        "paks",
    }
)


def _known_subdirs() -> frozenset[str]:
    """All subdir names ``under()`` will accept (canonical layout + adopted)."""
    return _STD_33_SUBDIRS | _ADOPTED_SUBDIRS


def _is_top_level_file(name: str) -> bool:
    """True when the first segment names a top-level file (e.g. ``config.json``).

    The resolver contract explicitly sanctions ``under("file")`` for top-level
    files ("always ... call ``_paths.under(\"file\")``"). Every top-level file
    carries an extension (``config.json``, ``license.json``, ``telemetry.db``
    ...), so the presence of a ``.`` distinguishes a file target from a
    (typo'd) subdir target.
    """
    return "." in name


_MONITOR_DB_ENV = "TOKENPAK_DB"
_MONITOR_DB_ENV_COMPAT = "TOKENPAK_MONITOR_DB"
_MONITOR_TABLE = "requests"


def home() -> Path:
    """Return the resolved TokenPak home directory.

    See module docstring for resolution order. Always returns a Path
    object even when the directory does not exist.
    """
    override = os.environ.get(ENV_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    canonical = Path.home() / CANONICAL_DIRNAME
    if canonical.exists():
        return canonical
    legacy = Path.home() / LEGACY_DIRNAME
    if legacy.exists():
        return legacy
    return canonical


def legacy_home() -> Path:
    """Return the legacy ``~/.tokenpak/`` path (always — for migration probes)."""
    return Path.home() / LEGACY_DIRNAME


def canonical_home() -> Path:
    """Return the canonical ``~/.tpk/`` path (always — for migration targets)."""
    return Path.home() / CANONICAL_DIRNAME


def has_legacy() -> bool:
    """True if ``~/.tokenpak/`` exists on disk (migration trigger)."""
    return legacy_home().exists()


def has_canonical() -> bool:
    """True if ``~/.tpk/`` exists on disk."""
    return canonical_home().exists()


def needs_migration() -> bool:
    """True when the legacy directory exists and the canonical does not.

    This is the migration trigger condition. ``tokenpak config migrate``
    backs up the legacy tree, copies it to the canonical location, and
    leaves the legacy tree in place (rename-after-soak, not delete) so
    no user state is destroyed.
    """
    return has_legacy() and not has_canonical()


def ensure_home(*, mode: int = 0o700) -> Path:
    """Create the resolved home directory if absent. Returns the path.

    Mode 0700 is enforced because the directory contains license keys
    and Pro daemon coordination state. Existing directories are not
    re-chmoded (operator may have intentional permissions).
    """
    h = home()
    h.mkdir(mode=mode, parents=True, exist_ok=True)
    return h


def under(*parts: str) -> Path:
    """Build a path under the resolved home: ``under("companion", "journal.db")``.

    Pure-path helper — does not create parents. Equivalent to
    ``home().joinpath(*parts)`` but spelled to encourage callsites to
    say what they want at the import site, not assemble strings.

    Fail-loud per the resolver contract: the first segment must be either a
    known layout subdir (``_STD_33_SUBDIRS`` / ``_ADOPTED_SUBDIRS``) or a
    top-level file (a name containing an extension). An unknown
    extension-less first segment raises ``ValueError`` rather than
    silently resolving — this catches typo'd subdirs (``under("compaion")``)
    and not-yet-enumerated subdirs before they create junk state.
    """
    if not parts:
        raise ValueError("under() requires at least one path segment")
    first = parts[0]
    if first in _known_subdirs() or _is_top_level_file(first):
        return home().joinpath(*parts)
    raise ValueError(
        f"unknown TokenPak home subdir {first!r}: allowed subdirs are "
        f"{sorted(_known_subdirs())}, or a top-level file (name with an "
        f"extension, e.g. 'config.json'). Add new subdirs to "
        f"_STD_33_SUBDIRS per a canonical layout amendment."
    )


def is_legacy_active() -> bool:
    """True when the *resolved* home is the legacy directory.

    Used by doctor/setup to surface a "you're on legacy paths — run
    ``tokenpak config migrate`` to move to ``~/.tpk/``" advisory.
    """
    return home() == legacy_home() and not has_canonical()


# Resolver-contract API names: ``resolved_home`` / ``is_legacy`` are the names
# the public path API uses. They alias the module's existing ``home`` /
# ``is_legacy_active`` so both spellings resolve identically and no existing
# callsite breaks.
def resolved_home() -> Path:
    """Alias for :func:`home`."""
    return home()


def is_legacy() -> bool:
    """Alias for :func:`is_legacy_active`."""
    return is_legacy_active()


# ---------------------------------------------------------------------------
# Monitor DB resolver
# ---------------------------------------------------------------------------


def _is_valid_monitor_db(p: Path) -> bool:
    """Check whether *p* is a usable monitor DB (exists, SQLite, has schema)."""
    try:
        resolved = p.resolve() if p.is_symlink() else p
        if not resolved.is_file():
            return False
        if resolved.stat().st_size < 100:
            return False
        conn = sqlite3.connect(str(resolved), timeout=2)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_MONITOR_TABLE,),
        )
        found = cur.fetchone() is not None
        conn.close()
        return found
    except Exception:
        return False


def _monitor_db_candidates() -> list[Path]:
    """Ordered candidate paths for the monitor DB (read resolution order)."""
    candidates: list[Path] = []
    env_val = os.environ.get(_MONITOR_DB_ENV, "").strip()
    if env_val:
        candidates.append(Path(env_val).expanduser())
    else:
        env_compat = os.environ.get(_MONITOR_DB_ENV_COMPAT, "").strip()
        if env_compat:
            candidates.append(Path(env_compat).expanduser())
    candidates.append(Path.home() / CANONICAL_DIRNAME / "monitor.db")
    candidates.append(Path.home() / LEGACY_DIRNAME / "monitor.db")
    candidates.append(Path.home() / "tokenpak" / "monitor.db")
    return candidates


def monitor_db(mode: str = "read") -> Optional[Path]:
    """Resolve the monitor DB path.

    mode="read":  Return the first valid active DB, or None if no
                  valid DB exists. Does not create anything.
    mode="write": Return the existing active DB if found, otherwise
                  the canonical fresh-install path (~/.tpk/monitor.db).
                  Creates the parent directory if needed, but does NOT
                  create the DB file itself.
    """
    for candidate in _monitor_db_candidates():
        if _is_valid_monitor_db(candidate):
            return candidate
    if mode == "write":
        target = Path.home() / CANONICAL_DIRNAME / "monitor.db"
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        return target
    return None


def monitor_db_candidates() -> list[dict[str, Any]]:
    """Return diagnostic info for each candidate path (for doctor/split-brain).

    Each entry: {path, exists, valid, rows, selected}.
    """
    results: list[dict[str, Any]] = []
    selected_path = monitor_db(mode="read")
    for candidate in _monitor_db_candidates():
        entry: dict[str, Any] = {
            "path": str(candidate),
            "exists": candidate.exists(),
            "valid": False,
            "rows": 0,
            "selected": False,
        }
        if _is_valid_monitor_db(candidate):
            entry["valid"] = True
            entry["selected"] = (
                selected_path is not None
                and candidate.resolve() == selected_path.resolve()
            )
            try:
                conn = sqlite3.connect(str(candidate.resolve()), timeout=2)
                cur = conn.execute(f"SELECT COUNT(*) FROM {_MONITOR_TABLE}")
                entry["rows"] = cur.fetchone()[0]
                conn.close()
            except Exception:
                pass
        results.append(entry)
    return results


__all__ = [
    "CANONICAL_DIRNAME",
    "LEGACY_DIRNAME",
    "ENV_VAR",
    "home",
    "resolved_home",
    "is_legacy",
    "legacy_home",
    "canonical_home",
    "has_legacy",
    "has_canonical",
    "needs_migration",
    "ensure_home",
    "under",
    "is_legacy_active",
    "monitor_db",
    "monitor_db_candidates",
]
