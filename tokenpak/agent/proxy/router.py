"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.router``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings

warnings.warn(
    "tokenpak.agent.proxy.router is a deprecated re-export; "
    "import from tokenpak.proxy.router instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.router import *  # noqa: F401,F403,E402
