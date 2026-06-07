# SPDX-License-Identifier: Apache-2.0
"""Telemetry-driven recommendations engine (Component F).

Reads existing TokenPak telemetry tables (``tp_events``, ``tp_usage``,
``tp_pricing_catalog``) and the optional attribution tables
(``tp_savings_attribution``, ``tp_cache_miss_reasons``) when present, then
emits a ranked list of actionable recommendations.

Design rules (per proposal Phase 3 Component F):
- Recommendations are specific, ranked, telemetry-backed, safe by default,
  not noisy, and actionable without reading docs.
- All rules degrade gracefully when their data source is missing — an empty
  or fresh telemetry DB returns no recommendations rather than raising.
- Provider/platform-managed savings are NEVER claimed as TokenPak savings;
  the engine only labels what the underlying telemetry already attributes.

Layering: telemetry subsystem (Level 2). Pure read-only query module — no
mutation of telemetry, no provider calls, no platform-specific branches in
the engine itself. CLI dispatch lives in ``tokenpak/cli/commands/recommendations.py``.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Union

# Severity tiers map onto display sections.
SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"
SEVERITY_TRACKING = "tracking"

_SEVERITY_ORDER = {
    SEVERITY_HIGH: 0,
    SEVERITY_MEDIUM: 1,
    SEVERITY_LOW: 2,
    SEVERITY_TRACKING: 3,
}

_SECTION_TITLE = {
    SEVERITY_HIGH: "High Impact",
    SEVERITY_MEDIUM: "Medium Impact",
    SEVERITY_LOW: "Low Impact",
    SEVERITY_TRACKING: "Tracking",
}


@dataclass(frozen=True)
class Recommendation:
    """A single ranked, evidence-backed recommendation."""

    id: str
    severity: str
    title: str
    evidence: dict = field(default_factory=dict)
    action: str = ""
    expected: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RecommendationsResult:
    window_hours: int
    generated_at: float
    recommendations: list[Recommendation]
    filters: dict

    def to_dict(self) -> dict:
        return {
            "window_hours": self.window_hours,
            "generated_at_utc": time.strftime(
                "%Y-%m-%dT%H:%M:%SZ", time.gmtime(self.generated_at)
            ),
            "filters": self.filters,
            "count": len(self.recommendations),
            "recommendations": [r.to_dict() for r in self.recommendations],
        }


class RecommendationsEngine:
    """Telemetry-driven recommendation engine.

    Parameters
    ----------
    db_path:
        Optional path to ``telemetry.db``. When omitted, resolves via
        :func:`tokenpak.core.paths.get_db_path` so the engine reads the same
        store as the rest of the CLI.
    """

    DEFAULT_WINDOW_HOURS = 24
    UNATTRIBUTED_PCT_HIGH = 30.0
    UNATTRIBUTED_PCT_MEDIUM = 10.0
    ERROR_RATE_HIGH = 0.10
    ERROR_RATE_MEDIUM = 0.03
    MIN_REQUESTS_FOR_RULE = 5
    SCHEMA_INSTABILITY_MIN_MISSES = 5

    def __init__(self, db_path: Optional[Union[str, Path]] = None) -> None:
        if db_path is None:
            from tokenpak.core.paths import get_db_path

            db_path = get_db_path("telemetry.db")
        self._db_path = str(db_path)

    @property
    def db_path(self) -> str:
        return self._db_path

    def _open(self) -> Optional[sqlite3.Connection]:
        if self._db_path == ":memory:":
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            return conn
        if not Path(self._db_path).exists():
            return None
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def run(
        self,
        *,
        window_hours: int = DEFAULT_WINDOW_HOURS,
        model: Optional[str] = None,
        platform: Optional[str] = None,
        conn: Optional[sqlite3.Connection] = None,
    ) -> RecommendationsResult:
        """Evaluate all rules and return a ranked result."""
        if window_hours <= 0:
            raise ValueError("window_hours must be positive")

        owned = conn is None
        if conn is None:
            conn = self._open()

        recs: list[Recommendation] = []
        filters = {"model": model, "platform": platform}

        try:
            if conn is not None:
                ctx = _RuleContext(
                    conn=conn,
                    window_hours=window_hours,
                    model=model,
                    platform=platform,
                    engine=self,
                )
                for rule in _RULES:
                    try:
                        recs.extend(rule(ctx) or [])
                    except Exception as exc:
                        recs.append(
                            Recommendation(
                                id="engine.rule-error",
                                severity=SEVERITY_TRACKING,
                                title=f"Rule '{getattr(rule, '__name__', 'unknown')}' raised an exception",
                                evidence={"error": repr(exc)},
                                action="Run `tokenpak doctor` to inspect telemetry-store integrity.",
                                expected="Engine returns clean recommendations on next run.",
                            )
                        )
        finally:
            if owned and conn is not None:
                conn.close()

        recs.sort(key=lambda r: (_SEVERITY_ORDER.get(r.severity, 9), r.id))
        return RecommendationsResult(
            window_hours=window_hours,
            generated_at=time.time(),
            recommendations=recs,
            filters=filters,
        )


# ---------------------------------------------------------------------------
# Rule machinery
# ---------------------------------------------------------------------------


@dataclass
class _RuleContext:
    conn: sqlite3.Connection
    window_hours: int
    model: Optional[str]
    platform: Optional[str]
    engine: RecommendationsEngine

    @property
    def cutoff(self) -> float:
        return time.time() - self.window_hours * 3600

    def has_table(self, name: str) -> bool:
        cur = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        )
        return cur.fetchone() is not None

    def column_exists(self, table: str, column: str) -> bool:
        cur = self.conn.execute(f"PRAGMA table_info({table})")
        return any(row[1] == column for row in cur.fetchall())

    def event_filter_clause(self) -> tuple[str, list[Any]]:
        """Return SQL WHERE-fragment for filtering tp_events by window/model/platform.

        The clause uses ``e.`` as the table alias for tp_events.
        """
        clauses = ["e.ts >= ?"]
        params: list[Any] = [self.cutoff]
        if self.model:
            clauses.append("e.model = ?")
            params.append(self.model)
        if self.platform:
            clauses.append("(e.payload LIKE ? OR e.agent_id = ?)")
            params.extend([f'%"{self.platform}"%', self.platform])
        return " AND ".join(clauses), params


def _rule_zero_cache_lookups(ctx: _RuleContext) -> list[Recommendation]:
    """Detect periods where TokenPak proxy is taking traffic but no cache reads occur."""
    if not ctx.has_table("tp_usage") or not ctx.has_table("tp_events"):
        return []
    where, params = ctx.event_filter_clause()
    cur = ctx.conn.execute(
        f"""
        SELECT COUNT(DISTINCT e.trace_id) AS n_traces,
               COALESCE(SUM(u.cache_read), 0) AS total_cache_read
        FROM tp_events e
        LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
        WHERE {where}
        """,
        params,
    )
    row = cur.fetchone()
    if row is None:
        return []
    n_traces = int(row[0] or 0)
    total_cache_read = int(row[1] or 0)
    if n_traces < ctx.engine.MIN_REQUESTS_FOR_RULE:
        return []
    if total_cache_read > 0:
        return []
    return [
        Recommendation(
            id="cache.zero-lookups",
            severity=SEVERITY_HIGH,
            title=f"0 cache reads recorded across {n_traces} requests in last {ctx.window_hours}h",
            evidence={
                "n_traces": n_traces,
                "total_cache_read_tokens": 0,
                "window_hours": ctx.window_hours,
            },
            action=(
                "Enable proxy semantic-cache stage for safe routes (status_check, "
                "configuration_inspection, summarization). See `tokenpak/services/optimization/`."
            ),
            expected="TokenPak-managed cache reads on repeated safe-route traffic.",
        )
    ]


def _rule_high_unattributed(ctx: _RuleContext) -> list[Recommendation]:
    """Flag traffic whose savings/source cannot be attributed.

    Prefers the ``tp_savings_attribution`` table. Falls back to
    ``tp_usage.usage_source`` so the rule still works on telemetry stores
    that predate the attribution-v2 migration.
    """
    if ctx.has_table("tp_savings_attribution"):
        cur = ctx.conn.execute(
            """
            SELECT COALESCE(source, 'unknown') AS source,
                   COALESCE(SUM(saved_tokens), 0) AS saved
            FROM tp_savings_attribution
            GROUP BY COALESCE(source, 'unknown')
            """
        )
        rows = list(cur.fetchall())
        total = sum(int(r["saved"] or 0) for r in rows)
        if total <= 0:
            return []
        unattrib = sum(
            int(r["saved"] or 0)
            for r in rows
            if (r["source"] or "").lower() in ("unknown", "unattributed", "")
        )
        pct = (unattrib / total) * 100 if total > 0 else 0.0
        denom_label = "saved_tokens"
        denom_value = total
    else:
        if not ctx.has_table("tp_usage") or not ctx.has_table("tp_events"):
            return []
        where, params = ctx.event_filter_clause()
        cur = ctx.conn.execute(
            f"""
            SELECT COUNT(*) AS n,
                   SUM(CASE WHEN COALESCE(u.usage_source,'') IN ('unknown','') THEN 1 ELSE 0 END) AS n_unknown
            FROM tp_events e
            LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
            WHERE {where}
            """,
            params,
        )
        row = cur.fetchone()
        if row is None:
            return []
        n = int(row[0] or 0)
        if n < ctx.engine.MIN_REQUESTS_FOR_RULE:
            return []
        n_unknown = int(row[1] or 0)
        pct = (n_unknown / n) * 100 if n > 0 else 0.0
        denom_label = "n_requests"
        denom_value = n

    if pct >= ctx.engine.UNATTRIBUTED_PCT_HIGH:
        sev = SEVERITY_HIGH
    elif pct >= ctx.engine.UNATTRIBUTED_PCT_MEDIUM:
        sev = SEVERITY_MEDIUM
    else:
        return []

    return [
        Recommendation(
            id="attribution.high-unattributed",
            severity=sev,
            title=f"{pct:.0f}% of traffic is unattributed",
            evidence={
                "unattributed_pct": round(pct, 1),
                denom_label: denom_value,
                "window_hours": ctx.window_hours,
            },
            action=(
                "Add adapter usage parser or route metadata so savings can be credited "
                "to provider/platform/TokenPak sources."
            ),
            expected="Lower unattributed share in `tokenpak savings` and clearer source breakdown.",
        )
    ]


def _rule_missing_pricing(ctx: _RuleContext) -> list[Recommendation]:
    """Surface models that appear in traffic but have no configured pricing."""
    if not ctx.has_table("tp_events"):
        return []
    where, params = ctx.event_filter_clause()
    cur = ctx.conn.execute(
        f"""
        SELECT e.model, COUNT(DISTINCT e.trace_id) AS n
        FROM tp_events e
        WHERE {where} AND e.model != ''
        GROUP BY e.model
        ORDER BY n DESC
        """,
        params,
    )
    seen_models = [(str(r[0]), int(r[1])) for r in cur.fetchall() if r[0]]
    if not seen_models:
        return []

    priced: set[str] = set()
    if ctx.has_table("tp_pricing_catalog"):
        cur = ctx.conn.execute("SELECT catalog_json FROM tp_pricing_catalog")
        for row in cur.fetchall():
            try:
                catalog = json.loads(row[0] or "{}")
            except Exception:
                continue
            if isinstance(catalog, dict):
                models_section = catalog.get("models")
                if isinstance(models_section, dict):
                    priced.update(models_section.keys())
                else:
                    # Flat shape: top-level keys are model ids whose values are dicts.
                    priced.update(k for k, v in catalog.items() if isinstance(v, dict))

    # Secondary signal: query the authoritative MODEL_RATES dict. We avoid
    # `calculate_request_cost` because it transparently falls back to default
    # rates for unknown models, which would mask the gap this rule exists to surface.
    try:
        from tokenpak.telemetry.pricing_rates import MODEL_RATES  # type: ignore

        priced.update(MODEL_RATES.keys())
    except Exception:
        pass

    missing = [(m, n) for (m, n) in seen_models if m not in priced]
    recs: list[Recommendation] = []
    for model, n in missing:
        recs.append(
            Recommendation(
                id=f"pricing.missing:{model}",
                severity=SEVERITY_TRACKING,
                title=f"Model {model} has no configured pricing ({n} requests in window)",
                evidence={
                    "model": model,
                    "n_requests": n,
                    "window_hours": ctx.window_hours,
                },
                action=(
                    "Set model cost metadata in `tokenpak/telemetry/data/pricing_catalog.json` "
                    "or insert a `tp_pricing_catalog` row."
                ),
                expected="Accurate cost estimates in `tokenpak savings` and `tokenpak status`.",
            )
        )
    return recs


def _rule_schema_instability(ctx: _RuleContext) -> list[Recommendation]:
    """Detect tool-schema digest churn by counting recent ``tool_schema_digest_mismatch`` cache misses."""
    if not ctx.has_table("tp_cache_miss_reasons"):
        return []

    has_ts = ctx.column_exists("tp_cache_miss_reasons", "timestamp")
    if has_ts:
        cutoff_iso = time.strftime(
            "%Y-%m-%dT%H:%M:%SZ", time.gmtime(ctx.cutoff)
        )
        cur = ctx.conn.execute(
            """
            SELECT COUNT(*) FROM tp_cache_miss_reasons
            WHERE reason = 'tool_schema_digest_mismatch'
              AND timestamp >= ?
            """,
            (cutoff_iso,),
        )
    else:
        cur = ctx.conn.execute(
            """
            SELECT COUNT(*) FROM tp_cache_miss_reasons
            WHERE reason = 'tool_schema_digest_mismatch'
            """
        )

    n = int((cur.fetchone() or [0])[0] or 0)
    if n < ctx.engine.SCHEMA_INSTABILITY_MIN_MISSES:
        return []
    return [
        Recommendation(
            id="cache.schema-instability",
            severity=SEVERITY_MEDIUM,
            title=f"Tool schema digest mismatched on {n} requests in last {ctx.window_hours}h",
            evidence={
                "n_misses": n,
                "reason": "tool_schema_digest_mismatch",
                "window_hours": ctx.window_hours,
            },
            action=(
                "Verify tool schema normalization runs before upstream call. Inspect the "
                "tool-schema-stability stage in `services/optimization/`."
            ),
            expected="Higher cache hit rate for repeated tool-schema requests.",
        )
    ]


def _rule_high_error_rate(ctx: _RuleContext) -> list[Recommendation]:
    """Flag windows where a noticeable fraction of requests fail."""
    if not ctx.has_table("tp_events"):
        return []
    where, params = ctx.event_filter_clause()
    cur = ctx.conn.execute(
        f"""
        SELECT COUNT(DISTINCT e.trace_id) AS n,
               COUNT(DISTINCT CASE WHEN e.status NOT IN ('ok','') THEN e.trace_id ELSE NULL END) AS n_err
        FROM tp_events e
        WHERE {where}
        """,
        params,
    )
    row = cur.fetchone()
    if row is None:
        return []
    n = int(row[0] or 0)
    if n < ctx.engine.MIN_REQUESTS_FOR_RULE:
        return []
    n_err = int(row[1] or 0)
    err_rate = n_err / n if n > 0 else 0.0
    if err_rate >= ctx.engine.ERROR_RATE_HIGH:
        sev = SEVERITY_HIGH
    elif err_rate >= ctx.engine.ERROR_RATE_MEDIUM:
        sev = SEVERITY_MEDIUM
    else:
        return []
    return [
        Recommendation(
            id="errors.high-rate",
            severity=sev,
            title=(
                f"{err_rate * 100:.0f}% of requests failed in last "
                f"{ctx.window_hours}h ({n_err}/{n})"
            ),
            evidence={
                "error_rate": round(err_rate, 3),
                "n_errors": n_err,
                "n_requests": n,
                "window_hours": ctx.window_hours,
            },
            action=(
                "Run `tokenpak doctor`, check provider creds, and inspect recent failures "
                "via `tokenpak status --full`."
            ),
            expected="Lower request failure rate; faster recovery from provider outages.",
        )
    ]


_RULES = [
    _rule_zero_cache_lookups,
    _rule_high_unattributed,
    _rule_high_error_rate,
    _rule_schema_instability,
    _rule_missing_pricing,
]


# ---------------------------------------------------------------------------
# Formatters
# ---------------------------------------------------------------------------


def format_human(result: RecommendationsResult) -> str:
    """Format ``result`` for terminal display."""
    header = f"TokenPak Recommendations — last {result.window_hours}h"
    if not result.recommendations:
        return (
            f"{header}\n\n"
            "No recommendations. Telemetry shows nothing to act on right now.\n"
        )

    sections: dict[str, list[Recommendation]] = {
        SEVERITY_HIGH: [],
        SEVERITY_MEDIUM: [],
        SEVERITY_LOW: [],
        SEVERITY_TRACKING: [],
    }
    for r in result.recommendations:
        sections.setdefault(r.severity, sections[SEVERITY_TRACKING]).append(r)

    out: list[str] = [header, ""]
    counter = 0
    for sev in (SEVERITY_HIGH, SEVERITY_MEDIUM, SEVERITY_LOW, SEVERITY_TRACKING):
        items = sections.get(sev) or []
        if not items:
            continue
        out.append(_SECTION_TITLE[sev])
        for r in items:
            counter += 1
            out.append(f"{counter}. {r.title}")
            if r.action:
                out.append(f"   Action: {r.action}")
            if r.expected:
                out.append(f"   Expected: {r.expected}")
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def format_json(result: RecommendationsResult, indent: int = 2) -> str:
    """Format ``result`` as machine-readable JSON."""
    return json.dumps(result.to_dict(), indent=indent)


__all__ = [
    "Recommendation",
    "RecommendationsEngine",
    "RecommendationsResult",
    "SEVERITY_HIGH",
    "SEVERITY_MEDIUM",
    "SEVERITY_LOW",
    "SEVERITY_TRACKING",
    "format_human",
    "format_json",
]
