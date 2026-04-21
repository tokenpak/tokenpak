"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.optimize``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.optimize is a deprecated re-export; "
    "import from tokenpak.cli.commands.optimize instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.optimize import *  # noqa: F401,F403,E402

__all__ = ["Any", "COMPRESSION_MODES", "Dict", "List", "MODEL_ALTERNATIVES", "MODEL_COSTS", "Optional", "PROXY_BASE", "Path", "SEP", "Tuple", "date", "optimize_cmd", "run_optimize"]
