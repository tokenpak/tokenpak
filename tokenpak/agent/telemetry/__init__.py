"""TokenPak Agent Telemetry — local stats collection, SQLite storage, and reporting."""

from .budget import BudgetConfig, BudgetStatus, BudgetTracker, get_budget_tracker
from .collector import RequestStats, SessionStats, TelemetryCollector, get_collector
from .demo import print_demo, run_demo
from .footer import render_footer, render_footer_compact, render_footer_oneline
from .replay import ReplayEntry, ReplayStore, get_replay_store
from .storage import TelemetryStorage, get_telemetry_storage

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
