"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.streaming``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy.streaming is a deprecated re-export; "
    "import from tokenpak.proxy.streaming instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.streaming import *  # noqa: F401,F403,E402

__all__ = ["Any", "Dict", "Iterator", "StreamHandler", "StreamUsage", "dataclass", "extract_sse_tokens", "iter_sse_events"]
