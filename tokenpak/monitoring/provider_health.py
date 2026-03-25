"""
tokenpak/monitoring/provider_health.py

Per-provider health metrics tracking for the TokenPak proxy.

Tracks per-provider metrics in a rolling 1-hour window:
    - Latency (p50, p99)
    - Error rate (5xx)
    - Success rate
    - Request count
    - Last-seen timestamp

Exposes JSON endpoint for dashboard consumption.

Thread-safe implementation using locks for concurrent request handling.
"""

from __future__ import annotations

import time
import threading
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional
from collections import deque
from dataclasses import dataclass, field
import statistics


@dataclass
class RequestMetric:
    """Single request metric."""
    timestamp: float  # unix timestamp
    latency_ms: float
    status_code: int
    
    @property
    def is_error(self) -> bool:
        """5xx errors."""
        return self.status_code >= 500
    
    @property
    def is_success(self) -> bool:
        """2xx and 3xx."""
        return 200 <= self.status_code < 400


@dataclass
class ProviderMetrics:
    """Aggregated metrics for a single provider."""
    provider: str
    metrics: deque = field(default_factory=lambda: deque(maxlen=10000))  # ~1hr at high volume
    lock: threading.Lock = field(default_factory=threading.Lock)
    
    WINDOW_SECONDS = 3600  # 1 hour rolling window
    
    def record_request(self, latency_ms: float, status_code: int) -> None:
        """Record a single request metric."""
        metric = RequestMetric(
            timestamp=time.time(),
            latency_ms=latency_ms,
            status_code=status_code,
        )
        with self.lock:
            self.metrics.append(metric)
    
    def _get_active_metrics(self) -> List[RequestMetric]:
        """Get metrics within the rolling window (drop old data)."""
        now = time.time()
        cutoff = now - self.WINDOW_SECONDS
        with self.lock:
            return [m for m in self.metrics if m.timestamp >= cutoff]
    
    def get_stats(self) -> Dict[str, Any]:
        """Return aggregated stats for this provider."""
        active = self._get_active_metrics()
        
        if not active:
            return {
                "provider": self.provider,
                "status": "GREEN",
                "request_count": 0,
                "success_rate": 1.0,
                "error_rate": 0.0,
                "latency_p50_ms": None,
                "latency_p99_ms": None,
                "last_seen": None,
            }
        
        latencies = [m.latency_ms for m in active]
        successes = sum(1 for m in active if m.is_success)
        errors = sum(1 for m in active if m.is_error)
        success_rate = successes / len(active) if active else 1.0
        error_rate = errors / len(active) if active else 0.0
        
        # Latency percentiles
        latencies_sorted = sorted(latencies)
        p50_idx = max(0, int(len(latencies_sorted) * 0.50) - 1)
        p99_idx = max(0, int(len(latencies_sorted) * 0.99) - 1)
        p50 = latencies_sorted[p50_idx] if latencies_sorted else None
        p99 = latencies_sorted[p99_idx] if latencies_sorted else None
        
        # Health status
        if success_rate > 0.99:
            health_status = "GREEN"
        elif success_rate > 0.95:
            health_status = "YELLOW"
        else:
            health_status = "RED"
        
        # Last seen
        last_seen = max(m.timestamp for m in active) if active else None
        last_seen_iso = (
            datetime.fromtimestamp(last_seen, tz=timezone.utc).isoformat()
            if last_seen else None
        )
        
        return {
            "provider": self.provider,
            "status": health_status,
            "request_count": len(active),
            "success_rate": round(success_rate, 4),
            "error_rate": round(error_rate, 4),
            "latency_p50_ms": round(p50, 2) if p50 else None,
            "latency_p99_ms": round(p99, 2) if p99 else None,
            "last_seen": last_seen_iso,
        }


class ProviderHealthRegistry:
    """Global registry for per-provider health metrics."""
    
    _instance: Optional[ProviderHealthRegistry] = None
    _lock: threading.Lock = threading.Lock()
    
    def __init__(self):
        self.providers: Dict[str, ProviderMetrics] = {}
        self.global_lock = threading.Lock()
    
    @classmethod
    def get_instance(cls) -> ProviderHealthRegistry:
        """Get or create singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance
    
    def record_request(
        self,
        provider: str,
        latency_ms: float,
        status_code: int,
    ) -> None:
        """Record a single request for a provider."""
        with self.global_lock:
            if provider not in self.providers:
                self.providers[provider] = ProviderMetrics(provider=provider)
            self.providers[provider].record_request(latency_ms, status_code)
    
    def get_all_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get aggregated stats for all providers."""
        with self.global_lock:
            return {
                name: metrics.get_stats()
                for name, metrics in self.providers.items()
            }
    
    def get_stats_for_provider(self, provider: str) -> Dict[str, Any]:
        """Get stats for a single provider."""
        with self.global_lock:
            if provider not in self.providers:
                return {
                    "provider": provider,
                    "status": "GREEN",
                    "request_count": 0,
                    "success_rate": 1.0,
                    "error_rate": 0.0,
                    "latency_p50_ms": None,
                    "latency_p99_ms": None,
                    "last_seen": None,
                }
            return self.providers[provider].get_stats()


def get_provider_health_registry() -> ProviderHealthRegistry:
    """Get the global provider health registry."""
    return ProviderHealthRegistry.get_instance()


def record_request(
    provider: str,
    latency_ms: float,
    status_code: int,
) -> None:
    """
    Record a request completion for a provider.
    
    Parameters
    ----------
    provider : str
        Provider name (e.g., "anthropic", "openai", "google")
    latency_ms : float
        Round-trip latency in milliseconds
    status_code : int
        HTTP response status code
    """
    registry = get_provider_health_registry()
    registry.record_request(provider, latency_ms, status_code)


def get_all_provider_stats() -> Dict[str, Dict[str, Any]]:
    """Get aggregated stats for all providers."""
    registry = get_provider_health_registry()
    return registry.get_all_stats()


def get_provider_stats(provider: str) -> Dict[str, Any]:
    """Get stats for a specific provider."""
    registry = get_provider_health_registry()
    return registry.get_stats_for_provider(provider)
