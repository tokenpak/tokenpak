"""tokenpak.telemetry.query — query functions for telemetry data.

Re-exports the core query functions from query_dsl so that existing
``from tokenpak.telemetry.query import get_model_usage`` imports
continue to work after the Phase 5B package restructure.
"""

from tokenpak.telemetry.query_dsl import (  # noqa: F401
    get_cost_summary,
    get_daily_trend,
    get_model_compression_breakdown,
    get_model_usage,
    get_recent_events,
    get_savings_report,
    QueryFilter,
    parse_filter,
    build_sql_where,
)

from tokenpak.telemetry.query_models import (  # noqa: F401
    CostSummary,
    DailyTrend,
    ModelCompressionBreakdown,
    ModelUsage,
    SavingsReport,
)

# Also expose the Phase 5B sub-modules
from . import api, audit, timeline  # noqa: F401
