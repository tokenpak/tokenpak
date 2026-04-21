"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.retain``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.retain is a deprecated re-export; "
    "import from tokenpak.cli.commands.retain instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.retain import *  # noqa: F401,F403,E402

__all__ = ["Optional", "Path", "SEP", "load_pins", "pin_block", "retain_cmd", "run_retain", "run_retain_list", "run_retain_pin", "run_retain_remove", "unpin_block"]
