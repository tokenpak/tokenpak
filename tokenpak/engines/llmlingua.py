"""Backwards-compat shim — see compression.engines.llmlingua."""
from __future__ import annotations

try:
    from tokenpak.compression.engines.llmlingua import LLMLinguaEngine  # noqa: F401
except ImportError:
    LLMLinguaEngine = None  # type: ignore[assignment, misc]
