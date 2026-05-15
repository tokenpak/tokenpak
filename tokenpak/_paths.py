# SPDX-License-Identifier: Apache-2.0
"""Canonical on-disk path resolver for TokenPak (Std 33).

Single source of truth for where TokenPak stores user state, system
state, and Pro daemon coordination files. New code MUST route through
this module rather than building ``Path.home() / ".tokenpak"`` ad hoc.

Resolution order (Std 33 §2):
    1. ``TOKENPAK_HOME`` env var (operator override, e.g. for sandboxes)
    2. ``~/.tpk/`` (canonical default — Glossary 08 §TPK)
    3. ``~/.tokenpak/`` (legacy fallback, only when ``~/.tpk/`` is absent
       AND the legacy directory exists — preserves zero-touch upgrade)

Layout (Std 33 §3):
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
        companion/              companion subsystem state
        pro/                    Pro daemon coordination (sock-info, state)

The resolver is deliberately read-only — it does not create directories.
Callsites that need a directory must call ``ensure_home()`` (creates
``<home>/`` with mode 0700) or build their own ensure-step.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

CANONICAL_DIRNAME = ".tpk"
LEGACY_DIRNAME = ".tokenpak"
ENV_VAR = "TOKENPAK_HOME"


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
    """
    return home().joinpath(*parts)


def is_legacy_active() -> bool:
    """True when the *resolved* home is the legacy directory.

    Used by doctor/setup to surface a "you're on legacy paths — run
    ``tokenpak config migrate`` to move to ``~/.tpk/``" advisory.
    """
    return home() == legacy_home() and not has_canonical()


__all__ = [
    "CANONICAL_DIRNAME",
    "LEGACY_DIRNAME",
    "ENV_VAR",
    "home",
    "legacy_home",
    "canonical_home",
    "has_legacy",
    "has_canonical",
    "needs_migration",
    "ensure_home",
    "under",
    "is_legacy_active",
]
