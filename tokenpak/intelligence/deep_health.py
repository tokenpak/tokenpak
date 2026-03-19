"""
TokenPak Intelligence Server — Deep Health Check

Implements GET /health?deep=true — verifies all system components:
- Provider connectivity + latency (Anthropic, OpenAI)
- Database health (existence, size)
- Index staleness (cached pricing data age)
- Memory usage (/proc/meminfo or psutil)
- Disk usage (shutil.disk_usage)

Status semantics:
  ok       — component healthy
  warning  — degraded but functional (disk > 80%, index stale > 24h)
  error    — component unavailable
  503 returned if ANY check is error; 200 for ok or warning.
"""

from __future__ import annotations

import os
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class CheckResult:
    status: str  # ok | warning | error
    latency_ms: Optional[float] = None  # provider checks
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"status": self.status}
        if self.latency_ms is not None:
            d["latency_ms"] = round(self.latency_ms, 1)
        if self.error:
            d["error"] = self.error
        d.update(self.details)
        return d


@dataclass
class DeepHealthResult:
    status: str  # ok | degraded | error
    checks: Dict[str, CheckResult]
    duration_ms: float

    @property
    def http_status(self) -> int:
        """200 for ok/degraded, 503 for error."""
        return 503 if self.status == "error" else 200

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "checks": {k: v.to_dict() for k, v in self.checks.items()},
            "duration_ms": round(self.duration_ms, 1),
        }


# ---------------------------------------------------------------------------
# Individual checkers
# ---------------------------------------------------------------------------


def _check_provider(
    name: str,
    api_key_env: str,
    probe_url: str,
    api_key_header: str,
    timeout: float = 5.0,
) -> CheckResult:
    """
    Check provider connectivity by hitting a lightweight endpoint.

    Returns CheckResult with latency_ms on success.
    """
    api_key = os.environ.get(api_key_env, "")
    if not api_key:
        return CheckResult(status="error", error="api_key_not_configured")

    headers = {
        api_key_header: api_key,
        "User-Agent": "tokenpak-healthcheck/1.0",
    }
    req = urllib.request.Request(probe_url, headers=headers)

    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            # 200 or 2xx — provider is up
            if resp.status < 300:
                return CheckResult(status="ok", latency_ms=elapsed_ms)
            return CheckResult(
                status="error",
                latency_ms=elapsed_ms,
                error=f"http_{resp.status}",
            )
    except urllib.error.HTTPError as e:
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if e.code == 429:
            # Rate limited — provider is reachable but busy
            return CheckResult(
                status="warning",
                latency_ms=elapsed_ms,
                error="rate_limited",
            )
        if e.code in (401, 403):
            return CheckResult(
                status="error",
                latency_ms=elapsed_ms,
                error="auth_failed",
            )
        return CheckResult(
            status="error",
            latency_ms=elapsed_ms,
            error=f"http_{e.code}",
        )
    except urllib.error.URLError as e:
        return CheckResult(status="error", error=f"network_error: {e.reason}")
    except TimeoutError:
        return CheckResult(status="error", error="timeout")
    except Exception as e:  # pragma: no cover
        return CheckResult(status="error", error=str(e))


def check_anthropic(timeout: float = 5.0) -> CheckResult:
    """Probe Anthropic API — GET /v1/models."""
    return _check_provider(
        name="anthropic",
        api_key_env="ANTHROPIC_API_KEY",
        probe_url="https://api.anthropic.com/v1/models",
        api_key_header="x-api-key",
        timeout=timeout,
    )


def check_openai(timeout: float = 5.0) -> CheckResult:
    """Probe OpenAI API — GET /v1/models."""
    return _check_provider(
        name="openai",
        api_key_env="OPENAI_API_KEY",
        probe_url="https://api.openai.com/v1/models",
        api_key_header="Authorization",
        timeout=timeout,
    )


def check_database(db_path: Optional[str] = None) -> CheckResult:
    """
    Check database existence and report size.

    Defaults to ~/.openclaw/workspace/.tokenpak/monitor.db.
    """
    if db_path is None:
        db_path = os.path.expanduser("~/.openclaw/workspace/.tokenpak/monitor.db")

    path = Path(db_path)
    if not path.exists():
        return CheckResult(status="error", error=f"not_found: {db_path}")

    try:
        size_bytes = path.stat().st_size
        size_mb = round(size_bytes / (1024 * 1024), 2)
        return CheckResult(
            status="ok",
            details={"size_mb": size_mb, "path": str(path)},
        )
    except Exception as e:
        return CheckResult(status="error", error=str(e))


