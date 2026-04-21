"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.route``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.route is a deprecated re-export; "
    "import from tokenpak.cli.commands.route instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.route import *  # noqa: F401,F403,E402

__all__ = ["route_cmd", "route_off", "route_on", "route_status", "run"]
