"""
TokenPak Telemetry - Operational Metrics

Prometheus-compatible metrics endpoint for monitoring TokenPak health and performance.
"""

import threading
import time
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class MetricCounters:
    """Counter metrics."""

    ingest_total: int = 0
    ingest_errors_total: int = 0
    rollups_total: int = 0


@dataclass
class MetricHistogram:
    """Histogram metric (latency buckets)."""

    name: str
    buckets: Dict[float, int] = field(
        default_factory=lambda: {
            0.01: 0,
            0.05: 0,
            0.1: 0,
            0.5: 0,
            1.0: 0,
            5.0: 0,
        }
    )
    sum: float = 0.0
    count: int = 0

    def observe(self, value: float):
        """Record a value in the histogram."""
        for threshold in self.buckets:
            if value <= threshold:
                self.buckets[threshold] += 1
        self.sum += value
        self.count += 1

    @property
    def mean(self) -> float:
        return self.sum / self.count if self.count > 0 else 0.0


@dataclass
class MetricsCollector:
    """Central metrics collection."""

    counters: MetricCounters = field(default_factory=MetricCounters)
    ingest_latency: MetricHistogram = field(
        default_factory=lambda: MetricHistogram("tokenpak_ingest_latency_seconds")
    )
    rollup_duration: MetricHistogram = field(
        default_factory=lambda: MetricHistogram("tokenpak_rollup_duration_seconds")
    )
    db_size_bytes: int = 0
    events_total: int = 0
    rollup_last_run_timestamp: Optional[float] = None

    _lock = threading.Lock()

    def record_ingest(self, latency: float, success: bool = True):
        """Record an ingest event."""
        with self._lock:
            self.counters.ingest_total += 1
            if not success:
                self.counters.ingest_errors_total += 1
            else:
                self.ingest_latency.observe(latency)

    def record_rollup(self, duration: float):
        """Record a rollup job completion."""
        with self._lock:
            self.counters.rollups_total += 1
            self.rollup_duration.observe(duration)
            self.rollup_last_run_timestamp = time.time()

    def to_prometheus_format(self) -> str:
        """Generate Prometheus-compatible output."""
        output = []

        # Counter: ingest_total
        output.append("# HELP tokenpak_ingest_total Total telemetry events ingested")
        output.append("# TYPE tokenpak_ingest_total counter")
        output.append(f"tokenpak_ingest_total {self.counters.ingest_total}")
        output.append("")

        # Counter: ingest_errors_total
        output.append("# HELP tokenpak_ingest_errors_total Failed ingest attempts")
        output.append("# TYPE tokenpak_ingest_errors_total counter")
        output.append(f"tokenpak_ingest_errors_total {self.counters.ingest_errors_total}")
        output.append("")

        # Histogram: ingest_latency_seconds
        output.append("# HELP tokenpak_ingest_latency_seconds Ingest latency in seconds")
        output.append("# TYPE tokenpak_ingest_latency_seconds histogram")
        for threshold, count in sorted(self.ingest_latency.buckets.items()):
            output.append(f'tokenpak_ingest_latency_seconds_bucket{{le="{threshold}"}} {count}')
        output.append(
            f'tokenpak_ingest_latency_seconds_bucket{{le="+Inf"}} {self.ingest_latency.count}'
        )
        output.append(f"tokenpak_ingest_latency_seconds_sum {self.ingest_latency.sum:.3f}")
        output.append(f"tokenpak_ingest_latency_seconds_count {self.ingest_latency.count}")
        output.append("")

        # Gauge: db_size_bytes
        output.append("# HELP tokenpak_db_size_bytes Database size in bytes")
        output.append("# TYPE tokenpak_db_size_bytes gauge")
        output.append(f"tokenpak_db_size_bytes {self.db_size_bytes}")
        output.append("")

        # Gauge: events_total
        output.append("# HELP tokenpak_events_total Total events in database")
        output.append("# TYPE tokenpak_events_total gauge")
        output.append(f"tokenpak_events_total {self.events_total}")
        output.append("")

        # Gauge: rollups_total
        output.append("# HELP tokenpak_rollups_total Total rollup jobs completed")
        output.append("# TYPE tokenpak_rollups_total counter")
        output.append(f"tokenpak_rollups_total {self.counters.rollups_total}")
        output.append("")

        # Gauge: rollup_last_run_timestamp
        if self.rollup_last_run_timestamp:
            output.append(
                "# HELP tokenpak_rollup_last_run_timestamp Last rollup job timestamp (unix)"
            )
            output.append("# TYPE tokenpak_rollup_last_run_timestamp gauge")
            output.append(
                f"tokenpak_rollup_last_run_timestamp {int(self.rollup_last_run_timestamp)}"
            )
            output.append("")

        # Histogram: rollup_duration_seconds
        output.append("# HELP tokenpak_rollup_duration_seconds Rollup job duration in seconds")
        output.append("# TYPE tokenpak_rollup_duration_seconds histogram")
        for threshold, count in sorted(self.rollup_duration.buckets.items()):
            output.append(f'tokenpak_rollup_duration_seconds_bucket{{le="{threshold}"}} {count}')
        output.append(
            f'tokenpak_rollup_duration_seconds_bucket{{le="+Inf"}} {self.rollup_duration.count}'
        )
        output.append(f"tokenpak_rollup_duration_seconds_sum {self.rollup_duration.sum:.3f}")
        output.append(f"tokenpak_rollup_duration_seconds_count {self.rollup_duration.count}")

        return "\n".join(output)


# Global metrics instance
METRICS = MetricsCollector()
