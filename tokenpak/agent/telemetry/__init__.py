"""TokenPak Agent Telemetry — local stats collection, SQLite storage, and reporting."""

from .collector import TelemetryCollector, RequestStats, SessionStats, get_collector
from .storage import TelemetryStorage, get_telemetry_storage
from .footer import render_footer, render_footer_oneline, render_footer_compact
from .demo import run_demo, print_demo
from .replay import ReplayStore, ReplayEntry, get_replay_store
from .budget import BudgetTracker, BudgetConfig, BudgetStatus, get_budget_tracker

__all__ = [
    # collector
    "TelemetryCollector",
    "RequestStats",
    "SessionStats",
    "get_collector",
    # storage
    "TelemetryStorage",
    "get_telemetry_storage",
    # footer
    "render_footer",
    "render_footer_oneline",
    "render_footer_compact",
    # demo
    "run_demo",
    "print_demo",
    # replay (stub)
    "ReplayStore",
    "ReplayEntry",
    "get_replay_store",
    # budget (stub)
    "BudgetTracker",
    "BudgetConfig",
    "BudgetStatus",
    "get_budget_tracker",
]
