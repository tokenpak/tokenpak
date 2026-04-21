"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.config``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.config is a deprecated re-export; "
    "import from tokenpak.cli.commands.config instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.config import *  # noqa: F401,F403,E402

__all__ = ["TOKENPAK_VARS", "config_cmd", "config_set_cmd", "config_show_cmd", "run", "run_set"]
