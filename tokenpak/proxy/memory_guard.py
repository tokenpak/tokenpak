"""
tokenpak.proxy.memory_guard — Adaptive internal memory pressure management.

Runs a background thread that checks the proxy's RSS and takes graduated action
to keep memory under a ceiling — WITHOUT destroying hot cache entries.

ADAPTIVE: At startup, reads system RAM from /proc/meminfo and calculates:
  - budget  = min(total_ram * proxy_share, 2048MB)
  - target  = budget * 0.75   (start mild eviction — coldest 25%)
  - ceiling = budget * 0.95   (aggressive eviction — coldest 50%)

On a 4GB machine → target ~1000MB, ceiling ~1264MB
On an 8GB machine → target ~1536MB, ceiling ~1945MB
On a 2GB machine → target ~537MB, ceiling ~680MB

Also monitors system-available memory — if other processes are competing,
the guard becomes more aggressive regardless of own RSS.

Strategy (preserves hot cache):
  1. gc.collect() + malloc_trim() — reclaim Python/glibc fragmentation
  2. Evict coldest N% of compact cache (ordered by insertion = access recency)
  3. Sweep expired semantic cache entries
  4. Evict coldest N% of token count cache (cheap to recompute on miss)

Environment overrides (all optional — auto-calculated if unset):
    TOKENPAK_MEMORY_GUARD        — 0 to disable (default: 1)
    TOKENPAK_MEMORY_TARGET_MB    — override auto-calculated target
    TOKENPAK_MEMORY_CEILING_MB   — override auto-calculated ceiling
    TOKENPAK_MEMORY_CHECK_SECS   — check interval (default: 30)
    TOKENPAK_MEMORY_PROXY_SHARE  — fraction of RAM for proxy (default: 0.35)
    TOKENPAK_MEMORY_BUDGET_MAX   — max budget cap in MB (default: 2048)
    TOKENPAK_MEMORY_SYS_LOW_MB   — system-available threshold for forced eviction (default: auto)
"""

import ctypes
import gc
import logging
import os
import threading
import time

logger = logging.getLogger("tokenpak.memory_guard")

# ---------------------------------------------------------------------------
# System introspection
# ---------------------------------------------------------------------------

def get_total_ram_mb() -> int:
    """Read total system RAM in MB from /proc/meminfo."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 4096  # safe fallback


def get_available_ram_mb() -> int:
    """Read available system RAM in MB from /proc/meminfo."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 2048  # safe fallback


def get_rss_mb() -> int:
    """Get current process RSS in MB from /proc/self/status."""
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) // 1024
    except Exception:
        pass
    return 0


def malloc_trim() -> bool:
    """Ask glibc to return freed memory to the OS. Linux-only."""
    try:
        libc = ctypes.CDLL("libc.so.6")
        result = libc.malloc_trim(0)
        return result == 1
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Adaptive budget calculation
# ---------------------------------------------------------------------------

def calculate_budget(
    proxy_share: float = 0.35,
    budget_max_mb: int = 2048,
    total_ram_mb: int | None = None,
) -> dict:
    """
    Calculate adaptive memory budget based on system RAM.

    Returns:
        {
            "total_ram_mb": 3896,
            "budget_mb": 1363,
            "target_mb": 1022,
            "ceiling_mb": 1294,
            "sys_low_mb": 300,
        }
    """
    if total_ram_mb is None:
        total_ram_mb = get_total_ram_mb()

    budget = min(int(total_ram_mb * proxy_share), budget_max_mb)
    target = int(budget * 0.75)
    ceiling = int(budget * 0.95)
    # System-available threshold: if system available drops below this,
    # trigger eviction regardless of own RSS (other processes competing)
    sys_low = max(200, int(total_ram_mb * 0.08))

    return {
        "total_ram_mb": total_ram_mb,
        "budget_mb": budget,
        "target_mb": target,
        "ceiling_mb": ceiling,
        "sys_low_mb": sys_low,
    }


# ---------------------------------------------------------------------------
# MemoryGuard
# ---------------------------------------------------------------------------

