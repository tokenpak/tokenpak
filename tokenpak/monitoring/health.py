"""
tokenpak/monitoring/health.py

Health check logic for the TokenPak proxy.

Provides:
    - Provider connectivity checks (Anthropic, with 1s timeout)
    - Cache metrics (entry count, memory usage, compression ratio)
    - Aggregate status logic: healthy | degraded | unhealthy
    - HealthChecker class consumed by the /health route
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx

from tokenpak import __version__ as _proxy_version

# ---------------------------------------------------------------------------
# Provider connectivity check
# ---------------------------------------------------------------------------

PROVIDER_ENDPOINTS: Dict[str, str] = {
    "anthropic": "https://api.anthropic.com",
}

_PROVIDER_CHECK_TIMEOUT = 1.0  # seconds


def _check_provider(name: str, url: str) -> Dict[str, Any]:
    """
    Perform a lightweight HEAD/GET against *url* to verify connectivity.

    Returns a dict with:
        status        : "ok" | "timeout" | "error"
        last_check    : ISO-8601 timestamp
        response_time_ms : int
    """
    ts = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    t0 = time.monotonic()
    try:
        resp = httpx.head(url, timeout=_PROVIDER_CHECK_TIMEOUT, follow_redirects=True)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        # Any HTTP response (even 401/403) means the network path is open
        status = "ok" if resp.status_code < 500 else "error"
    except httpx.TimeoutException:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        status = "timeout"
    except Exception:
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        status = "error"

    return {
        "status": status,
        "last_check": ts,
        "response_time_ms": elapsed_ms,
    }


def check_providers() -> Dict[str, Dict[str, Any]]:
    """Check all configured providers and return their status dicts."""
    results: Dict[str, Dict[str, Any]] = {}
    for name, url in PROVIDER_ENDPOINTS.items():
        results[name] = _check_provider(name, url)
    return results


# ---------------------------------------------------------------------------
# Cache metrics
# ---------------------------------------------------------------------------


def _estimate_dict_memory_mb(d: dict) -> float:
    """Best-effort estimate of in-memory dict size in MB via sys.getsizeof."""
    try:
        import sys as _sys

        # Rough estimate: walk the dict keys + values one level deep
        total = _sys.getsizeof(d)
        for k, v in d.items():
            total += _sys.getsizeof(k) + _sys.getsizeof(v)
        return round(total / (1024 * 1024), 3)
    except Exception:
        return 0.0


def get_cache_metrics() -> Dict[str, Any]:
    """
    Return cache metrics from the CacheRegistry.

    Includes:
        entries            : total entries across all registered caches
        memory_used_mb     : best-effort estimate
        compression_ratio  : pulled from most recent compression stats if available
    """
    try:
        from tokenpak.cache.registry import CacheRegistry

        summary = CacheRegistry.summary()
        total_entries = sum(v.get("size", 0) for v in summary.values())
        # Estimate memory by summing internal dicts for each registered cache
        total_memory_mb = 0.0
        for name in CacheRegistry.names():
            cache = CacheRegistry.get(name)
            if cache is not None and hasattr(cache, "_store"):
                total_memory_mb += _estimate_dict_memory_mb(getattr(cache, "_store", {}))
    except Exception:
        total_entries = 0
        total_memory_mb = 0.0

    # Pull latest compression ratio from compression events if available
    compression_ratio = _get_latest_compression_ratio()

    return {
        "entries": total_entries,
        "memory_used_mb": round(total_memory_mb, 2),
        "compression_ratio": compression_ratio,
    }


def _get_latest_compression_ratio() -> float:
    """
    Try to read the most recent average compression ratio from the proxy's
    rolling stats.  Returns 0.0 if unavailable (no requests yet).
    """
    try:
        from tokenpak.agent.proxy.stats import CompressionStats

        stats = CompressionStats.get_global()  # type: ignore[attr-defined]
        ratio = stats.avg_ratio() if stats else 0.0
        return round(ratio, 3)
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Status aggregation
# ---------------------------------------------------------------------------


def aggregate_status(
    provider_results: Dict[str, Dict[str, Any]],
    cache_ok: bool = True,
) -> str:
    """
    Determine overall proxy health status.

    Rules:
        healthy   — all providers ok + cache operational
        degraded  — one or more providers slow/timeout + cache ok
        unhealthy — multiple provider failures OR cache down
    """
    statuses = [v["status"] for v in provider_results.values()]
    bad = [s for s in statuses if s in ("timeout", "error")]

    if not cache_ok:
        return "unhealthy"
    if len(bad) == 0:
        return "healthy"
    if len(bad) == 1 and len(statuses) > 1:
        return "degraded"
    if len(bad) >= 2:
        return "unhealthy"
    # Single provider total failure
    if len(bad) >= 1 and statuses[0] == "error":
        return "unhealthy"
    return "degraded"


# ---------------------------------------------------------------------------
# HealthChecker — main entry point
# ---------------------------------------------------------------------------


class HealthChecker:
    """
    Assembles a full /health response payload.

    Parameters
    ----------
    start_time : float
        Unix timestamp of when the proxy process started (for uptime calc).
    version : str
        Proxy version string (defaults to tokenpak.__version__).
    """

    def __init__(
        self,
        start_time: Optional[float] = None,
        version: Optional[str] = None,
    ) -> None:
        self._start_time = start_time or time.time()
        self._version = version or _proxy_version

    def check(self) -> Dict[str, Any]:
        """
        Run all health checks and return the JSON-ready response dict.

        Always returns HTTP 200; status is embedded in the JSON body.
        """
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        uptime = int(time.time() - self._start_time)

        providers = check_providers()
        cache = get_cache_metrics()
        cache_ok = True  # cache is in-process; only false if import fails badly

        status = aggregate_status(providers, cache_ok=cache_ok)

        return {
            "status": status,
            "timestamp": timestamp,
            "uptime_seconds": uptime,
            "proxy_version": self._version,
            "providers": providers,
            "cache": cache,
        }
