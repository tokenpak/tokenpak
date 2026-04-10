# SPDX-License-Identifier: Apache-2.0
"""TokenPak — Universal Content Compiler for LLMs.

Public API surface for TokenPak v1.1.0.
Formalizes importable classes for agent integrations, deployment, and testing.

Quick start:
    from tokenpak import TelemetryCollector, CacheManager, CompressionEngine, Budgeter

Sub-package imports:
    from tokenpak.telemetry import TelemetryCollector
    from tokenpak.engines import CompactionEngine, HeuristicEngine
    from tokenpak.registry import Block, BlockRegistry
    from tokenpak.budgeter import Budgeter
    from tokenpak.agentic.handoff import HandoffManager, HandoffBlock
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
        "connectors": lambda: __import__("tokenpak.connectors", fromlist=[""]),
        "proxy": lambda: __import__("tokenpak.proxy", fromlist=[""]),
        # Budgeting
        "Budgeter": lambda: __import__("tokenpak.budgeter", fromlist=["Budgeter"]).Budgeter,
        "BudgetBlock": lambda: __import__("tokenpak.budget", fromlist=["BudgetBlock"]).BudgetBlock,
        # Telemetry — CompletionTracker re-exported from tokenpak.telemetry
        "TelemetryCollector": lambda: __import__("tokenpak.telemetry.collector", fromlist=["TelemetryCollector"]).TelemetryCollector,
        "CacheManager": lambda: __import__("tokenpak.telemetry.cache", fromlist=["CacheStore"]).CacheStore,
        "CompletionTracker": lambda: __import__("tokenpak.telemetry", fromlist=["CompletionTracker"]).CompletionTracker,
        # Token utilities
        "count_tokens": lambda: __import__("tokenpak.tokens", fromlist=["count_tokens"]).count_tokens,
        # Packing
        "pack_prompt": lambda: __import__("tokenpak.pack", fromlist=["pack_prompt"]).pack_prompt,
        "ContextPack": lambda: __import__("tokenpak.pack", fromlist=["ContextPack"]).ContextPack,
        "PackBlock": lambda: __import__("tokenpak.pack", fromlist=["PackBlock"]).PackBlock,
        "CompiledResult": lambda: __import__("tokenpak.pack", fromlist=["CompiledResult"]).CompiledResult,
        # Registry
        "Block": lambda: __import__("tokenpak.registry", fromlist=["Block"]).Block,
        "BlockRegistry": lambda: __import__("tokenpak.registry", fromlist=["BlockRegistry"]).BlockRegistry,
        # Reports
        "Action": lambda: __import__("tokenpak.report", fromlist=["Action"]).Action,
        "CompileReport": lambda: __import__("tokenpak.report", fromlist=["CompileReport"]).CompileReport,
        "Decision": lambda: __import__("tokenpak.report", fromlist=["Decision"]).Decision,
        # CLI
        "main": lambda: __import__("tokenpak.cli", fromlist=["main"]).main,
        # Agent Handoff Protocol (tokenpak.agentic.handoff — canonical location)
        "HandoffManager": lambda: __import__("tokenpak.agentic.handoff", fromlist=["HandoffManager"]).HandoffManager,
        "HandoffBlock": lambda: __import__("tokenpak.agentic.handoff", fromlist=["HandoffBlock"]).HandoffBlock,
        "HandoffStatus": lambda: __import__("tokenpak.agentic.handoff", fromlist=["HandoffStatus"]).HandoffStatus,
        "HandoffWire": lambda: __import__("tokenpak.agentic.handoff", fromlist=["HandoffWire"]).HandoffWire,
        "TokenPak": lambda: __import__("tokenpak.agentic.handoff", fromlist=["TokenPak"]).TokenPak,
        "ContextRef": lambda: __import__("tokenpak.agentic.handoff", fromlist=["ContextRef"]).ContextRef,
        # Handoff alias
        "Handoff": lambda: __import__("tokenpak.agentic.handoff", fromlist=["HandoffWire"]).HandoffWire,
    }
    if name in _lazy_map:
        val = _lazy_map[name]()
        globals()[name] = val  # cache for subsequent accesses
        return val
    raise AttributeError(f"module 'tokenpak' has no attribute {name!r}")

# All public names are available lazily via __getattr__ above.
# CompressionEngine / HeuristicEngine / get_engine need graceful degradation — handle here.
try:
    from tokenpak.engines import get_engine
    from tokenpak.engines.base import CompactionEngine as CompressionEngine
    from tokenpak.engines.heuristic import HeuristicEngine
except ImportError:
    def get_engine(*args, **kwargs):
        raise NotImplementedError(
            "Compression engines require tokenpak-pro Enterprise license. "
            "Install: pip install tokenpak-pro"
        )
    CompressionEngine = None
    HeuristicEngine = None

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
]
