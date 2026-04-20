"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.stats``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy.stats is a deprecated re-export; "
    "import from tokenpak.proxy.stats instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.stats import *  # noqa: F401,F403,E402

__all__ = ["Any", "CompressionStats", "DEFAULT_LOG_DIR", "DEFAULT_LOG_FILENAME", "DEFAULT_LOG_PATH", "Dict", "List", "MAX_LOG_BYTES", "Optional", "Path", "ROLLING_WINDOW", "datetime", "deque", "get_compression_stats", "reset_singleton", "timezone"]
