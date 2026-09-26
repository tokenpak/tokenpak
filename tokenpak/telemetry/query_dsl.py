"""Query DSL parser and query engine for TokenPak telemetry API.

Reads from the monitor database (the ``requests`` table) — the same store
the live proxy writes every completed request into, and the same resolver
``tokenpak doctor`` and the CLI's monitor-backed commands use
(``tokenpak._paths.monitor_db``). Earlier versions of this module queried a
separate ``telemetry.db`` (``tp_events``/``tp_usage``/``tp_costs``) that
nothing in the live proxy write path ever populated, so every caller of
these functions read a permanently empty store regardless of real traffic.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from tokenpak.models import detect_provider
from tokenpak.telemetry.pricing_rates import get_rates
from tokenpak.telemetry.query_models import (
    CostSummary,
    DailyTrend,
    ModelCompressionBreakdown,
    ModelUsage,
    SavingsReport,
)


@dataclass
class QueryFilter:
    """Filter parameters for telemetry database queries."""

    provider: Optional[str] = None
    model: Optional[str] = None
    agent: Optional[str] = None
    status: Optional[str] = None
    since_ts: Optional[float] = None
    until_ts: Optional[float] = None
    extra: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize this query result to a plain dict."""
        result: dict[str, Any] = {}
        if self.provider:
            result["provider"] = self.provider
        if self.model:
            result["model"] = self.model
        if self.agent:
            result["agent_id"] = self.agent
        if self.status:
            result["status"] = self.status
        if self.since_ts:
            result["since_ts"] = self.since_ts
        if self.until_ts:
            result["until_ts"] = self.until_ts
        result.update(self.extra)
        return result

    def is_empty(self) -> bool:
        """Return True if this filter has no active constraints."""
        return not any(
            [
                self.provider,
                self.model,
                self.agent,
                self.status,
                self.since_ts,
                self.until_ts,
                self.extra,
            ]
        )


def parse_filter(dsl: Optional[str]) -> QueryFilter:
    """Parse raw query-string params into a QueryFilter instance."""
    result = QueryFilter()
    if not dsl or not dsl.strip():
        return result
    for part in dsl.strip().split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            if not result.model:
                result.model = part
            continue
        key, _, value = part.partition(":")
        key, value = key.strip().lower(), value.strip()
        if not value:
            continue
        if key == "provider":
            result.provider = value
        elif key == "model":
            result.model = value
        elif key in ("agent", "agent_id"):
            result.agent = value
        elif key == "status":
            result.status = value
    return result


def build_sql_where(
    qf: QueryFilter, table_alias: str = "e", base_conditions: Optional[list[str]] = None
) -> tuple[str, list[Any]]:
    """Build a SQL WHERE clause from a QueryFilter; return (clause, params)."""
    conditions = list(base_conditions) if base_conditions else []
    params: list[Any] = []
    if qf.provider:
        conditions.append(f"{table_alias}.provider = ?")
        params.append(qf.provider)
    if qf.model:
        conditions.append(f"{table_alias}.model = ?")
        params.append(qf.model)
    if qf.agent:
        conditions.append(f"{table_alias}.agent_id = ?")
        params.append(qf.agent)
    if qf.status:
        conditions.append(f"{table_alias}.status = ?")
        params.append(qf.status)
    return ("WHERE " + " AND ".join(conditions), params) if conditions else ("", params)


def _default_db_path() -> Optional[Path]:
    """Resolve the canonical monitor store the live proxy writes to.

    Single-resolver rule: resolve at call time (not import time) so env
    changes are honored. Routes through ``tokenpak._paths.monitor_db``, the
    same resolver the proxy writer, ``tokenpak doctor``, and the CLI's
    monitor-backed commands use — so a read here sees the store the proxy
    actually populated instead of a second, never-written file. Returns
    ``None`` when no valid monitor DB exists anywhere (a fresh install),
    which callers must render as "no data yet", not as an error.
    """
    from tokenpak._paths import monitor_db

    return monitor_db(mode="read")


