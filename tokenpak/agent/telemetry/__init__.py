"""TokenPak Agent Telemetry — local stats collection, SQLite storage, and reporting."""

import warnings as _warnings
_warnings.warn(
    "tokenpak.agent.telemetry is deprecated, use tokenpak.telemetry instead. "
    "This will be removed in v2.0.",
    DeprecationWarning,
    stacklevel=2,
)

from .budget import BudgetConfig, BudgetStatus, BudgetTracker, get_budget_tracker
from .collector import RequestStats, SessionStats, TelemetryCollector, get_collector
from .demo import print_demo, run_demo
from .footer import render_footer, render_footer_compact, render_footer_oneline
from .replay import ReplayEntry, ReplayStore, get_replay_store
from .storage import TelemetryStorage, get_telemetry_storage

__all__ = ['TelemetryCollector', 'RequestStats', 'SessionStats', 'get_collector', 'TelemetryStorage', 'get_telemetry_storage', 'render_footer', 'render_footer_oneline', 'render_footer_compact', 'run_demo', 'print_demo', 'ReplayStore', 'ReplayEntry', 'get_replay_store', 'BudgetTracker', 'BudgetConfig', 'BudgetStatus', 'get_budget_tracker', 'budget', 'collector', 'cost_tracker', 'demo', 'footer', 'replay', 'storage']
