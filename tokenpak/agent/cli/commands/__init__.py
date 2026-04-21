"""Deprecated re-export shim for ``tokenpak.agent.cli.commands``.

Canonical home: ``tokenpak.cli.commands``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli.commands is a deprecated re-export; "
    "import from tokenpak.cli.commands instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli.commands import *  # noqa: F401,F403,E402

__all__ = ["compression", "config", "cost", "exec", "last", "license", "maintenance", "replay", "route", "savings", "status", "teacher", "vault", "workflow"]
