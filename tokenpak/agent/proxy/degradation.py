"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.degradation``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy.degradation is a deprecated re-export; "
    "import from tokenpak.proxy.degradation instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.degradation import *  # noqa: F401,F403,E402

__all__ = ["Any", "DegradationEvent", "DegradationEventType", "DegradationTracker", "Dict", "List", "dataclass", "datetime", "deque", "get_degradation_tracker", "timezone"]
