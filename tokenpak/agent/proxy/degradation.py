"""
TokenPak Proxy Degradation Tracker

Thread-safe tracker for graceful degradation events:
  - Compression failures (request passed through uncompressed)
  - Provider failovers (primary failed, fallback used)
  - Config fallbacks (invalid config defaulted)

Accessible via:
  - GET /degradation  (proxy endpoint)
  - `tokenpak status` CLI
  - DegradationTracker singleton (module-level)
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List

# ---------------------------------------------------------------------------
# Event types
# ---------------------------------------------------------------------------


class DegradationEventType:
    COMPRESSION_FAILURE = "compression_failure"  # Hook raised, original forwarded
    PROVIDER_FAILOVER = "provider_failover"  # Primary failed, fallback used
    CONFIG_FALLBACK = "config_fallback"  # Bad config, defaults applied
    STARTUP_WARNING = "startup_warning"  # Non-fatal startup issue


# ---------------------------------------------------------------------------
# Degradation event
# ---------------------------------------------------------------------------


@dataclass
class DegradationEvent:
    timestamp: str
    event_type: str
    detail: str
    recovered: bool = True  # True = user still got a response (graceful)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "detail": self.detail,
            "recovered": self.recovered,
        }


# ---------------------------------------------------------------------------
# Tracker
# ---------------------------------------------------------------------------


class DegradationTracker:
    """
    Thread-safe, bounded in-memory log of degradation events.

    Usage::

        from tokenpak.agent.proxy.degradation import get_degradation_tracker
        tracker = get_degradation_tracker()
        tracker.record("compression_failure", "CompressionError: …", recovered=True)
    """

    _MAX_EVENTS = 50
    # Events within this window count as "currently degraded"
    _DEGRADED_WINDOW_SECONDS = 600  # 10 minutes

    def __init__(self) -> None:
        self._events: deque = deque(maxlen=self._MAX_EVENTS)
        self._lock = threading.Lock()
        # Counters (never reset, lifetime totals)
        self._compression_failures: int = 0
        self._provider_failovers: int = 0

    # ------------------------------------------------------------------
    # Recording
    # ------------------------------------------------------------------

    def record(
        self,
        event_type: str,
        detail: str,
        recovered: bool = True,
    ) -> None:
        """Record a degradation event."""
        ts = datetime.now(timezone.utc).isoformat()
        event = DegradationEvent(
            timestamp=ts,
            event_type=event_type,
            detail=detail,
            recovered=recovered,
        )
        with self._lock:
            self._events.append(event)
            if event_type == DegradationEventType.COMPRESSION_FAILURE:
                self._compression_failures += 1
            elif event_type == DegradationEventType.PROVIDER_FAILOVER:
                self._provider_failovers += 1

    def record_compression_failure(self, exc: Exception) -> None:
        """Shortcut: record a compression/hook failure."""
        self.record(
            DegradationEventType.COMPRESSION_FAILURE,
            f"{type(exc).__name__}: {exc}",
            recovered=True,
        )

    def record_provider_failover(self, from_provider: str, to_provider: str, reason: str) -> None:
        """Shortcut: record a provider failover."""
        self.record(
            DegradationEventType.PROVIDER_FAILOVER,
            f"{from_provider} → {to_provider}: {reason}",
            recovered=True,
        )

    def record_config_fallback(self, detail: str) -> None:
        """Shortcut: record a config fallback."""
        self.record(DegradationEventType.CONFIG_FALLBACK, detail, recovered=True)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def is_degraded(self) -> bool:
        """True if there was a degradation event in the last 10 minutes."""
        with self._lock:
            if not self._events:
                return False
            last = self._events[-1]
        try:
            ts = datetime.fromisoformat(last.timestamp)
            age = (datetime.now(timezone.utc) - ts).total_seconds()
            return age < self._DEGRADED_WINDOW_SECONDS
        except Exception:
            return False

    def get_recent(self, limit: int = 10) -> List[Dict[str, Any]]:
        """Return the most recent events (newest first)."""
        with self._lock:
            events = list(self._events)
        return [e.to_dict() for e in reversed(events[-limit:])]

    def summary(self) -> Dict[str, Any]:
        """Return a summary dict for status display and the /degradation endpoint."""
        with self._lock:
            total_comp = self._compression_failures
            total_fo = self._provider_failovers
            recent = list(self._events)[-10:]

        is_deg = self.is_degraded()
        return {
            "is_degraded": is_deg,
            "status": "degraded" if is_deg else "healthy",
            "lifetime_compression_failures": total_comp,
            "lifetime_provider_failovers": total_fo,
            "recent_events": [e.to_dict() for e in reversed(recent)],
            "message": (
                "⚠️  Proxy is running in degraded mode — some features reduced, "
                "but requests are still being served."
                if is_deg
                else "✅  Proxy is running normally."
            ),
        }


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_tracker = DegradationTracker()


def get_degradation_tracker() -> DegradationTracker:
    """Return the global degradation tracker."""
    return _tracker
