# SPDX-License-Identifier: Apache-2.0
"""TokenPak Prometheus metrics exposition.

Generates a Prometheus text format (0.0.4) metrics response without
requiring the ``prometheus_client`` library. Falls back to a simpler
format if needed.

Usage (from proxy handler)::

    from tokenpak.metrics.prometheus import build_metrics_text
    output = build_metrics_text(session, monitor)
    # Send output as text/plain; version=0.0.4; charset=utf-8

"""

from __future__ import annotations

import math
import sqlite3
import time
from typing import Any

__all__ = ["build_metrics_text", "PrometheusRegistry"]

# ---------------------------------------------------------------------------
# Prometheus text format helpers
# ---------------------------------------------------------------------------


def _escape_label_value(v: str) -> str:
    """Escape special chars in label values per Prometheus spec."""
    return v.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _labels(**kw: str) -> str:
    """Format a label set: {key="val", ...}"""
    if not kw:
        return ""
    parts = [f'{k}="{_escape_label_value(str(v))}"' for k, v in kw.items() if v is not None]
    return "{" + ",".join(parts) + "}"


def _counter(name: str, help_text: str, value: float, **label_kw: str) -> list[str]:
    """Emit HELP + TYPE + sample for a counter."""
    lbl = _labels(**label_kw)
    return [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} counter",
        f"{name}{lbl} {value}",
    ]


def _gauge(name: str, help_text: str, value: float, **label_kw: str) -> list[str]:
    """Emit HELP + TYPE + sample for a gauge."""
    lbl = _labels(**label_kw)
    return [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} gauge",
        f"{name}{lbl} {value}",
    ]


def _histogram_lines(
    name: str, help_text: str, buckets: list[tuple[float, int]], count: int, total: float
) -> list[str]:
    """Emit HELP + TYPE + bucket/count/sum lines for a histogram."""
    lines = [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} histogram",
    ]
    cumulative = 0
    for le, cnt in sorted(buckets, key=lambda x: x[0]):
        cumulative += cnt
        le_str = "+Inf" if math.isinf(le) else str(le)
        lines.append(f'{name}_bucket{{le="{le_str}"}} {cumulative}')
    lines.append(f"{name}_count {count}")
    lines.append(f"{name}_sum {total:.6f}")
    return lines


# ---------------------------------------------------------------------------
# Main builder
# ---------------------------------------------------------------------------


