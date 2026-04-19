"""
TokenPak Prometheus Metrics Exporter

Generates Prometheus text format output for the GET /metrics endpoint.

Metrics exported:
- tokenpak_requests_total        (counter, labels: provider, status)
- tokenpak_tokens_total          (counter, labels: provider, direction)
- tokenpak_cost_usd_total        (counter, labels: provider)
- tokenpak_request_duration_seconds (histogram, labels: provider)
- tokenpak_compression_ratio     (gauge, labels: provider)
- tokenpak_circuit_state         (gauge, labels: provider) — 0=closed, 1=open, 2=half-open
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .storage import TelemetryDB

logger = logging.getLogger(__name__)

# Histogram bucket boundaries in seconds (converted from ms)
DURATION_BUCKETS = [0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0]


def _escape_label_value(v: str) -> str:
    """Escape special characters in Prometheus label values."""
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _label_str(**labels: str) -> str:
    """Format label dict as Prometheus label set string."""
    parts = [f'{k}="{_escape_label_value(v)}"' for k, v in labels.items() if v]
    return "{" + ",".join(parts) + "}" if parts else ""


def _format_value(v: float) -> str:
    """Format a numeric value for Prometheus output."""
    if v == float("inf"):
        return "+Inf"
    if v != v:  # NaN
        return "NaN"
    # Use integer form for whole numbers to keep output clean
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return f"{v:.6g}"


class PrometheusMetricsCollector:
    """
    Collects and renders TokenPak metrics in Prometheus text exposition format.

    Usage::

        collector = PrometheusMetricsCollector(storage)
        text = collector.collect()
        # Return as text/plain; charset=utf-8
    """

    def __init__(
        self,
        storage: "TelemetryDB",
        circuit_breaker: Optional[Any] = None,
    ) -> None:
        self._storage = storage
        self._circuit = circuit_breaker  # Optional CircuitBreaker instance

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(self) -> str:
        """Query storage and render full Prometheus metrics text."""
        lines: list[str] = []
        try:
            per_provider = self._query_per_provider_stats()
            duration_data = self._query_duration_histograms()
            compression_data = self._query_compression_ratios()
            circuit_data = self._get_circuit_states(per_provider)

            self._emit_requests_total(lines, per_provider)
            self._emit_tokens_total(lines, per_provider)
            self._emit_cost_total(lines, per_provider)
            self._emit_duration_histogram(lines, duration_data)
            self._emit_compression_ratio(lines, compression_data)
            self._emit_circuit_state(lines, circuit_data)

        except Exception as e:
            logger.error("PrometheusMetricsCollector.collect() error: %s", e)
            lines.append(f"# ERROR: {e}")

        lines.append("")  # trailing newline
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Data queries
    # ------------------------------------------------------------------

    def _query_per_provider_stats(self) -> list[dict[str, Any]]:
        """
        Return per-(provider, status) request/token/cost aggregates.

        Returns list of dicts with keys:
          provider, status, requests, input_tokens, output_tokens,
          tokens_saved, cost_total, savings_total
        """
        cur = self._storage._conn.cursor()
        sql = """
            SELECT
                COALESCE(e.provider, 'unknown')   AS provider,
                COALESCE(e.status, 'success')     AS status,
                COUNT(DISTINCT e.trace_id)        AS requests,
                COALESCE(SUM(u.input_billed), 0)  AS input_tokens,
                COALESCE(SUM(u.output_billed), 0) AS output_tokens,
                COALESCE(SUM(u.cache_read), 0)    AS tokens_saved,
                COALESCE(SUM(c.cost_total), 0)    AS cost_total,
                COALESCE(SUM(c.savings_total), 0) AS savings_total
            FROM tp_events e
            LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
            LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
            GROUP BY e.provider, e.status
        """
        cur.execute(sql)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def _query_duration_histograms(self) -> dict[str, dict[str, Any]]:
        """
        Return per-provider duration histogram data.

        Returns dict keyed by provider with keys:
          count, sum_seconds, buckets (dict le_seconds → count_cumulative)
        """
        cur = self._storage._conn.cursor()
        # Fetch all non-zero durations grouped by provider
        cur.execute("""
            SELECT
                COALESCE(provider, 'unknown') AS provider,
                duration_ms
            FROM tp_events
            WHERE duration_ms > 0
        """)
        rows = cur.fetchall()

        result: dict[str, dict[str, Any]] = {}
        for provider, duration_ms in rows:
            dur_s = duration_ms / 1000.0
            if provider not in result:
                result[provider] = {
                    "count": 0,
                    "sum_seconds": 0.0,
                    "raw": [],
                }
            result[provider]["count"] += 1
            result[provider]["sum_seconds"] += dur_s
            result[provider]["raw"].append(dur_s)

        # Compute cumulative bucket counts
        for provider, data in result.items():
            raw = sorted(data["raw"])
            buckets: dict[float, int] = {}
            for le in DURATION_BUCKETS:
                buckets[le] = sum(1 for d in raw if d <= le)
            buckets[float("inf")] = data["count"]
            data["buckets"] = buckets
            del data["raw"]

        return result

    def _query_compression_ratios(self) -> list[dict[str, Any]]:
        """
        Return average compression ratio per provider over the last 24h.

        Compression ratio = tokens_saved / (input_tokens + tokens_saved)
        A ratio of 0.47 means 47% of tokens were served from cache.
        Returns None ratio when no cache data exists.
        """
        cur = self._storage._conn.cursor()
        since = time.time() - 86400  # last 24h
        sql = """
            SELECT
                COALESCE(e.provider, 'unknown') AS provider,
                COALESCE(SUM(u.cache_read), 0)  AS tokens_saved,
                COALESCE(SUM(u.input_billed + COALESCE(u.cache_read, 0)), 0) AS total_input
            FROM tp_events e
            LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
            WHERE e.ts >= ?
            GROUP BY e.provider
        """
        cur.execute(sql, (since,))
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, row)) for row in cur.fetchall()]

        for row in rows:
            total = row["total_input"]
            saved = row["tokens_saved"]
            row["compression_ratio"] = round(saved / total, 4) if total > 0 else 0.0

        return rows

    def _get_circuit_states(
        self,
        per_provider: list[dict[str, Any]],
    ) -> dict[str, int]:
        """
        Return circuit state per provider: 0=closed, 1=open, 2=half-open.

        Uses the attached CircuitBreaker if available; otherwise falls back
        to 0 (closed/unknown) for all providers.
        """
        providers = {row["provider"] for row in per_provider}
        states: dict[str, int] = {}

        if self._circuit is not None:
            for provider in providers:
                state = self._circuit.get_state(provider)
                if state.get("is_open"):
                    # half-open if within cool-down but a probe is allowed
                    states[provider] = 1
                else:
                    states[provider] = 0
        else:
            for provider in providers:
                states[provider] = 0  # closed / unknown

        return states

    # ------------------------------------------------------------------
    # Metric emitters
    # ------------------------------------------------------------------

    def _emit_requests_total(
        self,
        lines: list[str],
        per_provider: list[dict[str, Any]],
    ) -> None:
        lines += [
            "# HELP tokenpak_requests_total Total LLM requests processed by TokenPak",
            "# TYPE tokenpak_requests_total counter",
        ]
        for row in per_provider:
            provider = row["provider"] or "unknown"
            status = row["status"] or "success"
            count = int(row["requests"])
            labels = _label_str(provider=provider, status=status)
            lines.append(f"tokenpak_requests_total{labels} {count}")
        lines.append("")

    def _emit_tokens_total(
        self,
        lines: list[str],
        per_provider: list[dict[str, Any]],
    ) -> None:
        lines += [
            "# HELP tokenpak_tokens_total Total tokens processed by TokenPak",
            "# TYPE tokenpak_tokens_total counter",
        ]
        # Aggregate by provider (collapse status dimension for token counts)
        by_prov: dict[str, dict[str, int]] = {}
        for row in per_provider:
            p = row["provider"] or "unknown"
            if p not in by_prov:
                by_prov[p] = {"input": 0, "output": 0, "saved": 0}
            by_prov[p]["input"] += int(row["input_tokens"])
            by_prov[p]["output"] += int(row["output_tokens"])
            by_prov[p]["saved"] += int(row["tokens_saved"])

        for provider, counts in by_prov.items():
            lines.append(
                f'tokenpak_tokens_total{_label_str(provider=provider, direction="input")}'
                f' {counts["input"]}'
            )
            lines.append(
                f'tokenpak_tokens_total{_label_str(provider=provider, direction="output")}'
                f' {counts["output"]}'
            )
            lines.append(
                f'tokenpak_tokens_total{_label_str(provider=provider, direction="saved")}'
                f' {counts["saved"]}'
            )
        lines.append("")

    def _emit_cost_total(
        self,
        lines: list[str],
        per_provider: list[dict[str, Any]],
    ) -> None:
        lines += [
            "# HELP tokenpak_cost_usd_total Total LLM cost in USD",
            "# TYPE tokenpak_cost_usd_total counter",
            "# HELP tokenpak_savings_usd_total Total USD saved via TokenPak compression",
            "# TYPE tokenpak_savings_usd_total counter",
        ]
        # Aggregate by provider
        by_prov: dict[str, dict[str, float]] = {}
        for row in per_provider:
            p = row["provider"] or "unknown"
            if p not in by_prov:
                by_prov[p] = {"cost": 0.0, "savings": 0.0}
            by_prov[p]["cost"] += float(row["cost_total"])
            by_prov[p]["savings"] += float(row["savings_total"])

        for provider, totals in by_prov.items():
            labels = _label_str(provider=provider)
            lines.append(f"tokenpak_cost_usd_total{labels} {_format_value(totals['cost'])}")
        lines.append("")
        for provider, totals in by_prov.items():
            labels = _label_str(provider=provider)
            lines.append(f"tokenpak_savings_usd_total{labels} {_format_value(totals['savings'])}")
        lines.append("")

    def _emit_duration_histogram(
        self,
        lines: list[str],
        duration_data: dict[str, dict[str, Any]],
    ) -> None:
        lines += [
            "# HELP tokenpak_request_duration_seconds LLM request duration in seconds",
            "# TYPE tokenpak_request_duration_seconds histogram",
        ]
        for provider, data in duration_data.items():
            labels_base = _label_str(provider=provider)
            # Emit bucket lines
            for le, count in data["buckets"].items():
                le_str = "+Inf" if le == float("inf") else str(le)
                # Build label set with 'le' appended
                raw_labels = f'provider="{_escape_label_value(provider)}",le="{le_str}"'
                lines.append(f"tokenpak_request_duration_seconds_bucket{{{raw_labels}}} {count}")
            # Emit sum and count
            lines.append(
                f"tokenpak_request_duration_seconds_sum{labels_base}"
                f" {_format_value(data['sum_seconds'])}"
            )
            lines.append(
                f"tokenpak_request_duration_seconds_count{labels_base}" f" {data['count']}"
            )
        lines.append("")

    def _emit_compression_ratio(
        self,
        lines: list[str],
        compression_data: list[dict[str, Any]],
    ) -> None:
        lines += [
            "# HELP tokenpak_compression_ratio Token compression ratio (tokens_saved / total_input) last 24h",
            "# TYPE tokenpak_compression_ratio gauge",
        ]
        for row in compression_data:
            provider = row["provider"] or "unknown"
            ratio = row["compression_ratio"]
            labels = _label_str(provider=provider)
            lines.append(f"tokenpak_compression_ratio{labels} {_format_value(ratio)}")
        lines.append("")

    def _emit_circuit_state(
        self,
        lines: list[str],
        circuit_data: dict[str, int],
    ) -> None:
        lines += [
            "# HELP tokenpak_circuit_state Circuit breaker state: 0=closed, 1=open, 2=half-open",
            "# TYPE tokenpak_circuit_state gauge",
        ]
        for provider, state in circuit_data.items():
            labels = _label_str(provider=provider)
            lines.append(f"tokenpak_circuit_state{labels} {state}")
        lines.append("")
