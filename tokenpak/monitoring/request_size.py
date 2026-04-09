"""
TokenPak Request Size Monitoring & Alerting

Provides tiered threshold alerts for request size bloat detection.
Helps users understand when context accumulation requires `/compact`.

Thresholds:
  - YELLOW (300 KB): Context is growing large
  - ORANGE (500 KB): Consider /compact to reduce overhead
  - RED (700 KB): Run /compact NOW to avoid slowdowns
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from threading import Lock
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


class AlertLevel(Enum):
    """Request size alert tiers."""

    YELLOW = "yellow"
    ORANGE = "orange"
    RED = "red"


@dataclass
class SizeAlert:
    """Single alert event."""

    timestamp: datetime
    level: AlertLevel
    size_bytes: int
    message: str
    session_id: Optional[str] = None


@dataclass
class RequestSizeConfig:
    """Configuration for request size monitoring."""

    enabled: bool = True
    yellow_threshold: int = 300_000  # 300 KB
    orange_threshold: int = 500_000  # 500 KB
    red_threshold: int = 700_000  # 700 KB
    track_history: bool = True
    max_history_size: int = 1000


class RequestSizeMonitor:
    """Thread-safe request size monitor with tiered alerting."""

    def __init__(self, config: Optional[RequestSizeConfig] = None):
        self.config = config or RequestSizeConfig()
        self._lock = Lock()
        self._last_level: Dict[str, AlertLevel] = {}  # session_id -> last alert level
        self._alert_history: List[SizeAlert] = []
        self._alert_counts: Dict[AlertLevel, int] = {
            AlertLevel.YELLOW: 0,
            AlertLevel.ORANGE: 0,
            AlertLevel.RED: 0,
        }

    def check_request_size(
        self,
        request_body_size: int,
        session_id: Optional[str] = None,
    ) -> Optional[SizeAlert]:
        """
        Check request size against thresholds.

        Returns alert if threshold breached (first-breach-only per session).
        Returns None if no threshold exceeded or if alert already sent for this level.

        Args:
            request_body_size: Request body size in bytes
            session_id: Optional session identifier for tracking

        Returns:
            SizeAlert if new threshold breached, None otherwise
        """
        if not self.config.enabled:
            return None

        # Determine alert level based on size
        alert_level = self._get_alert_level(request_body_size)

        if alert_level is None:
            # No threshold exceeded
            return None

        with self._lock:
            # Check if we've already alerted at this level for this session
            last_level = self._last_level.get(session_id)

            if last_level == alert_level:
                # Already alerted at this level, don't repeat
                return None

            # New alert — update tracking and record
            self._last_level[session_id] = alert_level
            self._alert_counts[alert_level] += 1

            alert = self._create_alert(alert_level, request_body_size, session_id)

            if self.config.track_history:
                self._alert_history.append(alert)
                # Trim history if it gets too large
                if len(self._alert_history) > self.config.max_history_size:
                    self._alert_history = self._alert_history[-self.config.max_history_size :]

            return alert

    def _get_alert_level(self, size_bytes: int) -> Optional[AlertLevel]:
        """Determine alert level from size."""
        if size_bytes >= self.config.red_threshold:
            return AlertLevel.RED
        elif size_bytes >= self.config.orange_threshold:
            return AlertLevel.ORANGE
        elif size_bytes >= self.config.yellow_threshold:
            return AlertLevel.YELLOW
        return None

    def _create_alert(
        self,
        level: AlertLevel,
        size_bytes: int,
        session_id: Optional[str],
    ) -> SizeAlert:
        """Create alert object with appropriate message."""
        size_kb = size_bytes / 1024

        messages = {
            AlertLevel.YELLOW: f"Context is growing large ({size_kb:.1f} KB). Monitor with `openclaw session status`.",
            AlertLevel.ORANGE: f"Large context detected ({size_kb:.1f} KB). Consider `/compact` to reduce overhead.",
            AlertLevel.RED: f"Very large context ({size_kb:.1f} KB). Run `/compact` NOW to avoid slowdowns.",
        }

        return SizeAlert(
            timestamp=datetime.now(timezone.utc),
            level=level,
            size_bytes=size_bytes,
            message=messages[level],
            session_id=session_id,
        )

    def reset_session(self, session_id: Optional[str] = None) -> None:
        """Reset alert state for a session (e.g., after /compact)."""
        with self._lock:
            if session_id in self._last_level:
                del self._last_level[session_id]

    def get_stats(self) -> Dict:
        """Get monitoring statistics."""
        with self._lock:
            return {
                "enabled": self.config.enabled,
                "thresholds": {
                    "yellow_bytes": self.config.yellow_threshold,
                    "orange_bytes": self.config.orange_threshold,
                    "red_bytes": self.config.red_threshold,
                },
                "alert_counts": {level.value: count for level, count in self._alert_counts.items()},
                "active_sessions": len(self._last_level),
                "history_size": len(self._alert_history),
            }

    def get_alert_history(self, limit: int = 50) -> List[Dict]:
        """Get recent alert history."""
        with self._lock:
            recent = self._alert_history[-limit:]
            return [
                {
                    "timestamp": alert.timestamp.isoformat(),
                    "level": alert.level.value,
                    "size_bytes": alert.size_bytes,
                    "size_kb": alert.size_bytes / 1024,
                    "message": alert.message,
                    "session_id": alert.session_id,
                }
                for alert in recent
            ]

    def to_dict(self) -> Dict:
        """Serialize to dictionary for telemetry/logging."""
        return {
            "type": "request_size_alert",
            "stats": self.get_stats(),
            "recent_alerts": self.get_alert_history(limit=10),
        }


# Singleton instance (will be initialized in proxy server)
_monitor: Optional[RequestSizeMonitor] = None
_monitor_lock = Lock()


def get_monitor(config: Optional[RequestSizeConfig] = None) -> RequestSizeMonitor:
    """Get or create the singleton monitor instance."""
    global _monitor

    if _monitor is not None:
        return _monitor

    with _monitor_lock:
        if _monitor is None:
            _monitor = RequestSizeMonitor(config)
        return _monitor


def reset_monitor() -> None:
    """Reset the singleton (for testing)."""
    global _monitor
    with _monitor_lock:
        _monitor = None
