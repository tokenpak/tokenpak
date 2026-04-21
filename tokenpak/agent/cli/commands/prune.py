"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.prune``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.prune is a deprecated re-export; "
    "import from tokenpak.cli.commands.prune instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.prune import *  # noqa: F401,F403,E402

__all__ = ["DEFAULT_THRESHOLD", "List", "Path", "SEP", "Tuple", "prune_cmd", "run_prune"]
