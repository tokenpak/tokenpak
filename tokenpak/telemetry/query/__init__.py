"""tokenpak.telemetry.query — query functions for telemetry data.

Re-exports the core query functions from query_dsl so that existing
``from tokenpak.telemetry.query import get_model_usage`` imports
continue to work after the Phase 5B package restructure.
"""

from tokenpak.telemetry.query_dsl import (  # noqa: F401
    QueryFilter,
    build_sql_where,
    get_cost_summary,
    get_daily_trend,
    get_model_compression_breakdown,
    get_model_usage,
    get_recent_events,
    get_savings_report,
    parse_filter,
)
from tokenpak.telemetry.query_models import (  # noqa: F401
    CostSummary,
    DailyTrend,
    ModelCompressionBreakdown,
    ModelUsage,
    SavingsReport,
)

# Also expose the Phase 5B sub-modules. ``audit`` and ``timeline`` are
# dependency-light. ``api`` exposes FastAPI HTTP endpoints and therefore
# requires FastAPI — an optional serve/dashboard extra. The core CLI value
# commands (``cost``/``savings``) only need the query_dsl functions re-exported
# above, so a missing FastAPI must not break importing this package or the
# receipt-backed savings summary. Keep the API sub-module optional.
from . import audit, timeline  # noqa: F401

try:
    from . import api  # noqa: F401  (requires FastAPI; optional serve extra)
except ImportError:  # pragma: no cover - exercised via the FastAPI-absent test
    api = None  # type: ignore[assignment]