def check_index(index_path: Optional[str] = None, stale_hours: float = 24.0) -> CheckResult:
    """
    Check pricing index freshness.

    Defaults to ~/.openclaw/workspace/.tokenpak/pricing_index.json.
    Marks stale if older than stale_hours.
    """
    if index_path is None:
        index_path = os.path.expanduser("~/.openclaw/workspace/.tokenpak/pricing_index.json")

    path = Path(index_path)
    if not path.exists():
        return CheckResult(status="error", error="index_not_found")

    try:
        age_seconds = time.time() - path.stat().st_mtime
        age_hours = round(age_seconds / 3600, 2)

        if age_hours > stale_hours:
            return CheckResult(
                status="warning",
                error="stale",
                details={"age_hours": age_hours},
            )
        return CheckResult(status="ok", details={"age_hours": age_hours})
    except Exception as e:
        return CheckResult(status="error", error=str(e))


def check_memory() -> CheckResult:
    """
    Check system memory usage.

    Uses psutil if available; falls back to /proc/meminfo on Linux.
    Warns above 85%, errors above 95%.
    """
    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        percent = round(vm.percent, 1)
    except ImportError:
        try:
            mem_info: Dict[str, int] = {}
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2:
                        key = parts[0].rstrip(":")
                        mem_info[key] = int(parts[1])
            total = mem_info.get("MemTotal", 0)
            available = mem_info.get("MemAvailable", 0)
            if total == 0:
                return CheckResult(status="error", error="cannot_read_meminfo")
            used = total - available
            percent = round((used / total) * 100, 1)
        except Exception as e:
            return CheckResult(status="error", error=str(e))

    if percent >= 95:
        return CheckResult(status="error", details={"percent": percent}, error="oom_risk")
    if percent >= 85:
        return CheckResult(status="warning", details={"percent": percent})
    return CheckResult(status="ok", details={"percent": percent})


def check_disk(path: str = "/") -> CheckResult:
    """
    Check disk usage.

    Warns above 80%, errors above 95%.
    """
    try:
        usage = shutil.disk_usage(path)
        percent = round((usage.used / usage.total) * 100, 1)
        free_gb = round(usage.free / (1024**3), 1)

        if percent >= 95:
            return CheckResult(
                status="error",
                details={"percent": percent, "free_gb": free_gb},
                error="disk_full",
            )
        if percent >= 80:
            return CheckResult(
                status="warning",
                details={"percent": percent, "free_gb": free_gb},
            )
        return CheckResult(status="ok", details={"percent": percent, "free_gb": free_gb})
    except Exception as e:
        return CheckResult(status="error", error=str(e))


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


class DeepHealthChecker:
    """
    Runs all deep health checks, optionally in parallel.

    Parameters
    ----------
    db_path:
        Override database path for testing.
    index_path:
        Override index file path for testing.
    provider_timeout:
        HTTP timeout for provider probe requests (seconds).
    """

    def __init__(
        self,
        db_path: Optional[str] = None,
        index_path: Optional[str] = None,
        provider_timeout: float = 5.0,
        # Injectable overrides for testing
        _check_anthropic=None,
        _check_openai=None,
        _check_database=None,
        _check_index=None,
        _check_memory=None,
        _check_disk=None,
    ):
        self.db_path = db_path
        self.index_path = index_path
        self.provider_timeout = provider_timeout

        # Allow full override for tests
        self._fn_anthropic = _check_anthropic or check_anthropic
        self._fn_openai = _check_openai or check_openai
        self._fn_database = _check_database or check_database
        self._fn_index = _check_index or check_index
        self._fn_memory = _check_memory or check_memory
        self._fn_disk = _check_disk or check_disk

    def run(self) -> DeepHealthResult:
        """Run all checks synchronously (safe for sync and async contexts)."""
        t0 = time.perf_counter()

        checks: Dict[str, CheckResult] = {}

        # Providers (network I/O — run concurrently via threads)
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            fut_anthropic = pool.submit(self._fn_anthropic, self.provider_timeout)
            fut_openai = pool.submit(self._fn_openai, self.provider_timeout)
            fut_db = pool.submit(self._fn_database, self.db_path)
            fut_index = pool.submit(self._fn_index, self.index_path)
            # Memory + disk are instant — no need for threads
            checks["memory"] = self._fn_memory()
            checks["disk"] = self._fn_disk()

            checks["anthropic"] = fut_anthropic.result()
            checks["openai"] = fut_openai.result()
            checks["database"] = fut_db.result()
            checks["index"] = fut_index.result()

        # Determine overall status
        statuses = {c.status for c in checks.values()}
        if "error" in statuses:
            overall = "error"
        elif "warning" in statuses:
            overall = "degraded"
        else:
            overall = "ok"

        duration_ms = (time.perf_counter() - t0) * 1000
        return DeepHealthResult(
            status=overall,
            checks=checks,
            duration_ms=duration_ms,
        )


# Module-level singleton (initialised lazily)
_checker: Optional[DeepHealthChecker] = None


def get_checker(**kwargs: Any) -> DeepHealthChecker:
    """Return a shared DeepHealthChecker instance."""
    global _checker
    if _checker is None:
        _checker = DeepHealthChecker(**kwargs)
    return _checker
