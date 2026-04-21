"""Deprecated re-export shim (TokenPak agent/cli consolidation 2026-04-20).

Canonical home: ``tokenpak.cli.commands.vault``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands.vault is a deprecated re-export; "
    "import from tokenpak.cli.commands.vault instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands.vault import *  # noqa: F401,F403,E402

__all__ = ["run", "vault_cmd", "vault_reindex", "vault_status"]
