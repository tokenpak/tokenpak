"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.connection_pool``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy.connection_pool is a deprecated re-export; "
    "import from tokenpak.proxy.connection_pool instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.connection_pool import *  # noqa: F401,F403,E402

__all__ = ["ConnectionPool", "Dict", "Optional", "PoolConfig", "PoolMetrics", "dataclass", "get_global_pool", "reset_global_pool"]
