"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.metrics``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.metrics is a deprecated re-export; "
    "import from tokenpak.cli.commands.metrics instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.metrics import *  # noqa: F401,F403,E402

__all__ = ["SEP", "cmd_history", "cmd_preview", "cmd_status", "cmd_sync", "metrics_cmd", "metrics_history_cmd", "metrics_preview_cmd", "metrics_status_cmd", "metrics_sync_cmd"]
