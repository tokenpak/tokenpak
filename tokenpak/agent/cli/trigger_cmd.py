"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.trigger_cmd``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.trigger_cmd is a deprecated re-export; "
    "import from tokenpak.cli.trigger_cmd instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.trigger_cmd import *  # noqa: F401,F403,E402

__all__ = ["DEFAULT_CONFIG", "SEP", "TriggerStore", "add_cmd", "list_cmd", "log_cmd", "match_event", "remove_cmd", "test_cmd", "trigger_group"]