class TelemetryUnavailable(Exception):
    """The telemetry store cannot be read. Not an error about the data."""


def _get_conn(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open the monitor store read-only. Never creates it.

    ``sqlite3.connect`` creates the file when it is absent, so *reading*
    telemetry on a fresh install materialised an empty database — at the
    ambient umask — and then every query raised
    ``sqlite3.OperationalError: no such table: requests`` as a raw traceback.

    Read paths must not create state, and "there is nothing recorded yet" is
    a normal condition with a defined representation elsewhere in this module.
    Callers translate ``TelemetryUnavailable`` into their own unavailable
    value rather than propagating a stack trace to a user's terminal.
    """
    resolved = Path(db_path) if db_path else _default_db_path()
    if resolved is None or not resolved.exists():
        raise TelemetryUnavailable(f"no telemetry store at {resolved}")
    try:
        conn = sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
    except sqlite3.Error as exc:  # unreadable, locked, not a database
        raise TelemetryUnavailable(f"cannot read {resolved}: {exc}") from exc
    conn.row_factory = sqlite3.Row
    return conn


def _date_cutoff(days: int) -> str:
    """ISO date string ``days`` back from today, for a local-time cutoff.

    ``requests.timestamp`` is written as ``datetime.now().isoformat()`` —
    local time, not epoch, and not UTC. A plain ISO date string (e.g.
    ``"2026-08-27"``) sorts and compares correctly against it with a simple
    lexicographic ``>=``, matching the convention already used by the
    monitor-backed CLI paths (``_cli_core._monitor_db_savings`` et al.).
    """
    return (date.today() - timedelta(days=days)).isoformat()


def _iso_to_epoch(ts: str | None) -> float:
    """Parse a local ISO-8601 ``requests.timestamp`` string to a Unix epoch.

    Feeds a display/sort field, not a filter, so a null or unparseable
    timestamp degrades to ``0.0`` rather than raising.
    """
    if not ts:
        return 0.0
    try:
        return datetime.fromisoformat(ts).timestamp()
    except ValueError:
        return 0.0


def _bucket_savings_usd(model: str, compressed_tokens: float, cache_read_tokens: float) -> float:
    """Approximate USD saved for one (model) bucket of compression + cache reads.

    Compression: tokens removed entirely, priced at the model's input rate
    (they would have been billed at that rate had they been sent).
    Cache reads: priced at the difference between the input rate and the
    cached-read rate. This mirrors the live proxy's own bucket estimator
    (``tokenpak.proxy.monitor._estimate_bucket_savings_usd``) for the common
    case where the model registry has a model-specific cached-input rate —
    the two formulas are then identical. It differs only in the fallback
    used when a model has *no* registry cache rate: the proxy falls back to
    a per-provider discount table (``tokenpak.proxy.cache``), which this
    telemetry-layer helper cannot import (it sits below the proxy layer), so
    it falls back to the registry's flat 10%-of-input default instead. That
    fallback only applies to models without a specific cache rate on record.
    """
    rates = get_rates(model or None)
    input_rate = rates.get("input", 0.0)
    cached_rate = rates.get("cached", 0.0)
    compression_saved = (compressed_tokens / 1_000_000) * input_rate
    cache_saved = (cache_read_tokens / 1_000_000) * max(input_rate - cached_rate, 0.0)
    return compression_saved + cache_saved


def get_cost_summary(db_path: str | Path | None = None, days: int = 30) -> CostSummary:
    """Query aggregated cost summary from the monitor DB."""
    try:
        conn = _get_conn(db_path)
    except TelemetryUnavailable:
        return CostSummary(period_days=days)
    try:
        since = _date_cutoff(days)
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT COALESCE(SUM(estimated_cost),0) FROM requests "
                "WHERE timestamp >= ? AND status_code < 400",
                (since,),
            )
            total = cur.fetchone()[0] or 0.0
            cur.execute(
                "SELECT model, COALESCE(SUM(estimated_cost),0) as cost FROM requests "
                "WHERE timestamp >= ? AND status_code < 400 GROUP BY model",
                (since,),
            )
            by_model = {(r["model"] or "unknown"): (r["cost"] or 0.0) for r in cur.fetchall()}
            by_provider: dict[str, float] = {}
            for model, cost in by_model.items():
                provider = detect_provider(model)
                by_provider[provider] = by_provider.get(provider, 0.0) + cost
            cur.execute(
                "SELECT DATE(timestamp) as d, COALESCE(SUM(estimated_cost),0) as cost "
                "FROM requests WHERE timestamp >= ? AND status_code < 400 "
                "GROUP BY d ORDER BY d",
                (since,),
            )
            daily = [{"date": r["d"], "cost": r["cost"] or 0.0} for r in cur.fetchall()]
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc) or "no such column" in str(exc):
                return CostSummary(period_days=days)
            raise
        return CostSummary(
            total_cost=total,
            by_model=by_model,
            by_provider=by_provider,
            daily=daily,
            period_days=days,
        )
    finally:
        conn.close()


def get_model_usage(db_path: str | Path | None = None, days: int = 30) -> list[ModelUsage]:
    """Query per-model token usage from the monitor DB."""
    try:
        conn = _get_conn(db_path)
    except TelemetryUnavailable:
        return []
    try:
        since = _date_cutoff(days)
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT model, COUNT(*) as cnt, "
                "COALESCE(SUM(input_tokens),0) as inp, "
                "COALESCE(SUM(output_tokens),0) as outp, "
                "AVG(latency_ms) as avg_latency "
                "FROM requests WHERE timestamp >= ? AND status_code < 400 "
                "GROUP BY model ORDER BY cnt DESC",
                (since,),
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc) or "no such column" in str(exc):
                return []
            raise
        return [
            ModelUsage(
                model=r["model"] or "unknown",
                provider=detect_provider(r["model"] or ""),
                request_count=r["cnt"],
                total_input_tokens=r["inp"],
                total_output_tokens=r["outp"],
                avg_latency_ms=(
                    round(r["avg_latency"], 1) if r["avg_latency"] is not None else None
                ),
            )
            for r in rows
        ]
    finally:
        conn.close()


def get_savings_report(db_path: str | Path | None = None, days: int = 30) -> SavingsReport:
    """Query token/cost savings from the monitor DB.

    Only proxy-caused savings are counted in ``savings_amount``: a row's
    compression and cache-read tokens are credited only when its
    ``cache_origin`` is ``'proxy'``. Client-caused caching
    (``cache_origin='client'``, e.g. an agent's own prompt caching) and rows
    of unknown origin are excluded from the dollar/percentage totals — the
    same exclusion the live proxy's own savings accounting applies
    (``tokenpak.proxy.monitor.Monitor.get_savings_report``) — because that
    savings was not caused by this product. ``total_cost`` and
    ``cache_hit_rate`` are reported across all rows regardless of origin,
    since those are observed totals, not an attribution claim.
    """
    try:
        conn = _get_conn(db_path)
    except TelemetryUnavailable:
        # available=False is the documented "could not read the store"
        # state, distinct from a healthy store with zero observations.
        return SavingsReport(available=False)
    try:
        since = _date_cutoff(days)
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT COALESCE(SUM(estimated_cost),0) as tc, COUNT(*) as n "
                "FROM requests WHERE timestamp >= ? AND status_code < 400",
                (since,),
            )
            r = cur.fetchone()
            total_cost = float(r["tc"] or 0.0)
            observations = int(r["n"] or 0)

            try:
                cur.execute(
                    "SELECT model, "
                    "COALESCE(SUM(compressed_tokens),0) as comp, "
                    "COALESCE(SUM(cache_read_tokens),0) as cread "
                    "FROM requests WHERE timestamp >= ? AND status_code < 400 "
                    "AND cache_origin = 'proxy' "
                    "GROUP BY model",
                    (since,),
                )
                origin_rows = cur.fetchall()
            except sqlite3.OperationalError as exc:
                if "no such column" in str(exc) and "cache_origin" in str(exc):
                    # A monitor.db predating the cache_origin column has no
                    # way to distinguish proxy- from client-caused savings;
                    # every row on that schema version was proxy-managed, so
                    # treat all of it as attributable rather than reporting
                    # zero savings on an otherwise healthy store.
                    cur.execute(
                        "SELECT model, "
                        "COALESCE(SUM(compressed_tokens),0) as comp, "
                        "COALESCE(SUM(cache_read_tokens),0) as cread "
                        "FROM requests WHERE timestamp >= ? AND status_code < 400 "
                        "GROUP BY model",
                        (since,),
                    )
                    origin_rows = cur.fetchall()
                else:
                    raise

            savings_amount = 0.0
            for row in origin_rows:
                savings_amount += _bucket_savings_usd(
                    row["model"] or "", row["comp"] or 0, row["cread"] or 0
                )

            cur.execute(
                "SELECT COALESCE(SUM(cache_read_tokens),0) as cr, "
                "COALESCE(SUM(input_tokens + cache_read_tokens),0) as ti "
                "FROM requests WHERE timestamp >= ? AND status_code < 400",
                (since,),
            )
            cr_row = cur.fetchone()
            cache_read_all = cr_row["cr"] or 0
            total_in_all = cr_row["ti"] or 0
            cache_hit_rate = (cache_read_all / total_in_all) if total_in_all else 0.0

            estimated_without = total_cost + savings_amount
            savings_pct = (savings_amount / estimated_without * 100) if estimated_without else 0.0

            return SavingsReport(
                total_cost=total_cost,
                estimated_without_compression=estimated_without,
                savings_amount=savings_amount,
                savings_pct=savings_pct,
                cache_hit_rate=cache_hit_rate,
                observations=observations,
                available=True,
            )
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc) or "no such column" in str(exc):
                # Legacy/unrecognised schema — do not crash, but do not claim
                # a measurement either: available=False marks these floats as
                # meaningless so callers render "unavailable" instead of
                # "$0.00 (0.0%)".
                return SavingsReport(available=False)
            raise  # re-raise unexpected SQLite errors
    finally:
        conn.close()


def get_recent_events(
    db_path: str | Path | None = None, limit: int = 50
) -> list[dict[str, object]]:
    """Fetch the most recent request events, most recent first."""
    try:
        conn = _get_conn(db_path)
    except TelemetryUnavailable:
        return []
    try:
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT id, timestamp, model, agent_id, input_tokens, output_tokens, "
                "estimated_cost, status_code "
                "FROM requests ORDER BY timestamp DESC, id DESC LIMIT ?",
                (limit,),
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc) or "no such column" in str(exc):
                return []
            raise
        events = []
        for r in rows:
            status_code = r["status_code"]
            is_ok = status_code is not None and status_code < 400
            events.append(
                {
                    "trace_id": str(r["id"]),
                    "request_id": str(r["id"]),
                    "event_type": "request_end",
                    "ts": _iso_to_epoch(r["timestamp"]),
                    "provider": detect_provider(r["model"] or ""),
                    "model": r["model"],
                    "agent_id": r["agent_id"] or None,
                    "status": "ok" if is_ok else "error",
                    "error_class": None if is_ok else f"http_{status_code}",
                    "input_tokens": r["input_tokens"],
                    "output_tokens": r["output_tokens"],
                    "cost": r["estimated_cost"],
                }
            )
        return events
    finally:
        conn.close()


def get_model_compression_breakdown(
    db_path: str | Path | None = None, days: int = 1
) -> list[ModelCompressionBreakdown]:
    """Query per-model compression ratio breakdown from the monitor DB.

    Args:
        db_path: Optional path to the SQLite DB (uses default if None).
        days: Number of days to look back (default 1 for daily report).

    Returns:
        List of ModelCompressionBreakdown sorted by tokens_saved descending.
        Returns empty list if no data or DB is unavailable.

    ``compressed_tokens`` records tokens *removed* by compression (priced at
    the full input rate), not tokens remaining afterwards. So raw
    (pre-compression) tokens = ``input_tokens + compressed_tokens``, and
    final (billed) tokens = ``input_tokens``.

    ``savings_amount`` here is compression savings only — it deliberately
    does not blend in cache-read savings, unlike ``get_savings_report``'s
    aggregate figure, because this field is specifically about compression.
    """
    try:
        conn = _get_conn(db_path)
    except TelemetryUnavailable:
        return []
    try:
        since = _date_cutoff(days)
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT model, COUNT(*) as req_count, "
                "COALESCE(AVG(input_tokens + compressed_tokens),0) as avg_raw, "
                "COALESCE(AVG(input_tokens),0) as avg_final, "
                "COALESCE(SUM(compressed_tokens),0) as tokens_saved, "
                "COALESCE(SUM(CASE WHEN cache_origin='proxy' THEN compressed_tokens "
                "ELSE 0 END),0) as comp_proxy "
                "FROM requests WHERE timestamp >= ? AND status_code < 400 "
                "AND model IS NOT NULL AND model != '' "
                "GROUP BY model ORDER BY tokens_saved DESC",
                (since,),
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError as exc:
            if "no such column" in str(exc) and "cache_origin" in str(exc):
                cur.execute(
                    "SELECT model, COUNT(*) as req_count, "
                    "COALESCE(AVG(input_tokens + compressed_tokens),0) as avg_raw, "
                    "COALESCE(AVG(input_tokens),0) as avg_final, "
                    "COALESCE(SUM(compressed_tokens),0) as tokens_saved, "
                    "COALESCE(SUM(compressed_tokens),0) as comp_proxy "
                    "FROM requests WHERE timestamp >= ? AND status_code < 400 "
                    "AND model IS NOT NULL AND model != '' "
                    "GROUP BY model ORDER BY tokens_saved DESC",
                    (since,),
                )
                rows = cur.fetchall()
            elif "no such table" in str(exc):
                return []
            else:
                raise

        results = []
        for r in rows:
            avg_raw = r["avg_raw"] or 0.0
            avg_final = r["avg_final"] or 0.0
            # Compression ratio: final / raw (< 1.0 means compressed; 1.0 if no data)
            ratio = (avg_final / avg_raw) if avg_raw > 0 else 1.0
            model = r["model"] or "unknown"
            savings_amount = (r["comp_proxy"] or 0) / 1_000_000 * get_rates(model).get("input", 0.0)
            results.append(
                ModelCompressionBreakdown(
                    model=model,
                    request_count=r["req_count"],
                    avg_compression_ratio=round(ratio, 4),
                    tokens_saved=max(int(r["tokens_saved"] or 0), 0),
                    avg_raw_tokens=round(avg_raw, 1),
                    avg_final_tokens=round(avg_final, 1),
                    savings_amount=round(savings_amount, 6),
                )
            )
        return results
    finally:
        conn.close()


def get_daily_trend(db_path: str | Path | None = None, days: int = 30) -> list[DailyTrend]:
    """Fetch daily aggregated usage for trend charts, from the monitor DB."""
    try:
        conn = _get_conn(db_path)
    except TelemetryUnavailable:
        return []
    try:
        since = _date_cutoff(days)
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT DATE(timestamp) as d, "
                "COALESCE(SUM(estimated_cost),0) as cost, "
                "COALESCE(SUM(input_tokens),0) as inp, "
                "COALESCE(SUM(output_tokens),0) as outp, "
                "COUNT(*) as cnt "
                "FROM requests WHERE timestamp >= ? AND status_code < 400 "
                "GROUP BY d ORDER BY d",
                (since,),
            )
            rows = cur.fetchall()
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc) or "no such column" in str(exc):
                return []
            raise
        return [
            DailyTrend(
                date=r["d"],
                cost=r["cost"] or 0.0,
                input_tokens=r["inp"],
                output_tokens=r["outp"],
                request_count=r["cnt"],
            )
            for r in rows
        ]
    finally:
        conn.close()
