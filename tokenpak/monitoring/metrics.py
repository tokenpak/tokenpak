"""
tokenpak/monitoring/metrics.py

Prometheus metrics collection for the TokenPak proxy server (port 8766).

Exposes a ``ProxyMetricsCollector`` that renders all required metrics in
Prometheus text exposition format.  Designed to be called from the
``GET /metrics`` handler in the proxy server's HTTP handler.

Metrics exported:
    tokenpak_requests_total        (counter, labels: provider, model)
    tokenpak_tokens_saved_total    (counter, labels: provider)
    tokenpak_cache_entries         (gauge)
    tokenpak_cache_memory_bytes    (gauge)
    tokenpak_cache_hit_ratio       (gauge)
    tokenpak_proxy_latency_ms      (histogram, labels: provider)
    tokenpak_up                    (gauge — 1=healthy, 0=down)

Data sources (in priority order):
    1. TelemetryDB   — per-provider/model breakdowns when DB is available
    2. Proxy session — aggregate in-memory counters as fallback
    3. CacheRegistry — live cache stats
    4. CircuitBreakerRegistry — provider health
    5. CompressionStats — latency and hit-ratio data

Usage::

    from tokenpak.monitoring.metrics import ProxyMetricsCollector

    collector = ProxyMetricsCollector(proxy_server=ps)
    text = collector.collect()
    # Return as Content-Type: text/plain; version=0.0.4; charset=utf-8
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Latency histogram bucket boundaries in milliseconds
_LATENCY_BUCKETS_MS = [50, 100, 250, 500, 1000, 2500, 5000, 10000]

# Default telemetry DB path (matches telemetry/api.py logic)
_DEFAULT_DB_PATH = Path(
    os.environ.get("TOKENPAK_DB_PATH", "")
    or str(Path(__file__).parent.parent.parent / "telemetry.db")
)


# ---------------------------------------------------------------------------
# Prometheus text format helpers
# ---------------------------------------------------------------------------

def _escape_label_value(v: str) -> str:
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _label_str(**labels: str) -> str:
    parts = [f'{k}="{_escape_label_value(str(v))}"' for k, v in labels.items() if v]
    return "{" + ",".join(parts) + "}" if parts else ""


def _fmt(v: float) -> str:
    if v == float("inf"):
        return "+Inf"
    if v != v:
        return "NaN"
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return f"{v:.6g}"


# ---------------------------------------------------------------------------
# ProxyMetricsCollector
# ---------------------------------------------------------------------------

class ProxyMetricsCollector:
    """
    Collects and renders TokenPak proxy metrics in Prometheus text format.

    Parameters
    ----------
    proxy_server : ProxyServer, optional
        Live proxy server instance for session + circuit-breaker data.
    db_path : str or Path, optional
        Path to the TelemetryDB for per-provider/model breakdowns.
        Falls back to the default ``telemetry.db`` path if not set.
    """

    def __init__(
        self,
        proxy_server: Optional[Any] = None,
        db_path: Optional[Any] = None,
    ) -> None:
        self._ps = proxy_server
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def collect(self) -> str:
        """Collect all metrics and return Prometheus text format string."""
        lines: List[str] = []
        try:
            # Rich per-provider data from TelemetryDB (best effort)
            db_rows = self._query_telemetry_db()

            # Always-available live data
            session = self._get_session()
            cache = self._get_cache_metrics()
            latency = self._get_latency_data()
            is_up = self._get_up_status()

            # Emit all metric families
            self._emit_requests_total(lines, db_rows, session)
            self._emit_tokens_saved_total(lines, db_rows, session)
            self._emit_cache_entries(lines, cache)
            self._emit_cache_memory_bytes(lines, cache)
            self._emit_cache_hit_ratio(lines, cache, session)
            self._emit_latency_histogram(lines, latency)
            self._emit_up(lines, is_up)

        except Exception as exc:
            logger.error("ProxyMetricsCollector.collect() failed: %s", exc)
            lines.append(f"# ERROR collecting metrics: {exc}")

        lines.append("")  # Prometheus expects a trailing newline
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Data collection helpers
    # ------------------------------------------------------------------

    def _query_telemetry_db(self) -> List[Dict[str, Any]]:
        """
        Query per-(provider, model) stats from TelemetryDB.

        Returns empty list if DB is unavailable or has no data.
        Each row has: provider, model, requests, tokens_saved, cost_total.
        """
        try:
            if not self._db_path.exists():
                return []
            import sqlite3
            conn = sqlite3.connect(str(self._db_path), timeout=2.0)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute("""
                SELECT
                    COALESCE(e.provider, 'unknown') AS provider,
                    COALESCE(e.model,    'unknown') AS model,
                    COUNT(DISTINCT e.trace_id)       AS requests,
                    COALESCE(SUM(u.cache_read), 0)   AS tokens_saved,
                    COALESCE(SUM(c.cost_total), 0)   AS cost_total
                FROM tp_events e
                LEFT JOIN tp_usage u ON e.trace_id = u.trace_id
                LEFT JOIN tp_costs c ON e.trace_id = c.trace_id
                GROUP BY e.provider, e.model
            """)
            rows = [dict(r) for r in cur.fetchall()]
            conn.close()
            return rows
        except Exception as exc:
            logger.debug("TelemetryDB query skipped: %s", exc)
            return []

    def _get_session(self) -> Dict[str, Any]:
        """Return proxy session dict (or empty defaults)."""
        defaults: Dict[str, Any] = {
            "requests": 0,
            "saved_tokens": 0,
            "input_tokens": 0,
            "cache_read_tokens": 0,
            "errors": 0,
            "cost": 0.0,
            "cost_saved": 0.0,
        }
        if self._ps is None:
            return defaults
        try:
            s = self._ps.session
            return {**defaults, **s}
        except Exception:
            return defaults

    def _get_cache_metrics(self) -> Dict[str, Any]:
        """Return cache entries, memory bytes, and hit-ratio from CacheRegistry."""
        defaults = {"entries": 0, "memory_bytes": 0, "hit_ratio": 0.0}
        try:
            from tokenpak.cache.registry import CacheRegistry
            import sys as _sys
            summary = CacheRegistry.summary()
            total_entries = sum(v.get("size", 0) for v in summary.values())
            # Estimate memory for each registered cache
            total_bytes = 0
            for name in CacheRegistry.names():
                cache = CacheRegistry.get(name)
                if cache is not None and hasattr(cache, "_store"):
                    store = getattr(cache, "_store", {})
                    # sys.getsizeof gives a rough per-object estimate
                    total_bytes += sum(
                        _sys.getsizeof(k) + _sys.getsizeof(v)
                        for k, v in store.items()
                    )
                    total_bytes += _sys.getsizeof(store)
            defaults["entries"] = total_entries
            defaults["memory_bytes"] = total_bytes
        except Exception as exc:
            logger.debug("Cache metrics unavailable: %s", exc)
        return defaults

    def _get_latency_data(self) -> Dict[str, Dict[str, Any]]:
        """
        Return per-provider latency histogram data from recent compression events.

        Falls back to the proxy's rolling compression stats.
        Returns dict: {provider: {count, sum_ms, buckets: {le_ms: cumulative_count}}}
        """
        result: Dict[str, Dict[str, Any]] = {}
        try:
            if self._ps is None:
                return result
            cs = getattr(self._ps, "compression_stats", None)
            if cs is None:
                return result
            # CompressionStats stores recent events in _recent deque
            recent = list(getattr(cs, "_recent", []))
            for evt in recent:
                provider = str(evt.get("model", "unknown")).split("/")[0] if isinstance(evt, dict) else "unknown"
                latency_ms = float(evt.get("latency_ms", 0)) if isinstance(evt, dict) else 0.0
                if provider not in result:
                    result[provider] = {"count": 0, "sum_ms": 0.0, "raw": []}
                result[provider]["count"] += 1
                result[provider]["sum_ms"] += latency_ms
                result[provider]["raw"].append(latency_ms)

            # Build cumulative histogram buckets
            for provider, data in result.items():
                raw = sorted(data.pop("raw"))
                buckets: Dict[float, int] = {}
                for le in _LATENCY_BUCKETS_MS:
                    buckets[le] = sum(1 for d in raw if d <= le)
                buckets[float("inf")] = data["count"]
                data["buckets"] = buckets
        except Exception as exc:
            logger.debug("Latency metrics unavailable: %s", exc)
        return result

    def _get_up_status(self) -> int:
        """Return 1 if the proxy is running and not shutting down, else 0."""
        try:
            if self._ps is None:
                return 1  # Assume up if we got a request
            shutdown = getattr(self._ps, "shutdown", None)
            if shutdown and getattr(shutdown, "is_shutting_down", False):
                return 0
            return 1
        except Exception:
            return 1

    # ------------------------------------------------------------------
    # Metric emitters
    # ------------------------------------------------------------------

    def _emit_requests_total(
        self,
        lines: List[str],
        db_rows: List[Dict[str, Any]],
        session: Dict[str, Any],
    ) -> None:
        lines += [
            "# HELP tokenpak_requests_total Total LLM requests proxied by TokenPak",
            "# TYPE tokenpak_requests_total counter",
        ]
        if db_rows:
            for row in db_rows:
                labels = _label_str(provider=row["provider"], model=row["model"])
                lines.append(f"tokenpak_requests_total{labels} {int(row['requests'])}")
        else:
            # Aggregate fallback — no provider/model breakdown available
            labels = _label_str(provider="unknown", model="unknown")
            lines.append(f"tokenpak_requests_total{labels} {int(session['requests'])}")
        lines.append("")

    def _emit_tokens_saved_total(
        self,
        lines: List[str],
        db_rows: List[Dict[str, Any]],
        session: Dict[str, Any],
    ) -> None:
        lines += [
            "# HELP tokenpak_tokens_saved_total Total tokens saved by TokenPak compression",
            "# TYPE tokenpak_tokens_saved_total counter",
        ]
        if db_rows:
            # Aggregate per provider (db_rows is per provider+model)
            by_provider: Dict[str, int] = {}
            for row in db_rows:
                p = row["provider"]
                by_provider[p] = by_provider.get(p, 0) + int(row["tokens_saved"])
            for provider, saved in by_provider.items():
                labels = _label_str(provider=provider)
                lines.append(f"tokenpak_tokens_saved_total{labels} {saved}")
        else:
            labels = _label_str(provider="unknown")
            lines.append(
                f"tokenpak_tokens_saved_total{labels} {int(session['saved_tokens'])}"
            )
        lines.append("")

    def _emit_cache_entries(
        self, lines: List[str], cache: Dict[str, Any]
    ) -> None:
        lines += [
            "# HELP tokenpak_cache_entries Active entries across all TokenPak caches",
            "# TYPE tokenpak_cache_entries gauge",
            f"tokenpak_cache_entries {int(cache['entries'])}",
            "",
        ]

    def _emit_cache_memory_bytes(
        self, lines: List[str], cache: Dict[str, Any]
    ) -> None:
        lines += [
            "# HELP tokenpak_cache_memory_bytes Estimated bytes used by TokenPak caches",
            "# TYPE tokenpak_cache_memory_bytes gauge",
            f"tokenpak_cache_memory_bytes {int(cache['memory_bytes'])}",
            "",
        ]

    def _emit_cache_hit_ratio(
        self,
        lines: List[str],
        cache: Dict[str, Any],
        session: Dict[str, Any],
    ) -> None:
        lines += [
            "# HELP tokenpak_cache_hit_ratio Cache hit ratio (cache_read_tokens / total_input_tokens)",
            "# TYPE tokenpak_cache_hit_ratio gauge",
        ]
        try:
            cache_read = int(session.get("cache_read_tokens", 0))
            total_input = int(session.get("input_tokens", 0))
            ratio = round(cache_read / total_input, 4) if total_input > 0 else 0.0
        except Exception:
            ratio = 0.0
        lines += [f"tokenpak_cache_hit_ratio {_fmt(ratio)}", ""]

    def _emit_latency_histogram(
        self, lines: List[str], latency: Dict[str, Dict[str, Any]]
    ) -> None:
        lines += [
            "# HELP tokenpak_proxy_latency_ms Proxy request latency in milliseconds",
            "# TYPE tokenpak_proxy_latency_ms histogram",
        ]
        for provider, data in latency.items():
            for le, count in data["buckets"].items():
                le_str = "+Inf" if le == float("inf") else str(int(le))
                raw_labels = (
                    f'provider="{_escape_label_value(provider)}",le="{le_str}"'
                )
                lines.append(
                    f"tokenpak_proxy_latency_ms_bucket{{{raw_labels}}} {count}"
                )
            labels = _label_str(provider=provider)
            lines.append(
                f"tokenpak_proxy_latency_ms_sum{labels} {_fmt(data['sum_ms'])}"
            )
            lines.append(
                f"tokenpak_proxy_latency_ms_count{labels} {data['count']}"
            )
        lines.append("")

    def _emit_up(self, lines: List[str], is_up: int) -> None:
        lines += [
            "# HELP tokenpak_up 1 if the TokenPak proxy is up and healthy, 0 otherwise",
            "# TYPE tokenpak_up gauge",
            f"tokenpak_up {is_up}",
            "",
        ]
