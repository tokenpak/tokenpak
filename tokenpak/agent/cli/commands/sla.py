"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.sla``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.sla is a deprecated re-export; "
    "import from tokenpak.cli.commands.sla instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.sla import *  # noqa: F401,F403,E402

__all__ = ["run"]
