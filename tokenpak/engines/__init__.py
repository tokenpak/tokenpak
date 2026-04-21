"""Backwards-compat shim — see compression.engines.

Canonical home is ``tokenpak.compression.engines`` (Architecture §1).
Moved 2026-04-20 per D1 migration. Legacy shim removal target:
TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.compression.engines import (  # noqa: F401
    ENGINES,
    LLMLINGUA_AVAILABLE,
    CompactionEngine,
    HeuristicEngine,
    LLMLinguaEngine,
    get_engine,
)

warnings.warn(
    "tokenpak.engines is deprecated — "
    "import from tokenpak.compression.engines (canonical home since 2026-04-20, D1 migration). "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "CompactionEngine",
    "HeuristicEngine",
    "LLMLinguaEngine",
    "LLMLINGUA_AVAILABLE",
    "ENGINES",
    "get_engine",
]