class MemoryGuard:
    """
    Background memory pressure manager for the TokenPak proxy.

    Adapts thresholds to the host machine's RAM. Monitors both own RSS
    and system-available memory.

    Levels:
        GREEN  — RSS < target, sys avail > sys_low → no action
        YELLOW — RSS >= target OR sys avail < sys_low → gc + trim + evict 25%
        RED    — RSS >= ceiling → aggressive evict 50% + double gc
    """

    def __init__(
        self,
        target_mb: int | None = None,
        ceiling_mb: int | None = None,
        sys_low_mb: int | None = None,
        check_interval_secs: int = 30,
        proxy_share: float = 0.35,
        budget_max_mb: int = 2048,
        on_evict_compact_cache=None,
        on_evict_token_cache=None,
        on_evict_semantic_cache=None,
    ):
        # Auto-calculate if not explicitly set
        budget = calculate_budget(proxy_share=proxy_share, budget_max_mb=budget_max_mb)

        self.total_ram_mb = budget["total_ram_mb"]
        self.target_mb = target_mb if target_mb is not None else budget["target_mb"]
        self.ceiling_mb = ceiling_mb if ceiling_mb is not None else budget["ceiling_mb"]
        self.sys_low_mb = sys_low_mb if sys_low_mb is not None else budget["sys_low_mb"]
        self.check_interval = check_interval_secs

        # Callbacks — set by the proxy at startup to wire into its data structures
        self._evict_compact_cache = on_evict_compact_cache
        self._evict_token_cache = on_evict_token_cache
        self._evict_semantic_cache = on_evict_semantic_cache

        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._stats = {
            "checks": 0,
            "gc_runs": 0,
            "trim_runs": 0,
            "yellow_triggers": 0,
            "red_triggers": 0,
            "sys_low_triggers": 0,
            "compact_evictions": 0,
            "token_evictions": 0,
            "semantic_evictions": 0,
            "peak_rss_mb": 0,
            "last_rss_mb": 0,
            "last_sys_avail_mb": 0,
            "last_level": "GREEN",
            "last_reclaimed_mb": 0,
            "total_reclaimed_mb": 0,
        }
        self._lock = threading.Lock()

    def start(self):
        """Start the background monitoring thread."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop, name="tokenpak-memory-guard", daemon=True
        )
        self._thread.start()
        logger.info(
            "MemoryGuard started: system=%dMB target=%dMB ceiling=%dMB sys_low=%dMB interval=%ds",
            self.total_ram_mb, self.target_mb, self.ceiling_mb, self.sys_low_mb, self.check_interval,
        )

    def stop(self):
        """Stop the background thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def stats(self) -> dict:
        with self._lock:
            s = dict(self._stats)
        s["config"] = {
            "total_ram_mb": self.total_ram_mb,
            "target_mb": self.target_mb,
            "ceiling_mb": self.ceiling_mb,
            "sys_low_mb": self.sys_low_mb,
            "check_interval_secs": self.check_interval,
        }
        return s

    # ------------------------------------------------------------------
    # Core loop
    # ------------------------------------------------------------------

    def _run_loop(self):
        while not self._stop_event.is_set():
            try:
                self._check()
            except Exception as e:
                logger.error("MemoryGuard check error: %s", e)
            self._stop_event.wait(self.check_interval)

    def _check(self):
        rss = get_rss_mb()
        sys_avail = get_available_ram_mb()
        with self._lock:
            self._stats["checks"] += 1
            self._stats["last_rss_mb"] = rss
            self._stats["last_sys_avail_mb"] = sys_avail
            if rss > self._stats["peak_rss_mb"]:
                self._stats["peak_rss_mb"] = rss

        if rss >= self.ceiling_mb:
            self._action_red(rss, sys_avail)
        elif rss >= self.target_mb or sys_avail < self.sys_low_mb:
            sys_triggered = sys_avail < self.sys_low_mb
            self._action_yellow(rss, sys_avail, sys_triggered=sys_triggered)
        else:
            with self._lock:
                self._stats["last_level"] = "GREEN"

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------

    def _action_yellow(self, rss: int, sys_avail: int, sys_triggered: bool = False):
        """Soft pressure: gc + trim + evict coldest 25% of caches."""
        reason = f"sys_avail={sys_avail}MB < {self.sys_low_mb}MB" if sys_triggered else f"RSS={rss}MB >= {self.target_mb}MB"
        logger.info("🟡 MemoryGuard YELLOW: %s", reason)
        with self._lock:
            self._stats["last_level"] = "YELLOW"
            self._stats["yellow_triggers"] += 1
            if sys_triggered:
                self._stats["sys_low_triggers"] += 1

        before = rss
        self._do_gc_trim()
        self._evict_caches(compact_pct=25, token_pct=25)
        after = get_rss_mb()

        reclaimed = max(0, before - after)
        with self._lock:
            self._stats["last_reclaimed_mb"] = reclaimed
            self._stats["total_reclaimed_mb"] += reclaimed
        logger.info("🟡 YELLOW done: %dMB → %dMB (freed %dMB, sys_avail=%dMB)", before, after, reclaimed, get_available_ram_mb())

    def _action_red(self, rss: int, sys_avail: int):
        """Hard pressure: aggressive eviction + full gc."""
        logger.warning("🔴 MemoryGuard RED: RSS=%dMB (ceiling=%dMB), sys_avail=%dMB", rss, self.ceiling_mb, sys_avail)
        with self._lock:
            self._stats["last_level"] = "RED"
            self._stats["red_triggers"] += 1

        before = rss
        self._evict_caches(compact_pct=50, token_pct=75)
        self._do_gc_trim()
        # Second pass — gc may have freed more cycles
        self._do_gc_trim()
        after = get_rss_mb()

        reclaimed = max(0, before - after)
        with self._lock:
            self._stats["last_reclaimed_mb"] = reclaimed
            self._stats["total_reclaimed_mb"] += reclaimed
        logger.warning("🔴 RED done: %dMB → %dMB (freed %dMB)", before, after, reclaimed)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _do_gc_trim(self):
        """Run garbage collection + malloc trim."""
        collected = gc.collect()
        trimmed = malloc_trim()
        with self._lock:
            self._stats["gc_runs"] += 1
            if trimmed:
                self._stats["trim_runs"] += 1
        if collected > 0:
            logger.debug("gc.collect() freed %d objects", collected)

    def _evict_caches(self, compact_pct: int = 25, token_pct: int = 25):
        """
        Evict the coldest N% of cache entries.
        Preserves the hottest entries (most recently accessed).
        """
        # Compact cache — evict oldest N%
        if self._evict_compact_cache:
            try:
                evicted = self._evict_compact_cache(compact_pct)
                with self._lock:
                    self._stats["compact_evictions"] += evicted
                if evicted > 0:
                    logger.info("  Compact cache: evicted %d coldest entries (%d%%)", evicted, compact_pct)
            except Exception as e:
                logger.error("  Compact cache eviction error: %s", e)

        # Token count cache — evict oldest N%
        if self._evict_token_cache:
            try:
                evicted = self._evict_token_cache(token_pct)
                with self._lock:
                    self._stats["token_evictions"] += evicted
                if evicted > 0:
                    logger.info("  Token cache: evicted %d entries (%d%%)", evicted, token_pct)
            except Exception as e:
                logger.error("  Token cache eviction error: %s", e)

        # Semantic cache — just sweep expired entries
        if self._evict_semantic_cache:
            try:
                evicted = self._evict_semantic_cache()
                with self._lock:
                    self._stats["semantic_evictions"] += evicted
                if evicted > 0:
                    logger.info("  Semantic cache: swept %d expired entries", evicted)
            except Exception as e:
                logger.error("  Semantic cache eviction error: %s", e)


