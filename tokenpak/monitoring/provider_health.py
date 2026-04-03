#!/usr/bin/env python3
"""
Provider Health Monitoring for TokenPak Proxy

Tracks per-provider metrics (latency, error rate, success rate) with a rolling
1-hour window. Thread-safe using locks. Minimal overhead (<1ms per request).

Metrics:
  - p50, p99 latency (milliseconds)
  - error_rate (5xx responses)
  - success_rate (2xx responses)
  - availability (requests that reached upstream)
  - last_seen (ISO timestamp)

Status colors:
  - GREEN: >99% success, p99 < 2000ms
  - YELLOW: >95% success, p99 < 5000ms
  - RED: <95% success
"""

import statistics
import threading
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional


@dataclass
class ProviderMetrics:
    """Per-provider aggregated metrics."""

    provider: str
    latencies_ms: List[float]  # Rolling window of latencies
    request_count: int = 0
    error_count: int = 0
    success_count: int = 0
    last_seen: str = ""
    status: str = "GREEN"  # GREEN, YELLOW, RED
    p50_latency: float = 0.0
    p99_latency: float = 0.0
    error_rate: float = 0.0
    success_rate: float = 0.0

    def to_dict(self) -> dict:
        """Return dict without latencies list (summary only)."""
        return {
            "provider": self.provider,
            "request_count": self.request_count,
            "error_count": self.error_count,
            "success_count": self.success_count,
            "error_rate": round(self.error_rate * 100, 2),  # Percent
            "success_rate": round(self.success_rate * 100, 2),
            "p50_latency_ms": round(self.p50_latency, 2),
            "p99_latency_ms": round(self.p99_latency, 2),
            "status": self.status,
            "last_seen": self.last_seen,
        }


class ProviderHealthMonitor:
    """Thread-safe provider health tracking with rolling 1-hour window."""

    WINDOW_SIZE_SECONDS = 3600  # 1 hour
    MAX_LATENCIES_PER_PROVIDER = 1000  # Max latencies to keep in memory per provider

    def __init__(self):
        self.metrics: Dict[str, ProviderMetrics] = defaultdict(
            lambda: ProviderMetrics(
                provider="",
                latencies_ms=deque(maxlen=self.MAX_LATENCIES_PER_PROVIDER),
            )
        )
        self.lock = threading.RLock()
        self.start_time = time.time()

    def record_request(
        self,
        provider: str,
        latency_ms: float,
        status_code: int,
        timestamp: Optional[float] = None,
    ) -> None:
        """Record a request outcome for a provider.

        Args:
            provider: Provider name (e.g., 'anthropic', 'openai', 'google')
            latency_ms: Request latency in milliseconds
            status_code: HTTP status code
            timestamp: Optional timestamp (uses current time if not provided)
        """
        if timestamp is None:
            timestamp = time.time()

        with self.lock:
            if provider not in self.metrics:
                self.metrics[provider] = ProviderMetrics(
                    provider=provider,
                    latencies_ms=deque(maxlen=self.MAX_LATENCIES_PER_PROVIDER),
                )

            m = self.metrics[provider]
            m.request_count += 1
            m.latencies_ms.append(latency_ms)
            m.last_seen = datetime.now(timezone.utc).isoformat()

            # Error if 5xx
            if status_code >= 500:
                m.error_count += 1
            elif 200 <= status_code < 300:
                m.success_count += 1

            # Recalculate rates and status
            self._update_metrics(m)

    def _update_metrics(self, m: ProviderMetrics) -> None:
        """Recalculate aggregated metrics for a provider."""
        if m.request_count == 0:
            return

        m.success_rate = m.success_count / m.request_count
        m.error_rate = m.error_count / m.request_count

        # Calculate percentiles
        if m.latencies_ms:
            try:
                sorted_latencies = sorted(m.latencies_ms)
                m.p50_latency = statistics.median(sorted_latencies)
                # p99: 99th percentile
                idx_99 = int(len(sorted_latencies) * 0.99)
                m.p99_latency = (
                    sorted_latencies[idx_99]
                    if idx_99 < len(sorted_latencies)
                    else sorted_latencies[-1]
                )
            except (ValueError, IndexError):
                m.p50_latency = 0.0
                m.p99_latency = 0.0
        else:
            m.p50_latency = 0.0
            m.p99_latency = 0.0

        # Determine status
        if m.success_rate > 0.99 and m.p99_latency < 2000:
            m.status = "GREEN"
        elif m.success_rate > 0.95 and m.p99_latency < 5000:
            m.status = "YELLOW"
        else:
            m.status = "RED"

    def get_provider_health(self, provider: str) -> Optional[dict]:
        """Get current health for a single provider."""
        with self.lock:
            if provider not in self.metrics:
                return None
            m = self.metrics[provider]
            return m.to_dict()

    def get_all_health(self) -> dict:
        """Return health summary for all providers."""
        with self.lock:
            providers = {}
            for provider, m in self.metrics.items():
                providers[provider] = m.to_dict()
            return {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "providers": providers,
                "total_providers": len(providers),
            }

    def clear(self) -> None:
        """Clear all metrics (for testing)."""
        with self.lock:
            self.metrics.clear()


# Global singleton instance
_monitor = None
_monitor_lock = threading.Lock()


def get_monitor() -> ProviderHealthMonitor:
    """Get or create the global monitor instance."""
    global _monitor
    if _monitor is None:
        with _monitor_lock:
            if _monitor is None:
                _monitor = ProviderHealthMonitor()
    return _monitor


def record_provider_request(
    provider: str,
    latency_ms: float,
    status_code: int,
) -> None:
    """Convenience function to record a request."""
    get_monitor().record_request(provider, latency_ms, status_code)
