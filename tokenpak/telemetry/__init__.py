"""TokenPak telemetry sub-package.

Exposes canonical types, provider adapters, the adapter registry,
and all agent-side telemetry: cost tracking, budget enforcement,
proxy stats collection, replay, footer rendering, and demos.
"""

from __future__ import annotations

# --- Merged from agent/telemetry/ ---
import warnings as _warnings
from typing import Any

# --- Canonical types & adapters (existing) ---
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
from tokenpak.telemetry.cost_tracker import CostTracker as CompletionTracker


class CostTracker(CompletionTracker):
    """Deprecated alias for CompletionTracker. Will be removed in v2.0."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        _warnings.warn(
            "CostTracker is deprecated, use CompletionTracker instead. Will be removed in v2.0.",
            DeprecationWarning,
            stacklevel=2,
        )
        super().__init__(*args, **kwargs)


from tokenpak.telemetry.budget import BudgetConfig, BudgetStatus, BudgetTracker, get_budget_tracker
from tokenpak.telemetry.demo import print_demo, run_demo
from tokenpak.telemetry.footer import render_footer, render_footer_compact, render_footer_oneline
from tokenpak.telemetry.proxy_collector import (
    RequestStats,
    SessionStats,
    get_collector,
)
from tokenpak.telemetry.proxy_collector import (
    TelemetryCollector as ProxyCollector,
)
from tokenpak.telemetry.proxy_storage import TelemetryStorage, get_telemetry_storage
from tokenpak.telemetry.replay import ReplayEntry, ReplayStore, get_replay_store

__all__ = [
    "CanonicalRequest",
    "CanonicalResponse",
    "CanonicalUsage",
    "UsageSource",
    "Confidence",
    "AdapterRegistry",
    "TelemetryCollector",
    "CostTracker",
    "CompletionTracker",
    "CacheManager",
    "BudgetTracker",
    "BudgetConfig",
    "BudgetStatus",
    "get_budget_tracker",
    "ProxyCollector",
    "RequestStats",
    "SessionStats",
    "get_collector",
    "TelemetryStorage",
    "get_telemetry_storage",
    "render_footer",
    "render_footer_oneline",
    "render_footer_compact",
    "run_demo",
    "print_demo",
    "ReplayStore",
    "ReplayEntry",
    "get_replay_store",
    "adapters",
    "anon_metrics",
    "api",
    "budget",
    "cache",
    "canonical",
    "collector",
    "config",
    "cost",
    "cost_tracker",
    "dashboard",
    "demo",
    "error_logger",
    "event_schema",
    "export",
    "footer",
    "insights",
    "integrity",
    "local_exporter",
    "milestones",
    "models",
    "operational",
    "pipeline",
    "pipeline_trace",
    "pricing",
    "prometheus",
    "proxy_collector",
    "proxy_storage",
    "proxy_trace_integration",
    "query",
    "query_models",
    "replay",
    "reporter",
    "response_models",
    "rollups",
    "segmentizer",
    "server",
    "settings",
    "stats",
    "storage",
    "storage_base",
    "storage_events",
    "storage_rollups",
    "storage_segments",
    "storage_usage",
]