# ---------------------------------------------------------------------------
# Factory — reads env vars, auto-calculates from system RAM if not overridden
# ---------------------------------------------------------------------------

def create_memory_guard() -> MemoryGuard | None:
    """
    Create a MemoryGuard from environment + system detection.

    If TOKENPAK_MEMORY_TARGET_MB / CEILING_MB are set, uses those.
    Otherwise auto-calculates from system RAM.

    Returns None if disabled.
    """
    enabled = os.environ.get("TOKENPAK_MEMORY_GUARD", "1")
    if enabled.lower() in ("0", "false", "no", "off"):
        return None

    proxy_share = float(os.environ.get("TOKENPAK_MEMORY_PROXY_SHARE", "0.35"))
    budget_max = int(os.environ.get("TOKENPAK_MEMORY_BUDGET_MAX", "2048"))
    interval = int(os.environ.get("TOKENPAK_MEMORY_CHECK_SECS", "30"))

    # Auto-calculate budget
    budget = calculate_budget(proxy_share=proxy_share, budget_max_mb=budget_max)

    # Allow explicit overrides (env vars trump auto-calc)
    target_env = os.environ.get("TOKENPAK_MEMORY_TARGET_MB")
    ceiling_env = os.environ.get("TOKENPAK_MEMORY_CEILING_MB")
    sys_low_env = os.environ.get("TOKENPAK_MEMORY_SYS_LOW_MB")

    target = int(target_env) if target_env else None
    ceiling = int(ceiling_env) if ceiling_env else None
    sys_low = int(sys_low_env) if sys_low_env else None

    guard = MemoryGuard(
        target_mb=target,
        ceiling_mb=ceiling,
        sys_low_mb=sys_low,
        check_interval_secs=interval,
        proxy_share=proxy_share,
        budget_max_mb=budget_max,
    )

    logger.info(
        "MemoryGuard auto-configured: system=%dMB budget=%dMB target=%dMB ceiling=%dMB sys_low=%dMB%s",
        budget["total_ram_mb"],
        budget["budget_mb"],
        guard.target_mb,
        guard.ceiling_mb,
        guard.sys_low_mb,
        " (explicit overrides active)" if (target_env or ceiling_env) else " (auto-calculated)",
    )

    return guard
