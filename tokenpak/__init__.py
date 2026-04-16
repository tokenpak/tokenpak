# SPDX-License-Identifier: Apache-2.0
"""TokenPak — Universal Content Compiler for LLMs.

Public API surface for TokenPak v1.1.0.
Formalizes importable classes for agent integrations, deployment, and testing.

Quick start:
    from tokenpak import TelemetryCollector, CacheManager, CompressionEngine, Budgeter

Sub-package imports:
    from tokenpak.telemetry import TelemetryCollector
    from tokenpak.compression.engines import CompactionEngine, HeuristicEngine
    from tokenpak.core.registry import Block, BlockRegistry
    from tokenpak.telemetry.budgeter import Budgeter
    from tokenpak.orchestration.handoff import HandoffManager, HandoffBlock
"""

from __future__ import annotations

__version__ = "1.1.0"
__author__ = "TokenPak Contributors"
__license__ = "Apache-2.0"
__description__ = "Deterministic compression for multi-agent AI workflows"

# ---------------------------------------------------------------------------
# Lazy public API — imports deferred to avoid 2-4s startup cost when only
# sub-modules (e.g. tokenpak.proxy.token_cache) are needed directly.
# All names remain importable via `from tokenpak import X` via __getattr__.
# ---------------------------------------------------------------------------

def __getattr__(name: str):
    """Lazy top-level attribute resolution — defers heavy imports until used."""
    _lazy_map = {
        # Sub-packages
        "connectors": lambda: __import__("tokenpak.sources", fromlist=[""]),
        "proxy": lambda: __import__("tokenpak.proxy", fromlist=[""]),
        "watchdog": lambda: __import__("tokenpak.proxy.proxy_watchdog", fromlist=[""]),
        "extensions": lambda: __import__("tokenpak.core.extensions", fromlist=[""]),
        # Compression engines (lazy to avoid 2-4s startup penalty)
        "CompressionEngine": lambda: __import__("tokenpak.compression.engines.base", fromlist=["CompactionEngine"]).CompactionEngine,
        "HeuristicEngine": lambda: __import__("tokenpak.compression.engines.heuristic", fromlist=["HeuristicEngine"]).HeuristicEngine,
        "get_engine": lambda: __import__("tokenpak.compression.engines", fromlist=["get_engine"]).get_engine,
        # Budgeting
        "Budgeter": lambda: __import__("tokenpak.telemetry.budgeter", fromlist=["Budgeter"]).Budgeter,
        "BudgetBlock": lambda: __import__("tokenpak.telemetry.budget_allocator", fromlist=["BudgetBlock"]).BudgetBlock,
        # Telemetry — CompletionTracker re-exported from tokenpak.telemetry
        "TelemetryCollector": lambda: __import__("tokenpak.telemetry.collector", fromlist=["TelemetryCollector"]).TelemetryCollector,
        "CacheManager": lambda: __import__("tokenpak.cache.cache_manager", fromlist=["CacheManager"]).CacheManager,
        "CompletionTracker": lambda: __import__("tokenpak.telemetry", fromlist=["CompletionTracker"]).CompletionTracker,
        # Token utilities
        "count_tokens": lambda: __import__("tokenpak.telemetry.tokens", fromlist=["count_tokens"]).count_tokens,
        # Packing
        "pack_prompt": lambda: __import__("tokenpak.compression.pack", fromlist=["pack_prompt"]).pack_prompt,
        "ContextPack": lambda: __import__("tokenpak.compression.pack", fromlist=["ContextPack"]).ContextPack,
        "PackBlock": lambda: __import__("tokenpak.compression.pack", fromlist=["PackBlock"]).PackBlock,
        "CompiledResult": lambda: __import__("tokenpak.compression.pack", fromlist=["CompiledResult"]).CompiledResult,
        # Registry
        "Block": lambda: __import__("tokenpak.core.registry", fromlist=["Block"]).Block,
        "BlockRegistry": lambda: __import__("tokenpak.core.registry", fromlist=["BlockRegistry"]).BlockRegistry,
        # Reports
        "Action": lambda: __import__("tokenpak.compression.report", fromlist=["Action"]).Action,
        "CompileReport": lambda: __import__("tokenpak.compression.report", fromlist=["CompileReport"]).CompileReport,
        "Decision": lambda: __import__("tokenpak.compression.report", fromlist=["Decision"]).Decision,
        # CLI
        "main": lambda: __import__("tokenpak.cli", fromlist=["main"]).main,
        # Agent Handoff Protocol (tokenpak.orchestration.handoff — canonical location)
        "HandoffManager": lambda: __import__("tokenpak.orchestration.handoff", fromlist=["HandoffManager"]).HandoffManager,
        "HandoffBlock": lambda: __import__("tokenpak.orchestration.handoff", fromlist=["HandoffBlock"]).HandoffBlock,
        "HandoffStatus": lambda: __import__("tokenpak.orchestration.handoff", fromlist=["HandoffStatus"]).HandoffStatus,
        "HandoffWire": lambda: __import__("tokenpak.orchestration.handoff", fromlist=["HandoffWire"]).HandoffWire,
        "TokenPak": lambda: __import__("tokenpak.orchestration.handoff", fromlist=["TokenPak"]).TokenPak,
        "ContextRef": lambda: __import__("tokenpak.orchestration.handoff", fromlist=["ContextRef"]).ContextRef,
        # Handoff alias
        "Handoff": lambda: __import__("tokenpak.orchestration.handoff", fromlist=["HandoffWire"]).HandoffWire,
    }
    if name in _lazy_map:
        val = _lazy_map[name]()
        globals()[name] = val  # cache for subsequent accesses
        return val
    raise AttributeError(f"module 'tokenpak' has no attribute {name!r}")

# All public names are available lazily via __getattr__ above.
# CompressionEngine / HeuristicEngine / get_engine are in the lazy map.
# No eager imports here — this was the source of a 2-4s startup penalty.

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
    "proxy",
    "watchdog",
]
