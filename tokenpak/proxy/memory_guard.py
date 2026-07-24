"""Adaptive, opt-in memory pressure handling for the proxy process.

The guard is disabled unless ``TOKENPAK_MEMORY_GUARD`` is explicitly enabled
and both RSS thresholds are supplied.  It never invents a successful memory
reading: Linux uses ``/proc``; macOS and Windows use ``psutil`` when available;
other platforms (or missing measurement support) fail closed as unsupported.

The proxy's built-in memory-holding structures are bounded or semantically
required, so its default pressure action is best-effort Python garbage
collection plus ``malloc_trim`` where glibc exposes it. Callers may inject
explicit eviction callbacks for independently synchronized, disposable caches;
the guard reports exactly which callbacks are wired.

Active environment controls:

``TOKENPAK_MEMORY_GUARD``
    Explicit boolean; disabled by default.
``TOKENPAK_MEMORY_TARGET_MB`` / ``TOKENPAK_MEMORY_CEILING_MB``
    Required positive integers when enabled, with target strictly below ceiling.
``TOKENPAK_MEMORY_CHECK_SECS``
    Positive sampling cadence (default 30 seconds).
``TOKENPAK_MEMORY_COOLDOWN_SECS``
    Minimum time between same-level actions (default 300 seconds and never less
    than the sampling cadence).
``TOKENPAK_MEMORY_SYS_LOW_MB``
    Optional non-negative host-available-memory trigger; zero disables it.

``calculate_budget`` remains a planning helper only.  Its host-RAM estimate is
not consumed by the proxy lifecycle and does not activate the guard.
"""

from __future__ import annotations

import ctypes
import gc
import logging
import math
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping

logger = logging.getLogger("tokenpak.memory_guard")

__all__ = [
    "MemoryGuard",
    "calculate_budget",
    "create_memory_guard",
    "get_available_ram_mb",
    "get_rss_mb",
    "get_total_ram_mb",
    "malloc_trim",
]

_MIB = 1024 * 1024
_FALSE_VALUES = {"", "0", "false", "no", "off"}
_TRUE_VALUES = {"1", "true", "yes", "on"}
_MEMORY_ENV_PREFIX = "TOKENPAK_MEMORY_"
_MEMORY_ENV_NAMES = frozenset(
    {
        "TOKENPAK_MEMORY_GUARD",
        "TOKENPAK_MEMORY_MODE",
        "TOKENPAK_MEMORY_TARGET_MB",
        "TOKENPAK_MEMORY_CEILING_MB",
        "TOKENPAK_MEMORY_CHECK_SECS",
        "TOKENPAK_MEMORY_COOLDOWN_SECS",
        "TOKENPAK_MEMORY_SYS_LOW_MB",
    }
)
_CONFIG_STATUS_LOCK = threading.Lock()
_LAST_CONFIG_STATUS: dict[str, Any] = {
    "source": "default",
    "mode": "off",
    "plan_sha256": None,
    "managed_config_path": None,
    "managed_file_present": False,
    "managed_file_ignored": False,
    "triggering_env": [],
    "warning": None,
}


class MemoryMeasurementUnsupported(RuntimeError):
    """Raised when trustworthy RSS/available-memory measurement is unavailable."""


def _load_psutil() -> Any | None:
    try:
        import psutil

        return psutil
    except (ImportError, OSError):
        return None


def memory_measurement_support() -> dict[str, Any]:
    """Return a non-secret description of this platform's measurement support."""
    if sys.platform.startswith("linux"):
        required = (Path("/proc/meminfo"), Path("/proc/self/status"))
        missing = [str(path) for path in required if not path.is_file()]
        return {
            "supported": not missing,
            "platform": "linux",
            "source": "procfs",
            "reason": (
                None if not missing else f"missing required procfs files: {', '.join(missing)}"
            ),
        }

    if sys.platform == "darwin" or sys.platform.startswith("win"):
        platform_name = "macos" if sys.platform == "darwin" else "windows"
        supported = _load_psutil() is not None
        return {
            "supported": supported,
            "platform": platform_name,
            "source": "psutil" if supported else None,
            "reason": None if supported else "psutil is required for memory measurement",
        }

    return {
        "supported": False,
        "platform": sys.platform,
        "source": None,
        "reason": f"unsupported platform: {sys.platform}",
    }


