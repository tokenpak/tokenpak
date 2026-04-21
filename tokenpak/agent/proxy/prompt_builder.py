"""Deprecated re-export shim (TokenPak D1 migration 2026-04-20).

The canonical home of this module is ``tokenpak.proxy.prompt_builder``.
This shim exists for backwards compatibility and will be removed
in TIP-2.0.
"""
from __future__ import annotations

import warnings as _warnings

_warnings.warn(
    "tokenpak.agent.proxy.prompt_builder is a deprecated re-export; "
    "import from tokenpak.proxy.prompt_builder instead. "
    "This shim will be removed in TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from tokenpak.proxy.prompt_builder import *  # noqa: F401,F403,E402

__all__ = ["DeterministicPromptPack", "PromptBuilder", "PromptCacheStats", "PromptParts", "apply_deterministic_cache_breakpoints", "apply_stable_cache_control", "build_stable_prefix", "build_volatile_tail", "classify_system_blocks", "get_stats", "inject_with_cache_boundary"]
