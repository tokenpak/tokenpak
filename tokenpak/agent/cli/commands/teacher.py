"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.teacher``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.teacher is a deprecated re-export; "
    "import from tokenpak.cli.commands.teacher instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.teacher import *  # noqa: F401,F403,E402

__all__ = ["DEFAULT_COMMAND_ROOTS", "DEFAULT_OUTPUT_ROOT", "DEFAULT_SOURCE_ROOTS", "Path", "build_teacher_pack", "run_teacher_cmd"]
