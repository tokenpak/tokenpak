"""
tokenpak.proxy.stats — Real-time metrics collector for the TokenPak proxy.

Provides a thread-safe StatsCollector class that tracks:
  - Uptime and request throughput
  - Compression ratios (tokens before/after)
  - Model routing breakdown
  - Error rates by type
  - Vault search cache hit/miss rates
  - Latest request latency

Usage (in proxy.py):
    from tokenpak.proxy.stats import STATS

    STATS.record_request(model="anthropic/claude-3-5-sonnet",
                         tokens_in=1000, tokens_out=400,
                         compressed=True, latency_ms=120)
    STATS.record_error("AUTH_001")
    STATS.record_vault_search(hit=True)

    payload = STATS.snapshot()   # → dict matching /stats JSON schema
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional


class StatsCollector:
    """Thread-safe metrics collector."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time: float = time.monotonic()

        # Request counters
        self._requests_total: int = 0
        self._compressed_total: int = 0
        self._skipped_total: int = 0

        # Token counters
        self._tokens_before: int = 0  # raw input tokens (before compression)
        self._tokens_after: int = 0  # tokens actually sent upstream

        # Routing breakdown  { "anthropic/claude-3-5-sonnet": 12, ... }
        self._routing: Dict[str, int] = {}

        # Error counts  { "AUTH_001": 3, ... }
        self._errors: Dict[str, int] = {}

        # Vault search cache
        self._vault_hits: int = 0
        self._vault_misses: int = 0

        # Latest request latency (ms)
        self._latest_latency_ms: Optional[float] = None

    # ------------------------------------------------------------------
    # Recording helpers
    # ------------------------------------------------------------------

    def record_request(
        self,
        model: str = "unknown",
        tokens_in: int = 0,
        tokens_out: int = 0,
        compressed: bool = False,
        tokens_saved: int = 0,
        latency_ms: float = 0.0,
    ) -> None:
        """Record a completed proxy request."""
        with self._lock:
            self._requests_total += 1
            raw_in = tokens_in + tokens_saved  # tokens_in = sent; raw = sent + saved
            self._tokens_before += raw_in
            self._tokens_after += tokens_in
            if compressed:
                self._compressed_total += 1
            else:
                self._skipped_total += 1
            # Routing breakdown — normalise key
            key = self._normalise_model(model)
            self._routing[key] = self._routing.get(key, 0) + 1
            self._latest_latency_ms = latency_ms

    def record_error(self, error_code: str) -> None:
        """Record a proxy error by code (e.g. 'AUTH_001')."""
        with self._lock:
            self._errors[error_code] = self._errors.get(error_code, 0) + 1

    def record_vault_search(self, hit: bool) -> None:
        """Record a vault search cache event."""
        with self._lock:
            if hit:
                self._vault_hits += 1
            else:
                self._vault_misses += 1

    def reset(self) -> None:
        """Reset all counters (start_time included)."""
        with self._lock:
            self._start_time = time.monotonic()
            self._requests_total = 0
            self._compressed_total = 0
            self._skipped_total = 0
            self._tokens_before = 0
            self._tokens_after = 0
            self._routing = {}
            self._errors = {}
            self._vault_hits = 0
            self._vault_misses = 0
            self._latest_latency_ms = None

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a JSON-serialisable metrics snapshot."""
        with self._lock:
            uptime = time.monotonic() - self._start_time
            rps = self._requests_total / max(uptime, 1.0)

            before = self._tokens_before
            after = self._tokens_after
            ratio = round(after / before, 4) if before > 0 else 0.0

            total_errors = sum(self._errors.values())

            total_searches = self._vault_hits + self._vault_misses
            hit_rate = round(self._vault_hits / total_searches, 4) if total_searches > 0 else 0.0

            return {
                "uptime_seconds": round(uptime, 2),
                "requests_total": self._requests_total,
                "requests_per_sec": round(rps, 4),
                "compression": {
                    "tokens_before": before,
                    "tokens_after": after,
                    "ratio": ratio,
                    "compressed": self._compressed_total,
                    "skipped": self._skipped_total,
                },
                "routing": dict(self._routing),
                "errors": {
                    **dict(self._errors),
                    "total": total_errors,
                },
                "vault_search": {
                    "cache_hits": self._vault_hits,
                    "cache_misses": self._vault_misses,
                    "hit_rate": hit_rate,
                },
                "latest_request_ms": self._latest_latency_ms,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_model(model: str) -> str:
        """Map raw model strings to canonical routing bucket names."""
        m = model.lower()
        if "claude" in m or "anthropic" in m:
            return "anthropic_claude"
        if "gemini" in m or "google" in m:
            return "google_gemini"
        if "gpt" in m or "openai" in m or "o1" in m or "o3" in m:
            return "openai"
        if "ollama" in m or "llama" in m or "mistral" in m:
            return "ollama"
        return model  # keep unknown as-is


# ---------------------------------------------------------------------------
# Module-level singleton — import and use directly
# ---------------------------------------------------------------------------
STATS = StatsCollector()

# Aliases for compatibility
StatsCollector.to_dict = StatsCollector.snapshot


def to_text(self: "StatsCollector") -> str:
    """Return plaintext representation suitable for grep/bash scripting."""
    d = self.snapshot()
    c = d["compression"]
    v = d["vault_search"]
    lines = [
        f"uptime_seconds={d['uptime_seconds']}",
        f"requests_total={d['requests_total']}",
        f"requests_per_sec={d['requests_per_sec']}",
        f"compression_ratio={c['ratio']}",
        f"tokens_before={c['tokens_before']}",
        f"tokens_after={c['tokens_after']}",
        f"compressed={c['compressed']}",
        f"skipped={c['skipped']}",
        f"errors_total={d['errors']['total']}",
        f"vault_hit_rate={v['hit_rate']}",
        f"vault_hits={v['cache_hits']}",
        f"vault_misses={v['cache_misses']}",
        f"latest_request_ms={d['latest_request_ms']}",
        f"timestamp={d['timestamp']}",
    ]
    for provider, count in d["routing"].items():
        lines.append(f"routing_{provider}={count}")
    for code, count in d["errors"].items():
        if code != "total":
            lines.append(f"error_{code}={count}")
    return "\n".join(lines)


StatsCollector.to_text = to_text  # type: ignore[method-assign]

# ---- Singleton helpers ----
_SINGLETON_LOCK = threading.Lock()
_SINGLETON: Optional[StatsCollector] = None


def get_stats_collector() -> StatsCollector:
    """Return the process-global StatsCollector (same as STATS)."""
    global _SINGLETON
    if _SINGLETON is None:
        with _SINGLETON_LOCK:
            if _SINGLETON is None:
                _SINGLETON = STATS
    return _SINGLETON


def reset_stats_collector() -> None:
    """Replace the global singleton with a fresh instance (for testing)."""
    global _SINGLETON, STATS
    with _SINGLETON_LOCK:
        _SINGLETON = StatsCollector()
        # Update module-level STATS as well
        import sys

        sys.modules[__name__].STATS = _SINGLETON


# ---------------------------------------------------------------------------
# HTTP Endpoint Response Builders
#
# Pure functions that assemble the /health and /stats JSON response dicts.
# All required state is passed in as arguments so these are testable without
# touching module-level globals in runtime/proxy.py.
# ---------------------------------------------------------------------------



def build_health_response(
    *,
    session: dict,
    compilation_mode: str,
    vault_info: dict,
    router_info: dict,
    router_enabled: bool,
    capsule_available: bool,
    canon_available: bool,
    skeleton_enabled: bool,
    shadow_enabled: bool,
    budget_total_tokens: int,
    tool_registry_stats: dict,
    tool_registry_available: bool,
    term_resolver_enabled: bool,
    term_resolver_available: bool,
    term_resolver_top_k: int,
    term_resolver_max_bytes: int,
    strict_validation: bool,
    upstream_timeout: int,
    provider_circuits: dict,
    request_latencies: list,
) -> dict:
    """
    Assemble the /health endpoint response dict.

    All inputs are passed explicitly so this function is pure and testable.

    Args:
        session: Session-level counters dict.
        compilation_mode: Active compilation mode string.
        vault_info: Dict with ``available``, ``blocks``, ``path`` keys.
        router_info: Dict returned by the router health helper.
        router_enabled: Whether request routing is enabled.
        capsule_available: Whether the capsule builder is initialised.
        canon_available: Whether canon resolution is active.
        skeleton_enabled: Whether skeleton mode is active.
        shadow_enabled: Whether shadow reader is active.
        budget_total_tokens: Configured token budget ceiling.
        tool_registry_stats: Stats dict from ToolSchemaRegistry (may be empty).
        tool_registry_available: Whether the tool registry is available.
        term_resolver_enabled: Whether term resolver middleware is active.
        term_resolver_available: Whether a TermResolver instance exists.
        term_resolver_top_k: Configured top-k for term resolution.
        term_resolver_max_bytes: Configured max bytes per term card.
        strict_validation: Whether strict request validation is on.
        upstream_timeout: Upstream timeout in seconds.
        provider_circuits: Dict of circuit-breaker state per provider.
        request_latencies: Sorted list of recent request latency values (ms).

    Returns:
        JSON-serialisable dict suitable for ``self._send_json()``.
    """
    lats = sorted(request_latencies)
    latency_info = {
        "p50_latency_ms": lats[int(len(lats) * 0.50)] if lats else 0,
        "p99_latency_ms": lats[int(len(lats) * 0.99)] if lats else 0,
        "samples": len(lats),
    }

    return {
        "status": "ok",
        "compilation_mode": compilation_mode,
        "vault_index": vault_info,
        "router": {"enabled": router_enabled, **router_info},
        "capsule_available": capsule_available,
        "canon": {
            "enabled": canon_available,
            "session_hits": session.get("canon_hits", 0),
        },
        "skeleton": {"enabled": skeleton_enabled},
        "shadow_reader": {"enabled": shadow_enabled},
        "budget": {"enabled": True, "total_tokens": budget_total_tokens},
        "tool_schema_registry": {
            "enabled": tool_registry_available,
            **(tool_registry_stats if tool_registry_available else {}),
        },
        "term_resolver": {
            "enabled": term_resolver_enabled,
            "available": term_resolver_available,
            "top_k": term_resolver_top_k,
            "max_bytes_per_card": term_resolver_max_bytes,
        },
        "cache_poison_removal": {"enabled": True},
        "strict_validation": {"enabled": strict_validation},
        "upstream_timeout_seconds": upstream_timeout,
        "circuit_breakers": {
            p: {"open": cb["open"], "failures": cb["failures"]}
            for p, cb in provider_circuits.items()
        },
        "stats": {
            "requests": session.get("requests", 0),
            "input_tokens": session.get("input_tokens", 0),
            "sent_input_tokens": session.get("sent_input_tokens", 0),
            "saved_tokens": session.get("saved_tokens", 0),
            "errors": session.get("errors", 0),
            "cache_hits": session.get("cache_hits", 0),
            "cache_misses": session.get("cache_misses", 0),
            "cost": session.get("cost", 0),
        },
        "latency": latency_info,
    }


def build_stats_response(
    *,
    session: dict,
    compilation_mode: str,
    vault_info: dict,
    router_enabled: bool,
    capsule_available: bool,
    compression_timeouts: int,
    max_compression_time_ms: int,
    canon_available: bool,
    skeleton_enabled: bool,
    shadow_enabled: bool,
    budget_total_tokens: int,
    monitor_today: Any,
    monitor_by_model: Any,
    monitor_recent: Any,
) -> dict:
    """
    Assemble the /stats endpoint response dict.

    All inputs are passed explicitly so this function is pure and testable.

    Args:
        session: Full session-level counters dict.
        compilation_mode: Active compilation mode string.
        vault_info: Dict with ``available``, ``blocks``, ``last_timing_ms`` keys.
        router_enabled: Whether request routing is enabled.
        capsule_available: Whether the capsule builder is initialised.
        compression_timeouts: Count of compression timeouts this session.
        max_compression_time_ms: Configured compression timeout ceiling (ms).
        canon_available: Whether canon resolution is active.
        skeleton_enabled: Whether skeleton mode is active.
        shadow_enabled: Whether shadow reader is active.
        budget_total_tokens: Configured token budget ceiling.
        monitor_today: Today's stats from the Monitor instance.
        monitor_by_model: Per-model stats from the Monitor instance.
        monitor_recent: Recent request list from the Monitor instance.

    Returns:
        JSON-serialisable dict suitable for ``self._send_json()``.
    """
    return {
        "session": session,
        "compilation_mode": compilation_mode,
        "vault_index": vault_info,
        "router": {"enabled": router_enabled},
        "capsule_available": capsule_available,
        "compression_timeouts": compression_timeouts,
        "max_compression_time_ms": max_compression_time_ms,
        "canon": {
            "enabled": canon_available,
            "session_hits": session.get("canon_hits", 0),
            "tokens_saved": session.get("canon_tokens_saved", 0),
        },
        "skeleton": {"enabled": skeleton_enabled},
        "shadow_reader": {"enabled": shadow_enabled},
        "budget": {"enabled": True, "total_tokens": budget_total_tokens},
        "today": monitor_today,
        "by_model": monitor_by_model,
        "recent": monitor_recent,
    }


# ===========================================================================
# CompressionStats (merged from agent.proxy.stats — FIN-07)
# ===========================================================================

import json as _json
import os as _os
from collections import deque as _deque
from pathlib import Path as _Path

_DEFAULT_LOG_DIR = _os.path.expanduser("~/.tokenpak")
_DEFAULT_LOG_FILENAME = "compression_events.jsonl"
_DEFAULT_LOG_PATH = _os.path.join(_DEFAULT_LOG_DIR, _DEFAULT_LOG_FILENAME)
_MAX_LOG_BYTES = 10 * 1024 * 1024  # 10 MB rotation threshold
_ROLLING_WINDOW = 100  # events kept in memory for fast stats


class CompressionStats:
    """
    Thread-safe compression telemetry recorder.

    Records per-request compression events to a rotating JSONL file and
    maintains an in-memory rolling window for fast aggregation.
    """

    def __init__(
        self,
        log_path: Optional[str] = None,
        start_time: Optional[float] = None,
    ):
        self._log_path = _Path(log_path or _DEFAULT_LOG_PATH)
        self._lock = threading.Lock()
        self._recent: _deque = _deque(maxlen=_ROLLING_WINDOW)
        self._total_requests: int = 0
        self._total_errors: int = 0
        self._start_time: float = start_time if start_time is not None else time.time()

    def record_compression(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        ratio: float,
        latency_ms: int,
        status: str = "ok",
    ) -> Dict[str, Any]:
        """Record one compression event."""
        event: Dict[str, Any] = {
            "ts": datetime.now(tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "ratio": round(float(ratio), 4),
            "latency_ms": int(latency_ms),
            "status": status,
        }
        with self._lock:
            self._recent.append(event)
            self._total_requests += 1
            if status != "ok":
                self._total_errors += 1
            self._write_event(event)
        return event

    def get_stats(self) -> Dict[str, Any]:
        """Return aggregated stats over the rolling window."""
        with self._lock:
            events = list(self._recent)
            total = self._total_requests
            errors = self._total_errors

        ok_events = [e for e in events if e.get("status") == "ok"]
        avg_ratio = (
            round(sum(e["ratio"] for e in ok_events) / len(ok_events), 4) if ok_events else 0.0
        )
        avg_latency = int(sum(e["latency_ms"] for e in events) / len(events)) if events else 0
        uptime_s = int(time.time() - self._start_time)
        return {
            "requests_total": total,
            "requests_errors": errors,
            "avg_ratio": avg_ratio,
            "avg_latency_ms": avg_latency,
            "uptime_seconds": uptime_s,
            "window_size": len(events),
        }

    def read_events(self, limit: int = _ROLLING_WINDOW):
        """Read the last *limit* events from the JSONL log file on disk."""
        if not self._log_path.exists():
            return []
        try:
            with self._log_path.open("r", encoding="utf-8") as fh:
                lines = fh.readlines()
        except OSError:
            return []
        events = []
        for line in reversed(lines[-limit * 2 :]):
            line = line.strip()
            if not line:
                continue
            try:
                events.append(_json.loads(line))
            except _json.JSONDecodeError:
                continue
            if len(events) >= limit:
                break
        events.reverse()
        return events

    def stats_from_file(self, limit: int = _ROLLING_WINDOW) -> Dict[str, Any]:
        """Compute stats from on-disk JSONL (no in-memory state)."""
        events = self.read_events(limit=limit)
        total = len(events)
        errors = sum(1 for e in events if e.get("status") != "ok")
        ok_events = [e for e in events if e.get("status") == "ok"]
        avg_ratio = (
            round(sum(e.get("ratio", 0) for e in ok_events) / len(ok_events), 4)
            if ok_events
            else 0.0
        )
        avg_latency = int(sum(e.get("latency_ms", 0) for e in events) / total) if total else 0
        return {
            "requests_total": total,
            "requests_errors": errors,
            "avg_ratio": avg_ratio,
            "avg_latency_ms": avg_latency,
            "uptime_seconds": None,
            "window_size": total,
        }

    def flush_shutdown_record(self, record: Dict[str, Any]) -> None:
        """Append a shutdown record to the telemetry JSONL file."""
        with self._lock:
            self._write_event(record)

    def _write_event(self, event: Dict[str, Any]) -> None:
        """Append event to JSONL file; rotate if exceeds threshold."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            if self._log_path.exists() and self._log_path.stat().st_size >= _MAX_LOG_BYTES:
                rotated = self._log_path.with_suffix(".jsonl.1")
                self._log_path.rename(rotated)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(_json.dumps(event) + "\n")
        except OSError:
            pass


_compression_singleton: Optional[CompressionStats] = None
_compression_singleton_lock = threading.Lock()


def get_compression_stats(log_path: Optional[str] = None) -> CompressionStats:
    """Return the module-level singleton CompressionStats instance."""
    global _compression_singleton
    with _compression_singleton_lock:
        if _compression_singleton is None:
            _compression_singleton = CompressionStats(log_path=log_path)
        return _compression_singleton


def reset_compression_singleton() -> None:
    """Reset the module-level singleton (for testing)."""
    global _compression_singleton
    with _compression_singleton_lock:
        _compression_singleton = None
