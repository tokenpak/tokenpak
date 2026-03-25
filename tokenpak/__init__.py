# SPDX-License-Identifier: MIT
"""TokenPak — Universal Content Compiler for LLMs.

Public API surface for TokenPak v1.0.1.
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

__version__ = "1.0.2"
__author__ = "Kevin Yang"
__license__ = "MIT"
__description__ = "Deterministic compression for multi-agent AI workflows"

# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Sub-packages (for advanced use)
# ---------------------------------------------------------------------------
from tokenpak import agent, connectors, proxy

# CompletionTracker: tracks per-completion cost, model, and latency
from tokenpak.agent.telemetry.cost_tracker import CostTracker as CompletionTracker
from tokenpak.budget import BudgetBlock

# ---------------------------------------------------------------------------
# Budgeting
# ---------------------------------------------------------------------------
from tokenpak.budgeter import Budgeter

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
from tokenpak.cli import main

# ---------------------------------------------------------------------------
# Compression / Compaction Engines
# ---------------------------------------------------------------------------
# CompressionEngine: abstract base for all compaction strategies
# Graceful degradation if engines are not available (pro-only installs)
try:
    from tokenpak.engines import get_engine
    from tokenpak.engines.base import CompactionEngine as CompressionEngine
    from tokenpak.engines.heuristic import HeuristicEngine
except ImportError:
    # Graceful degradation for OSS
    def get_engine(*args, **kwargs):
        raise NotImplementedError(
            "Compression engines require tokenpak-pro Enterprise license. "
            "Install: pip install tokenpak-pro"
        )
    CompressionEngine = None
    HeuristicEngine = None

# ---------------------------------------------------------------------------
# Agent Handoff Protocol
# ---------------------------------------------------------------------------
from tokenpak.agent.agentic.handoff import (
    ContextRef,
    HandoffBlock,
    HandoffManager,
    HandoffStatus,
    HandoffWire,
    TokenPak,
)
from tokenpak.pack import CompiledResult, ContextPack, PackBlock, pack_prompt

# ---------------------------------------------------------------------------
# Content Blocks
# ---------------------------------------------------------------------------
from tokenpak.registry import Block, BlockRegistry

# ---------------------------------------------------------------------------
# Compile Reports
# ---------------------------------------------------------------------------
from tokenpak.report import Action, CompileReport, Decision

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
# CacheManager: semantic cache store (get/set/hit-rate tracking)
from tokenpak.telemetry.cache import CacheStore as CacheManager
from tokenpak.telemetry.collector import TelemetryCollector

# ---------------------------------------------------------------------------
# Token Counting (Level 1 — single import, zero config)
# ---------------------------------------------------------------------------
from tokenpak.tokens import count_tokens
from tokenpak.trace import (  # noqa: F401
    TokenPakTrace,
    TraceBuilder,
    assert_no_leak,
    attach_trace_envelope,
    attach_trace_header,
    read_trace_envelope,
    read_trace_header,
    strip_trace,
    strip_trace_header,
)

# HandoffWire is the intended top-level "Handoff" API (pack-based wire format)
# The internal Handoff dataclass (file-based) is available via
# tokenpak.agent.agentic.handoff.Handoff
Handoff = HandoffWire  # type: ignore

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
    # Agent Handoff Protocol
    "HandoffBlock",
    "HandoffManager",
    "HandoffStatus",
    "Handoff",
    "HandoffWire",
    "ContextRef",
    "TokenPak",
    # CLI
    "main",
    # Sub-packages
    "connectors",
    "agent",
    "proxy",
]
