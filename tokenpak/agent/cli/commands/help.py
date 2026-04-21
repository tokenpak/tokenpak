"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.help``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.help is a deprecated re-export; "
    "import from tokenpak.cli.commands.help instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.help import *  # noqa: F401,F403,E402

__all__ = ["Optional", "Path", "help_cmd", "print_command_help", "print_essential_help", "print_full_help", "print_intermediate_help", "print_minimal_help", "run"]
