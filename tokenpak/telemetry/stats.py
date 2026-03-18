"""TokenPak Request and Session Stats — Track compression effectiveness."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RequestStats:
    """Stats for a single request through TokenPak."""

    request_id: str
    timestamp: datetime
    input_tokens_raw: int  # What the user sent
    input_tokens_sent: int  # After compression
    tokens_saved: int
    percent_saved: float
    cost_saved: float

    @property
    def footer_oneline(self) -> str:
        """Generate single-line footer format (without session total)."""
        if self.tokens_saved == 0:
            return "⚡ TokenPak: 0 tokens saved"
        return f"⚡ TokenPak: -{self.tokens_saved:,} tokens ({self.percent_saved:.0f}%) | ${self.cost_saved:.3f} saved"

    def to_dict(self):
        """Convert to dict."""
        return {
            "request_id": self.request_id,
            "timestamp": self.timestamp.isoformat(),
            "input_tokens_raw": self.input_tokens_raw,
            "input_tokens_sent": self.input_tokens_sent,
            "tokens_saved": self.tokens_saved,
            "percent_saved": self.percent_saved,
            "cost_saved": self.cost_saved,
        }


@dataclass
class SessionStats:
    """Aggregated stats for the current session (proxy uptime)."""

    session_requests: int = 0
    session_total_tokens_raw: int = 0
    session_total_tokens_sent: int = 0
    session_total_saved: int = 0
    session_total_cost_saved: float = 0.0
    session_start_time: datetime = field(default_factory=datetime.now)

    @property
    def session_total_percent(self) -> float:
        """Overall savings percentage."""
        if self.session_total_tokens_raw == 0:
            return 0.0
        return (self.session_total_saved / self.session_total_tokens_raw) * 100

    def to_dict(self):
        """Convert to dict."""
        return {
            "session_requests": self.session_requests,
            "session_total_tokens_raw": self.session_total_tokens_raw,
            "session_total_tokens_sent": self.session_total_tokens_sent,
            "session_total_saved": self.session_total_saved,
            "session_total_cost_saved": self.session_total_cost_saved,
            "session_total_percent": self.session_total_percent,
            "session_start_time": self.session_start_time.isoformat(),
        }


class StatsStorage:
    """Track request stats and session aggregates."""

    def __init__(self, max_history: int = 100):
        self.max_history = max_history
        self.request_history: deque = deque(maxlen=max_history)
        self.session_stats = SessionStats()
        self.lock = threading.Lock()

    def add_request(
        self,
        request_id: str,
        input_tokens_raw: int,
        input_tokens_sent: int,
        cost_saved: float,
    ) -> RequestStats:
        """Record a request and update session totals."""
        tokens_saved = input_tokens_raw - input_tokens_sent
        percent_saved = (tokens_saved / input_tokens_raw * 100) if input_tokens_raw > 0 else 0.0

        stats = RequestStats(
            request_id=request_id,
            timestamp=datetime.now(),
            input_tokens_raw=input_tokens_raw,
            input_tokens_sent=input_tokens_sent,
            tokens_saved=tokens_saved,
            percent_saved=percent_saved,
            cost_saved=cost_saved,
        )

        with self.lock:
            self.request_history.append(stats)
            self.session_stats.session_requests += 1
            self.session_stats.session_total_tokens_raw += input_tokens_raw
            self.session_stats.session_total_tokens_sent += input_tokens_sent
            self.session_stats.session_total_saved += tokens_saved
            self.session_stats.session_total_cost_saved += cost_saved

        return stats

    def get_last(self) -> Optional[RequestStats]:
        """Get most recent request stats."""
        with self.lock:
            if self.request_history:
                return self.request_history[-1]
        return None

    def get_last_with_session(self) -> dict:
        """Get last request stats combined with session totals."""
        with self.lock:
            if not self.request_history:
                return {"request": None, "session": self.session_stats.to_dict()}
            return {
                "request": self.request_history[-1].to_dict(),
                "session": self.session_stats.to_dict(),
            }

    def get_session(self) -> SessionStats:
        """Get current session stats."""
        with self.lock:
            return SessionStats(
                session_requests=self.session_stats.session_requests,
                session_total_tokens_raw=self.session_stats.session_total_tokens_raw,
                session_total_tokens_sent=self.session_stats.session_total_tokens_sent,
                session_total_saved=self.session_stats.session_total_saved,
                session_total_cost_saved=self.session_stats.session_total_cost_saved,
                session_start_time=self.session_stats.session_start_time,
            )


# Global singleton
_stats_storage: Optional[StatsStorage] = None


def get_stats_storage() -> StatsStorage:
    """Get or create the global stats storage."""
    global _stats_storage
    if _stats_storage is None:
        _stats_storage = StatsStorage()
    return _stats_storage


# Mock stats for demo
def create_demo_stats() -> tuple[RequestStats, SessionStats]:
    """Create realistic demo stats."""
    request_stats = RequestStats(
        request_id="req-12345",
        timestamp=datetime.now(),
        input_tokens_raw=1715,
        input_tokens_sent=1403,
        tokens_saved=312,
        percent_saved=18.2,
        cost_saved=0.003,
    )

    session_stats = SessionStats(
        session_requests=47,
        session_total_tokens_raw=78432,
        session_total_tokens_sent=63521,
        session_total_saved=14911,
        session_total_cost_saved=1.24,
    )

    return request_stats, session_stats
