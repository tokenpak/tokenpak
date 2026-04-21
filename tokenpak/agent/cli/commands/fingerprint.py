"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.fingerprint``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.fingerprint is a deprecated re-export; "
    "import from tokenpak.cli.commands.fingerprint instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.fingerprint import *  # noqa: F401,F403,E402

__all__ = ["Optional", "Path", "fingerprint_cache", "fingerprint_clear_cache", "fingerprint_cmd", "fingerprint_sync"]
