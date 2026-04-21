"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.budget``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.budget is a deprecated re-export; "
    "import from tokenpak.cli.commands.budget instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.budget import *  # noqa: F401,F403,E402

__all__ = ["Optional", "Path", "SEP", "date", "print_budget_forecast", "print_budget_history", "print_budget_intelligence", "print_budget_status", "run_budget_cmd", "timedelta"]
