# SPDX-License-Identifier: Apache-2.0
"""TokenPak — local proxy that compresses LLM context before it hits the API.

Public API surface for TokenPak.

Quick start:
    from tokenpak import TelemetryCollector, CacheManager, CompressionEngine, Budgeter

Sub-package imports:
    from tokenpak.telemetry import TelemetryCollector
    from tokenpak.compression.engines import CompactionEngine, HeuristicEngine
    from tokenpak.core.registry import Block, BlockRegistry
    from tokenpak.services.policy_service.budget.budgeter import Budgeter
"""

from __future__ import annotations

__version__ = "1.3.4"
__author__ = "TokenPak"
__license__ = "Apache-2.0"
__description__ = "Local proxy that compresses LLM context before it hits the API"

# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Sub-packages (for advanced use)
# ---------------------------------------------------------------------------
from tokenpak import agent, connectors, proxy

# ---------------------------------------------------------------------------
# Agent Handoff Protocol
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Agentic handoff protocol
# ---------------------------------------------------------------------------
from tokenpak.agent.agentic.handoff import (
    ContextRef,
    HandoffBlock,
    HandoffManager,
    HandoffStatus,
    HandoffWire,
    TokenPak,
)
from tokenpak.agent.agentic.handoff import (
    HandoffWire as Handoff,
)

# CompletionTracker: tracks per-completion cost, model, and latency
from tokenpak.agent.telemetry.cost_tracker import CostTracker as CompletionTracker
from tokenpak.services.policy_service.budget.rules import BudgetBlock

# ---------------------------------------------------------------------------
# Budgeting
# ---------------------------------------------------------------------------
from tokenpak.services.policy_service.budget.budgeter import Budgeter

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
# SKIPPED: from tokenpak.cli import main  # main not defined
from tokenpak.compression.engines import get_engine

# ---------------------------------------------------------------------------
# Compression / Compaction Engines
# ---------------------------------------------------------------------------
# CompressionEngine: abstract base for all compaction strategies
from tokenpak.compression.engines.base import CompactionEngine as CompressionEngine
from tokenpak.compression.engines.heuristic import HeuristicEngine
from tokenpak.compression.pack import CompiledResult, ContextPack, PackBlock, pack_prompt

# ---------------------------------------------------------------------------
# Content Blocks
# ---------------------------------------------------------------------------
from tokenpak.core.registry import Block, BlockRegistry

# ---------------------------------------------------------------------------
# Compile Reports
# ---------------------------------------------------------------------------
from tokenpak.telemetry.report import Action, CompileReport, Decision

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
# CacheManager: semantic cache store (get/set/hit-rate tracking)
from tokenpak.telemetry.cache import CacheStore as CacheManager
from tokenpak.telemetry.collector import TelemetryCollector

# ---------------------------------------------------------------------------
# Token Counting (Level 1 — single import, zero config)
# ---------------------------------------------------------------------------
from tokenpak.telemetry.tokens import count_tokens
from tokenpak.debug.trace import (  # noqa: F401
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
    "ContextRef",
    "TokenPak",
    # CLI
    "main",
    # Sub-packages
    "connectors",
    "agent",
    "proxy",
    # Agentic handoff protocol
    "ContextRef",
    "Handoff",
    "HandoffBlock",
    "HandoffManager",
    "HandoffStatus",
    "HandoffWire",
    "TokenPak",
]
