"""TokenPak — Universal Content Compiler for LLMs.

Public API surface for TokenPak v0.1.0.
Formalizes importable classes for agent integrations, deployment, and testing.

Quick start:
    from tokenpak import TelemetryCollector, CacheManager, CompressionEngine, Budgeter

Sub-package imports:
    from tokenpak.telemetry import TelemetryCollector
    from tokenpak.engines import CompactionEngine, HeuristicEngine
    from tokenpak.registry import Block, BlockRegistry
    from tokenpak.budgeter import Budgeter
"""

from __future__ import annotations

__version__ = "1.0.0"
__author__ = "Kevin Yang"
__license__ = "MIT"
__description__ = "Deterministic compression for multi-agent AI workflows"

# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------
from tokenpak.telemetry.collector import TelemetryCollector

# CompletionTracker: tracks per-completion cost, model, and latency
from tokenpak.agent.telemetry.cost_tracker import CostTracker as CompletionTracker

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
# CacheManager: semantic cache store (get/set/hit-rate tracking)
from tokenpak.telemetry.cache import CacheStore as CacheManager

# ---------------------------------------------------------------------------
# Compression / Compaction Engines
# ---------------------------------------------------------------------------
# CompressionEngine: abstract base for all compaction strategies
from tokenpak.engines.base import CompactionEngine as CompressionEngine
from tokenpak.engines.heuristic import HeuristicEngine
from tokenpak.engines import get_engine

# ---------------------------------------------------------------------------
# Content Blocks
# ---------------------------------------------------------------------------
from tokenpak.registry import Block, BlockRegistry

# ---------------------------------------------------------------------------
# Budgeting
# ---------------------------------------------------------------------------
from tokenpak.budgeter import Budgeter
from tokenpak.budget import BudgetBlock

# ---------------------------------------------------------------------------
# Compile Reports
# ---------------------------------------------------------------------------
from tokenpak.report import Action, CompileReport, Decision
from tokenpak.pack import ContextPack, PackBlock, CompiledResult, pack_prompt

# ---------------------------------------------------------------------------
# Token Counting (Level 1 — single import, zero config)
# ---------------------------------------------------------------------------
from tokenpak.tokens import count_tokens

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
from tokenpak.cli import main

# ---------------------------------------------------------------------------
# Sub-packages (for advanced use)
# ---------------------------------------------------------------------------
from tokenpak import connectors
from tokenpak import agent
from tokenpak import proxy

# ---------------------------------------------------------------------------
# Public API declaration
# ---------------------------------------------------------------------------
__all__ = [
    # Metadata
    "__version__",
    "__author__",
    "__license__",
    "__description__",
    # Telemetry
    "TelemetryCollector",
    "CompletionTracker",
    # Cache
    "CacheManager",
    # Compression
    "CompressionEngine",
    "HeuristicEngine",
    "get_engine",
    # Content Blocks
    "Block",
    "BlockRegistry",
    # Budgeting
    "Budgeter",
    "BudgetBlock",
    # Compile Reports
    "Action",
    "CompileReport",
    "Decision",
    "ContextPack",
    "PackBlock",
    "CompiledResult",
    # Incremental adoption helpers
    "count_tokens",
    "pack_prompt",
    # CLI
    "main",
    # Sub-packages
    "connectors",
    "agent",
    "proxy",
]
