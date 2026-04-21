"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.diff``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.diff is a deprecated re-export; "
    "import from tokenpak.cli.commands.diff instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.diff import *  # noqa: F401,F403,E402

__all__ = ["ContextDiff", "DiffBlock", "Optional", "SEP", "datetime", "print_diff", "print_diff_json", "run_diff_cmd"]