class PrometheusRegistry:
    """Collects metrics from SessionDict + Monitor and renders Prometheus text."""

    # Histogram bucket boundaries (seconds) for request duration
    DURATION_BUCKETS_S = [0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, float("inf")]

    def __init__(self, session: dict, monitor: Any = None) -> None:
        """
        Args:
            session: The proxy SESSION dict (in-process totals).
            monitor: Monitor instance with DB-backed per-request data (optional).
        """
        self._session = session
        self._monitor = monitor

    # ------------------------------------------------------------------
    # DB queries
    # ------------------------------------------------------------------

    def _query_by_model_status(self) -> list[tuple[str, str, int]]:
        """Return [(model, status_ok_or_error, count), ...] from DB."""
        if self._monitor is None:
            return []
        try:
            db_path = self._monitor.db_path
            conn = sqlite3.connect(db_path)
            rows = conn.execute("""
                SELECT model,
                       CASE WHEN status_code < 400 THEN 'success' ELSE 'error' END AS status,
                       COUNT(*)
                FROM requests
                GROUP BY model, status
            """).fetchall()
            conn.close()
            return [(r[0] or "unknown", r[1], r[2]) for r in rows]
        except Exception:
            return []

    def _query_latency_histogram(self) -> tuple[list[tuple[float, int]], int, float]:
        """Return (bucket_counts, total_count, total_seconds) from DB."""
        buckets_ms = [b * 1000 for b in self.DURATION_BUCKETS_S if not math.isinf(b)]
        if self._monitor is None:
            return [(b, 0) for b in self.DURATION_BUCKETS_S], 0, 0.0
        try:
            db_path = self._monitor.db_path
            conn = sqlite3.connect(db_path)
            rows = conn.execute(
                "SELECT latency_ms FROM requests WHERE latency_ms IS NOT NULL"
            ).fetchall()
            conn.close()
        except Exception:
            return [(b, 0) for b in self.DURATION_BUCKETS_S], 0, 0.0

        latencies_s = [r[0] / 1000.0 for r in rows if r[0] is not None]
        if not latencies_s:
            return [(b, 0) for b in self.DURATION_BUCKETS_S], 0, 0.0

        bucket_counts: list[tuple[float, int]] = []
        for le in self.DURATION_BUCKETS_S:
            cnt = sum(1 for v in latencies_s if v <= le)
            bucket_counts.append((le, cnt))

        return bucket_counts, len(latencies_s), sum(latencies_s)

    def _query_tokens_by_model(self) -> list[tuple[str, int, int, int]]:
        """Return [(model, input_tokens, output_tokens, saved_tokens), ...]."""
        if self._monitor is None:
            return []
        try:
            db_path = self._monitor.db_path
            conn = sqlite3.connect(db_path)
            rows = conn.execute("""
                SELECT model,
                       COALESCE(SUM(input_tokens), 0),
                       COALESCE(SUM(output_tokens), 0),
                       COALESCE(SUM(input_tokens - compressed_tokens), 0)
                FROM requests
                GROUP BY model
            """).fetchall()
            conn.close()
            return [(r[0] or "unknown", r[1], r[2], max(0, r[3])) for r in rows]
        except Exception:
            return []

    # ------------------------------------------------------------------
    # Renderer
    # ------------------------------------------------------------------

    def render(self) -> str:
        """Build and return full Prometheus text format output."""
        s = self._session
        lines: list[str] = []
        uptime = int(time.time() - s.get("start_time", time.time()))

        # ── tokenpak_requests_total (labeled by model + status) ────────────
        lines += [
            "# HELP tokenpak_requests_total Total requests processed",
            "# TYPE tokenpak_requests_total counter",
        ]
        model_status_rows = self._query_by_model_status()
        if model_status_rows:
            for model, status, count in model_status_rows:
                lbl = _labels(model=model, status=status)
                lines.append(f"tokenpak_requests_total{lbl} {count}")
        else:
            # Fallback: session totals without labels
            lines.append(f'tokenpak_requests_total {{status="success"}} {s.get("requests", 0)}')
        lines.append("")

        # ── tokenpak_request_duration_seconds (histogram) ──────────────────
        bucket_counts, total_count, total_seconds = self._query_latency_histogram()
        lines += _histogram_lines(
            "tokenpak_request_duration_seconds",
            "Request latency in seconds",
            bucket_counts,
            total_count,
            total_seconds,
        )
        lines.append("")

        # ── tokenpak_tokens_input_total (labeled by model) ─────────────────
        lines += [
            "# HELP tokenpak_tokens_input_total Total input tokens processed",
            "# TYPE tokenpak_tokens_input_total counter",
        ]
        token_rows = self._query_tokens_by_model()
        if token_rows:
            for model, input_tok, _, _ in token_rows:
                lines.append(
                    f'tokenpak_tokens_input_total{{model="{_escape_label_value(model)}"}} {input_tok}'
                )
        else:
            lines.append(f"tokenpak_tokens_input_total {s.get('input_tokens', 0)}")
        lines.append("")

        # ── tokenpak_tokens_output_total (labeled by model) ────────────────
        lines += [
            "# HELP tokenpak_tokens_output_total Total output tokens generated",
            "# TYPE tokenpak_tokens_output_total counter",
        ]
        if token_rows:
            for model, _, output_tok, _ in token_rows:
                lines.append(
                    f'tokenpak_tokens_output_total{{model="{_escape_label_value(model)}"}} {output_tok}'
                )
        else:
            lines.append(f"tokenpak_tokens_output_total {s.get('output_tokens', 0)}")
        lines.append("")

        # ── tokenpak_tokens_saved_total ────────────────────────────────────
        lines += [
            "# HELP tokenpak_tokens_saved_total Total input tokens saved by compression",
            "# TYPE tokenpak_tokens_saved_total counter",
            f"tokenpak_tokens_saved_total {s.get('saved_tokens', 0)}",
            "",
        ]

        # ── tokenpak_compression_ratio (gauge) ────────────────────────────
        raw = s.get("input_tokens", 0)
        sent = s.get("sent_input_tokens", raw)
        ratio = round(raw / sent, 4) if sent > 0 else 1.0
        lines += [
            "# HELP tokenpak_compression_ratio Ratio of raw input tokens to compressed tokens sent (raw/sent)",
            "# TYPE tokenpak_compression_ratio gauge",
            f"tokenpak_compression_ratio {ratio}",
            "",
        ]

        # ── tokenpak_errors_total (labeled by type) ───────────────────────
        lines += [
            "# HELP tokenpak_errors_total Total errors by type",
            "# TYPE tokenpak_errors_total counter",
            f'tokenpak_errors_total{{type="all"}} {s.get("errors", 0)}',
            "",
        ]

        # ── tokenpak_cost_usd_total ────────────────────────────────────────
        lines += [
            "# HELP tokenpak_cost_usd_total Total estimated cost in USD",
            "# TYPE tokenpak_cost_usd_total counter",
            f"tokenpak_cost_usd_total {s.get('cost', 0.0):.6f}",
            "",
        ]

        # ── tokenpak_cache_read_tokens_total ──────────────────────────────
        lines += [
            "# HELP tokenpak_cache_read_tokens_total Total Anthropic prompt cache read tokens",
            "# TYPE tokenpak_cache_read_tokens_total counter",
            f"tokenpak_cache_read_tokens_total {s.get('cache_read_tokens', 0)}",
            "",
        ]

        # ── tokenpak_uptime_seconds ────────────────────────────────────────
        lines += [
            "# HELP tokenpak_uptime_seconds Proxy process uptime in seconds",
            "# TYPE tokenpak_uptime_seconds gauge",
            f"tokenpak_uptime_seconds {uptime}",
            "",
        ]

        # ── tokenpak_vault_blocks ──────────────────────────────────────────
        vault_blocks = 0
        try:
            if hasattr(self._monitor, "_vault_blocks"):
                vault_blocks = self._monitor._vault_blocks
        except Exception:
            pass
        lines += [
            "# HELP tokenpak_vault_blocks Number of vault index blocks loaded",
            "# TYPE tokenpak_vault_blocks gauge",
            f"tokenpak_vault_blocks {vault_blocks}",
        ]

        return "\n".join(lines) + "\n"


def build_metrics_text(session: dict, monitor: Any = None, vault_blocks: int = 0) -> str:
    """Build Prometheus text format metrics.

    Args:
        session: Proxy SESSION dict.
        monitor: Monitor instance (for DB-backed labeled metrics).
        vault_blocks: Current vault index block count.

    Returns:
        Prometheus text format string (text/plain; version=0.0.4).
    """
    registry = PrometheusRegistry(session, monitor)
    if vault_blocks and hasattr(registry._monitor, "_vault_blocks") is False and monitor:
        monitor._vault_blocks = vault_blocks
    return registry.render()
