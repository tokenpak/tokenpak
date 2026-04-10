"""TokenPak telemetry sub-package.

Exposes canonical types, provider adapters, the adapter registry,
and all agent-side telemetry: cost tracking, budget enforcement,
proxy stats collection, replay, footer rendering, and demos.
"""

from __future__ import annotations

try:
    from tokenpak.telemetry.cost_tracker import CostTracker as CompletionTracker
except ImportError:
    CompletionTracker = None  # type: ignore[assignment,misc]
from tokenpak.telemetry.adapters.registry import AdapterRegistry
from tokenpak.telemetry.cache import CacheStore as CacheManager
from tokenpak.telemetry.canonical import (
    CanonicalRequest,
    CanonicalResponse,
    CanonicalUsage,
    Confidence,
    UsageSource,
)

# --- File-watcher collector (existing in telemetry/) ---
from tokenpak.telemetry.collector import TelemetryCollector

# --- Merged from agent/telemetry/ ---
import warnings as _warnings
from tokenpak.telemetry.cost_tracker import CostTracker as CompletionTracker

class CostTracker(CompletionTracker):
    """Deprecated alias for CompletionTracker. Will be removed in v2.0."""
    def __init__(self, *args, **kwargs):
        _warnings.warn(
            "CostTracker is deprecated, use CompletionTracker instead. Will be removed in v2.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(*args, **kwargs)
from tokenpak.telemetry.budget import BudgetConfig, BudgetStatus, BudgetTracker, get_budget_tracker
from tokenpak.telemetry.proxy_collector import (
    RequestStats,
    SessionStats,
    TelemetryCollector as ProxyCollector,
    get_collector,
)
from tokenpak.telemetry.proxy_storage import TelemetryStorage, get_telemetry_storage
from tokenpak.telemetry.footer import render_footer, render_footer_compact, render_footer_oneline
from tokenpak.telemetry.demo import print_demo, run_demo
from tokenpak.telemetry.replay import ReplayEntry, ReplayStore, get_replay_store

__all__ = [
    # Canonical types
    "CanonicalRequest",
    "CanonicalResponse",
    "CanonicalUsage",
    "UsageSource",
    "Confidence",
    "AdapterRegistry",
    # File-watcher collector
    "TelemetryCollector",
    # Cost tracking
    "CostTracker",
    "CompletionTracker",
    "CacheManager",
    # Budget
    "BudgetTracker",
    "BudgetConfig",
    "BudgetStatus",
    "get_budget_tracker",
    # Proxy stats collector
    "ProxyCollector",
    "RequestStats",
    "SessionStats",
    "get_collector",
    # Storage (proxy-level)
    "TelemetryStorage",
    "get_telemetry_storage",
    # Footer rendering
    "render_footer",
    "render_footer_oneline",
    "render_footer_compact",
    # Demo
    "run_demo",
    "print_demo",
    # Replay
    "ReplayStore",
    "ReplayEntry",
    "get_replay_store",
]