def _require_measurement_support() -> dict[str, Any]:
    support = memory_measurement_support()
    if not support["supported"]:
        raise MemoryMeasurementUnsupported(str(support["reason"]))
    return support


def _read_proc_kib(path: str, field: str) -> int:
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if line.startswith(field):
                    parts = line.split()
                    if len(parts) < 2:
                        break
                    value = int(parts[1])
                    if value < 0:
                        break
                    return value
    except (OSError, ValueError) as exc:
        raise MemoryMeasurementUnsupported(
            f"cannot read {field.rstrip(':')} from {path}: {exc}"
        ) from exc
    raise MemoryMeasurementUnsupported(f"missing or invalid {field.rstrip(':')} in {path}")


def get_total_ram_mb() -> int:
    """Return total host RAM in binary MiB without a fabricated fallback."""
    support = _require_measurement_support()
    if support["source"] == "procfs":
        return _read_proc_kib("/proc/meminfo", "MemTotal:") // 1024
    psutil = _load_psutil()
    if psutil is None:  # pragma: no cover - support probe already guards this
        raise MemoryMeasurementUnsupported("psutil became unavailable")
    try:
        return int(psutil.virtual_memory().total // _MIB)
    except Exception as exc:
        raise MemoryMeasurementUnsupported(f"psutil total-memory read failed: {exc}") from exc


def get_available_ram_mb() -> int:
    """Return host-available RAM in binary MiB without a fabricated fallback."""
    support = _require_measurement_support()
    if support["source"] == "procfs":
        return _read_proc_kib("/proc/meminfo", "MemAvailable:") // 1024
    psutil = _load_psutil()
    if psutil is None:  # pragma: no cover - support probe already guards this
        raise MemoryMeasurementUnsupported("psutil became unavailable")
    try:
        return int(psutil.virtual_memory().available // _MIB)
    except Exception as exc:
        raise MemoryMeasurementUnsupported(f"psutil available-memory read failed: {exc}") from exc


def get_rss_mb() -> int:
    """Return current-process RSS in binary MiB without a false-green zero."""
    support = _require_measurement_support()
    if support["source"] == "procfs":
        return _read_proc_kib("/proc/self/status", "VmRSS:") // 1024
    psutil = _load_psutil()
    if psutil is None:  # pragma: no cover - support probe already guards this
        raise MemoryMeasurementUnsupported("psutil became unavailable")
    try:
        return int(psutil.Process().memory_info().rss // _MIB)
    except Exception as exc:
        raise MemoryMeasurementUnsupported(f"psutil RSS read failed: {exc}") from exc


def malloc_trim() -> bool:
    """Ask glibc to return free arenas to the OS; false means unavailable."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        libc = ctypes.CDLL("libc.so.6")
        return bool(libc.malloc_trim(0) == 1)
    except (AttributeError, OSError):
        return False


def calculate_budget(
    proxy_share: float = 0.35,
    budget_max_mb: int = 2048,
    total_ram_mb: int | None = None,
) -> dict[str, int | str]:
    """Return a host-RAM planning estimate; the live factory never consumes it."""
    if not 0 < proxy_share <= 1:
        raise ValueError("proxy_share must be greater than 0 and at most 1")
    if budget_max_mb <= 0:
        raise ValueError("budget_max_mb must be positive")
    if total_ram_mb is None:
        total_ram_mb = get_total_ram_mb()
    if total_ram_mb <= 0:
        raise ValueError("total_ram_mb must be positive")

    budget = min(int(total_ram_mb * proxy_share), budget_max_mb)
    target = int(budget * 0.75)
    ceiling = int(budget * 0.95)
    return {
        "mode": "planning_only",
        "total_ram_mb": total_ram_mb,
        "budget_mb": budget,
        "target_mb": target,
        "ceiling_mb": ceiling,
        "sys_low_mb": max(200, int(total_ram_mb * 0.08)),
    }


class MemoryGuard:
    """Own one pressure-monitor thread with explicit thresholds and lifecycle."""

    def __init__(
        self,
        *,
        target_mb: int,
        ceiling_mb: int,
        sys_low_mb: int = 0,
        check_interval_secs: float = 30,
        cooldown_secs: float = 300,
        action_mode: str = "auto",
        configuration: Mapping[str, Any] | None = None,
        on_evict_compact_cache: Callable[[int], int] | None = None,
        on_evict_token_cache: Callable[[int], int] | None = None,
        on_evict_semantic_cache: Callable[[], int] | None = None,
    ) -> None:
        if action_mode not in {"observe", "auto"}:
            raise ValueError("action_mode must be 'observe' or 'auto'")
        if not isinstance(target_mb, int) or isinstance(target_mb, bool) or target_mb <= 0:
            raise ValueError("target_mb must be positive")
        if (
            not isinstance(ceiling_mb, int)
            or isinstance(ceiling_mb, bool)
            or ceiling_mb <= target_mb
        ):
            raise ValueError("ceiling_mb must be greater than target_mb")
        if not isinstance(sys_low_mb, int) or isinstance(sys_low_mb, bool) or sys_low_mb < 0:
            raise ValueError("sys_low_mb must be non-negative")
        try:
            check_interval = float(check_interval_secs)
            cooldown = float(cooldown_secs)
        except (TypeError, ValueError) as exc:
            raise ValueError("check interval and cooldown must be numeric") from exc
        if not math.isfinite(check_interval) or check_interval <= 0:
            raise ValueError("check_interval_secs must be positive")
        if not math.isfinite(cooldown) or cooldown < check_interval:
            raise ValueError("cooldown_secs must be at least check_interval_secs")

        self.target_mb = int(target_mb)
        self.ceiling_mb = int(ceiling_mb)
        self.sys_low_mb = int(sys_low_mb)
        self.check_interval = check_interval
        self.cooldown_secs = cooldown
        self.action_mode = action_mode
        self.configuration = dict(configuration or {})
        gap = self.ceiling_mb - self.target_mb
        self.hysteresis_mb = min(64, max(1, gap // 4), max(0, self.target_mb - 1))

        self._evict_compact_cache = on_evict_compact_cache
        self._evict_token_cache = on_evict_token_cache
        self._evict_semantic_cache = on_evict_semantic_cache
        callback_count = sum(
            callback is not None
            for callback in (
                on_evict_compact_cache,
                on_evict_token_cache,
                on_evict_semantic_cache,
            )
        )

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._stop_call_lock = threading.Lock()
        self._stopping = False
        self._stats_lock = threading.Lock()
        self._last_action_monotonic: float | None = None
        self._last_action_level: str | None = None
        self._stats: dict[str, Any] = {
            "state": "created",
            "checks": 0,
            "measurement_errors": 0,
            "gc_runs": 0,
            "trim_runs": 0,
            "yellow_triggers": 0,
            "red_triggers": 0,
            "sys_low_triggers": 0,
            "suppressed_actions": 0,
            "observed_pressure_checks": 0,
            "compact_evictions": 0,
            "token_evictions": 0,
            "semantic_evictions": 0,
            "peak_rss_mb": 0,
            "last_rss_mb": None,
            "last_sys_avail_mb": None,
            "last_level": "UNKNOWN",
            "last_reclaimed_mb": 0,
            "total_reclaimed_mb": 0,
            "pressure_latched": False,
            "last_error": None,
            "measurement": memory_measurement_support(),
            "callback_policy": (
                "caller_supplied_eviction_callbacks"
                if callback_count
                else "gc_trim_only_no_unbounded_disposable_proxy_cache"
            ),
        }

    def start(self) -> bool:
        """Start exactly one monitor thread; return false when already running."""
        with self._lifecycle_lock:
            if self._stopping:
                raise RuntimeError("MemoryGuard stop is still in progress")
            if self._thread is not None and self._thread.is_alive():
                return False

            support = memory_measurement_support()
            with self._stats_lock:
                self._stats["measurement"] = dict(support)
            if not support["supported"]:
                with self._stats_lock:
                    self._stats["state"] = "unsupported"
                    self._stats["last_error"] = support["reason"]
                raise MemoryMeasurementUnsupported(str(support["reason"]))

            try:
                get_rss_mb()
                get_available_ram_mb()
            except MemoryMeasurementUnsupported as exc:
                with self._stats_lock:
                    self._stats["state"] = "unsupported"
                    self._stats["last_error"] = str(exc)
                    self._stats["measurement"] = {
                        **support,
                        "supported": False,
                        "reason": str(exc),
                    }
                raise

            self._stop_event.clear()
            thread = threading.Thread(
                target=self._run_loop,
                name="tokenpak-memory-guard",
                daemon=True,
            )
            self._thread = thread
            with self._stats_lock:
                self._stats["state"] = "running"
                self._stats["last_error"] = None
            try:
                thread.start()
            except Exception:
                self._thread = None
                with self._stats_lock:
                    self._stats["state"] = "start_failed"
                raise

        logger.info(
            "MemoryGuard started: target=%dMiB ceiling=%dMiB hysteresis=%dMiB "
            "sys_low=%dMiB interval=%.3fs cooldown=%.3fs",
            self.target_mb,
            self.ceiling_mb,
            self.hysteresis_mb,
            self.sys_low_mb,
            self.check_interval,
            self.cooldown_secs,
        )
        return True

    def stop(self, timeout: float = 5.0) -> bool:
        """Stop and join the monitor, retaining ownership if the join times out."""
        if not math.isfinite(timeout) or timeout < 0:
            raise ValueError("timeout must be non-negative")
        with self._stop_call_lock:
            with self._lifecycle_lock:
                thread = self._thread
                if thread is None:
                    self._stopping = False
                    with self._stats_lock:
                        if self._stats["state"] not in {"unsupported", "start_failed"}:
                            self._stats["state"] = "stopped"
                    return True

                self._stopping = True
                self._stop_event.set()

            thread.join(timeout=timeout)
            with self._lifecycle_lock:
                if thread.is_alive():
                    message = f"MemoryGuard thread did not stop within {timeout:.3f}s"
                    with self._stats_lock:
                        self._stats["state"] = "stop_timeout"
                        self._stats["last_error"] = message
                    # Keep both the owned handle and the stopping state. A later
                    # stop call must reap it before start can create a successor.
                    raise RuntimeError(message)

                if self._thread is thread:
                    self._thread = None
                self._stopping = False
                with self._stats_lock:
                    self._stats["state"] = "stopped"
                    self._stats["last_error"] = None
                return True

    @property
    def stats(self) -> dict[str, Any]:
        with self._lifecycle_lock:
            thread = self._thread
            thread_alive = bool(thread and thread.is_alive())
            thread_ident = thread.ident if thread is not None and thread_alive else None
            stopping = self._stopping
            with self._stats_lock:
                snapshot = dict(self._stats)
        snapshot["thread_alive"] = thread_alive
        snapshot["thread_ident"] = thread_ident
        snapshot["stopping"] = stopping
        snapshot["enabled"] = True
        snapshot["measurement"] = dict(snapshot["measurement"])
        snapshot["config"] = {
            "action_mode": self.action_mode,
            "target_mb": self.target_mb,
            "ceiling_mb": self.ceiling_mb,
            "hysteresis_mb": self.hysteresis_mb,
            "sys_low_mb": self.sys_low_mb,
            "check_interval_secs": self.check_interval,
            "cooldown_secs": self.cooldown_secs,
        }
        snapshot["configuration"] = dict(self.configuration)
        snapshot["callbacks"] = {
            "compact": self._evict_compact_cache is not None,
            "token": self._evict_token_cache is not None,
            "semantic": self._evict_semantic_cache is not None,
        }
        return snapshot

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._check()
            except Exception as exc:
                with self._stats_lock:
                    self._stats["measurement_errors"] += 1
                    self._stats["state"] = "degraded"
                    self._stats["last_error"] = str(exc)
                    self._stats["measurement"] = {
                        **self._stats["measurement"],
                        "supported": False,
                        "reason": str(exc),
                    }
                logger.error("MemoryGuard check error: %s", exc)
            self._stop_event.wait(self.check_interval)

    def _check(self, *, now: float | None = None) -> None:
        rss = get_rss_mb()
        sys_avail = get_available_ram_mb()
        sample_time = time.monotonic() if now is None else now
        support = memory_measurement_support()

        if rss >= self.ceiling_mb:
            level = "RED"
            sys_triggered = False
        elif rss >= self.target_mb or (self.sys_low_mb > 0 and sys_avail < self.sys_low_mb):
            level = "YELLOW"
            sys_triggered = self.sys_low_mb > 0 and sys_avail < self.sys_low_mb
        else:
            level = "GREEN"
            sys_triggered = False

        with self._stats_lock:
            self._stats["checks"] += 1
            self._stats["last_rss_mb"] = rss
            self._stats["last_sys_avail_mb"] = sys_avail
            self._stats["peak_rss_mb"] = max(self._stats["peak_rss_mb"], rss)
            self._stats["measurement"] = dict(support)
            if self._stats["state"] == "degraded":
                self._stats["state"] = "running"
                self._stats["last_error"] = None

            if level == "GREEN":
                sys_recovered = (
                    self.sys_low_mb == 0 or sys_avail >= self.sys_low_mb + self.hysteresis_mb
                )
                fully_recovered = rss <= self.target_mb - self.hysteresis_mb and sys_recovered
                if self._stats["pressure_latched"] and not fully_recovered:
                    self._stats["last_level"] = "RECOVERY"
                else:
                    self._stats["last_level"] = "GREEN"
                if fully_recovered:
                    self._stats["pressure_latched"] = False
                return

            escalation = self._last_action_level == "YELLOW" and level == "RED"
            in_cooldown = (
                self._last_action_monotonic is not None
                and sample_time - self._last_action_monotonic < self.cooldown_secs
            )
            self._stats["last_level"] = level
            self._stats["pressure_latched"] = True
            if self.action_mode == "observe":
                self._stats["observed_pressure_checks"] += 1
                return
            if in_cooldown and not escalation:
                self._stats["suppressed_actions"] += 1
                return
            self._last_action_monotonic = sample_time
            self._last_action_level = level

        if level == "RED":
            self._action_red(rss, sys_avail)
        else:
            self._action_yellow(rss, sys_avail, sys_triggered=sys_triggered)

    def _action_yellow(self, rss: int, sys_avail: int, *, sys_triggered: bool) -> None:
        with self._stats_lock:
            self._stats["yellow_triggers"] += 1
            if sys_triggered:
                self._stats["sys_low_triggers"] += 1
        self._do_gc_trim()
        self._evict_caches(compact_pct=25, token_pct=25)
        self._record_reclaimed(rss)
        logger.info("MemoryGuard YELLOW: rss=%dMiB available=%dMiB", rss, sys_avail)

    def _action_red(self, rss: int, sys_avail: int) -> None:
        with self._stats_lock:
            self._stats["red_triggers"] += 1
        self._evict_caches(compact_pct=50, token_pct=75)
        self._do_gc_trim()
        self._do_gc_trim()
        self._record_reclaimed(rss)
        logger.warning("MemoryGuard RED: rss=%dMiB available=%dMiB", rss, sys_avail)

    def _record_reclaimed(self, before_rss: int) -> None:
        after_rss = get_rss_mb()
        reclaimed = max(0, before_rss - after_rss)
        with self._stats_lock:
            self._stats["last_reclaimed_mb"] = reclaimed
            self._stats["total_reclaimed_mb"] += reclaimed

    def _do_gc_trim(self) -> None:
        gc.collect()
        trimmed = malloc_trim()
        with self._stats_lock:
            self._stats["gc_runs"] += 1
            if trimmed:
                self._stats["trim_runs"] += 1

    def _evict_caches(self, *, compact_pct: int, token_pct: int) -> None:
        callbacks: tuple[tuple[str, Callable[..., int] | None, tuple[Any, ...]], ...] = (
            ("compact_evictions", self._evict_compact_cache, (compact_pct,)),
            ("token_evictions", self._evict_token_cache, (token_pct,)),
            ("semantic_evictions", self._evict_semantic_cache, ()),
        )
        for counter, callback, args in callbacks:
            if callback is None:
                continue
            try:
                evicted = int(callback(*args))
                if evicted < 0:
                    raise ValueError("eviction callback returned a negative count")
                with self._stats_lock:
                    self._stats[counter] += evicted
            except Exception as exc:
                logger.error("MemoryGuard %s callback error: %s", counter, exc)


def _parse_enabled(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in _FALSE_VALUES:
        return False
    if normalized in _TRUE_VALUES:
        return True
    raise ValueError("TOKENPAK_MEMORY_GUARD must be an explicit boolean")


def _required_positive_int(name: str) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        raise ValueError(f"{name} is required when TOKENPAK_MEMORY_GUARD is enabled")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def _number_from_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be numeric") from exc


def _set_configuration_status(**updates: Any) -> None:
    with _CONFIG_STATUS_LOCK:
        _LAST_CONFIG_STATUS.clear()
        _LAST_CONFIG_STATUS.update(
            {
                "source": "default",
                "mode": "off",
                "plan_sha256": None,
                "managed_config_path": None,
                "managed_file_present": False,
                "managed_file_ignored": False,
                "triggering_env": [],
                "warning": None,
                **updates,
            }
        )


def memory_guard_configuration_status() -> dict[str, Any]:
    """Return the startup-time configuration source and fail-safe warning."""
    with _CONFIG_STATUS_LOCK:
        snapshot = dict(_LAST_CONFIG_STATUS)
    snapshot["triggering_env"] = list(snapshot.get("triggering_env", []))
    return snapshot


def _present_memory_env() -> list[str]:
    return sorted(name for name in os.environ if name.startswith(_MEMORY_ENV_PREFIX))


def _validate_memory_env(names: list[str]) -> None:
    unknown = sorted(set(names) - _MEMORY_ENV_NAMES)
    if unknown:
        raise ValueError(f"unknown TOKENPAK_MEMORY_* variable(s): {', '.join(unknown)}")
    empty = [name for name in names if not os.environ[name].strip()]
    if empty:
        raise ValueError(f"empty TOKENPAK_MEMORY_* value(s): {', '.join(empty)}")
    if "TOKENPAK_MEMORY_GUARD" in names:
        _parse_enabled(os.environ["TOKENPAK_MEMORY_GUARD"])
    if "TOKENPAK_MEMORY_MODE" in names:
        mode = os.environ["TOKENPAK_MEMORY_MODE"].strip().lower()
        if mode not in {"observe", "auto"}:
            raise ValueError("TOKENPAK_MEMORY_MODE must be 'observe' or 'auto'")

    for name in ("TOKENPAK_MEMORY_TARGET_MB", "TOKENPAK_MEMORY_CEILING_MB"):
        if name in names:
            try:
                value = int(os.environ[name])
            except ValueError as exc:
                raise ValueError(f"{name} must be an integer") from exc
            if value <= 0:
                raise ValueError(f"{name} must be positive")
    if "TOKENPAK_MEMORY_SYS_LOW_MB" in names:
        try:
            sys_low = int(os.environ["TOKENPAK_MEMORY_SYS_LOW_MB"])
        except ValueError as exc:
            raise ValueError("TOKENPAK_MEMORY_SYS_LOW_MB must be an integer") from exc
        if sys_low < 0:
            raise ValueError("TOKENPAK_MEMORY_SYS_LOW_MB must be non-negative")
    for name in ("TOKENPAK_MEMORY_CHECK_SECS", "TOKENPAK_MEMORY_COOLDOWN_SECS"):
        if name in names:
            interval = _number_from_env(name, 0)
            if not math.isfinite(interval) or interval <= 0:
                raise ValueError(f"{name} must be positive and finite")


def _guard_from_environment(names: list[str], *, managed_path: Path) -> MemoryGuard | None:
    enabled = _parse_enabled(os.environ.get("TOKENPAK_MEMORY_GUARD", "0"))
    mode = os.environ.get("TOKENPAK_MEMORY_MODE", "auto").strip().lower()
    status = {
        "source": "environment",
        "mode": mode if enabled else "off",
        "managed_config_path": str(managed_path),
        "managed_file_present": managed_path.exists(),
        "managed_file_ignored": managed_path.exists(),
        "triggering_env": names,
    }
    if not enabled:
        _set_configuration_status(**status)
        return None

    target = _required_positive_int("TOKENPAK_MEMORY_TARGET_MB")
    ceiling = _required_positive_int("TOKENPAK_MEMORY_CEILING_MB")
    interval = _number_from_env("TOKENPAK_MEMORY_CHECK_SECS", 30.0)
    cooldown = _number_from_env("TOKENPAK_MEMORY_COOLDOWN_SECS", 300.0)
    sys_low = int(os.environ.get("TOKENPAK_MEMORY_SYS_LOW_MB", "0"))
    configuration = {**status, "plan_sha256": None, "warning": None}
    _set_configuration_status(**configuration)
    return MemoryGuard(
        target_mb=target,
        ceiling_mb=ceiling,
        sys_low_mb=sys_low,
        check_interval_secs=interval,
        cooldown_secs=cooldown,
        action_mode=mode,
        configuration=configuration,
    )


def create_memory_guard() -> MemoryGuard | None:
    """Create a guard from one exclusive source: env, managed plan, or off."""
    from tokenpak.services.memory_optimization import (
        CorruptManagedConfigError,
        load_managed_plan,
        managed_paths,
    )

    managed_path = managed_paths().config
    present_env = _present_memory_env()
    if present_env:
        _validate_memory_env(present_env)
        return _guard_from_environment(present_env, managed_path=managed_path)

    if not managed_path.exists():
        _set_configuration_status(
            source="default",
            mode="off",
            managed_config_path=str(managed_path),
        )
        return None

    try:
        plan, plan_hash = load_managed_plan(managed_path)
    except CorruptManagedConfigError as exc:
        warning = f"managed MemoryGuard config ignored: {exc}"
        logger.warning(warning)
        _set_configuration_status(
            source="managed_error",
            mode="off",
            managed_config_path=str(managed_path),
            managed_file_present=True,
            warning=warning,
        )
        return None

    guard_config = plan["memory_guard"]
    configuration = {
        "source": "managed",
        "mode": plan["mode"],
        "plan_sha256": plan_hash,
        "managed_config_path": str(managed_path),
        "managed_file_present": True,
        "managed_file_ignored": False,
        "triggering_env": [],
        "warning": None,
    }
    _set_configuration_status(**configuration)
    if not guard_config["enabled"]:
        return None
    return MemoryGuard(
        target_mb=guard_config["target_mb"],
        ceiling_mb=guard_config["ceiling_mb"],
        sys_low_mb=guard_config["sys_low_mb"],
        check_interval_secs=guard_config["check_interval_secs"],
        cooldown_secs=guard_config["cooldown_secs"],
        action_mode=guard_config["mode"],
        configuration=configuration,
    )
