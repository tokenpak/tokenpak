"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.providers.stream_translator``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy.providers.stream_translator is a deprecated re-export; "
    "import from tokenpak.proxy.providers.stream_translator instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.providers.stream_translator import *  # noqa: F401,F403,E402

__all__ = ["Any", "Dict", "Iterator", "List", "Optional", "StreamingTranslator"]
