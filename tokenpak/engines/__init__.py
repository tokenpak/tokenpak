"""Compaction engines module."""

from .base import CompactionEngine
from .heuristic import HeuristicEngine

# LLMLingua engines will be imported conditionally when available
try:
    from .llmlingua import LLMLinguaEngine

    LLMLINGUA_AVAILABLE = True
except ImportError:
    LLMLINGUA_AVAILABLE = False
    LLMLinguaEngine = None

ENGINES = {
    "heuristic": HeuristicEngine,
    "fast": HeuristicEngine,
}

if LLMLINGUA_AVAILABLE:
    ENGINES["balanced"] = LLMLinguaEngine
    ENGINES["llmlingua"] = LLMLinguaEngine


def get_engine(name: str = "heuristic") -> CompactionEngine:
    """Get a compaction engine by name."""
    engine_class = ENGINES.get(name, HeuristicEngine)
    return engine_class()
