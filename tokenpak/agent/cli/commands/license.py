"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.license``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.license is a deprecated re-export; "
    "import from tokenpak.cli.commands.license instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.license import *  # noqa: F401,F403,E402

__all__ = ["activate_cmd", "deactivate_cmd", "license_cmd", "plan_cmd", "run"]
