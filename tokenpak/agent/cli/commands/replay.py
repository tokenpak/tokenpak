"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.replay``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.replay is a deprecated re-export; "
    "import from tokenpak.cli.commands.replay instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.replay import *  # noqa: F401,F403,E402

__all__ = ["Path", "build_replay_parser", "cmd_replay_clear", "cmd_replay_list", "cmd_replay_run", "cmd_replay_show"]
