"""Deprecated re-export shim for ``tokenpak.agent.cli``.

Canonical home: ``tokenpak.cli``. Removal target: TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.cli is a deprecated re-export; "
    "import from tokenpak.cli instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.cli import *  # noqa: F401,F403,E402

__all__ = ["commands", "main", "proxy_client"]
