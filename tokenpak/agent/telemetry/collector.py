"""TokenPak Agent Telemetry Collector — in-memory stats collection for the proxy."""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class RequestStats:
    """Stats for a single request through the TokenPak proxy."""

    request_id: str
    timestamp: datetime
    input_tokens_raw: int
    input_tokens_sent: int
    tokens_saved: int
    percent_saved: float
    cost_saved: float
    # Failover tracking: list of providers tried (empty = no failover)
    failover_chain: List[str] = field(default_factory=list)
    # Original provider before failover (None = no failover)
    original_provider: Optional[str] = None
    # Final provider used (None = use first in chain or default)
    final_provider: Optional[str] = None

    @property
    def failover_indicator(self) -> Optional[str]:
        """Generate failover indicator string if failover occurred."""
        if not self.failover_chain or len(self.failover_chain) <= 1:
            return None
        chain_str = "→".join(self.failover_chain)
        return f"⚠️ failover:{chain_str}"

    @property
    def footer_oneline(self) -> str:
        base = ""
        if self.tokens_saved == 0:
            base = "⚡ TokenPak: 0 tokens saved"
        else:
            base = (
                f"⚡ TokenPak: -{self.tokens_saved:,} tokens "
                f"({self.percent_saved:.0f}%) | ${self.cost_saved:.3f} saved"
            )
        # Append failover indicator if present
        indicator = self.failover_indicator
        if indicator:
            base = f"{base} | {indicator}"
        return base

    def to_dict(self) -> dict:
        d = {
            "request_id": self.request_id,
            "timestamp": self.timestamp.isoformat(),
            "input_tokens_raw": self.input_tokens_raw,
            "input_tokens_sent": self.input_tokens_sent,
            "tokens_saved": self.tokens_saved,
            "percent_saved": self.percent_saved,
            "cost_saved": self.cost_saved,
        }
        if self.failover_chain:
            d["failover_chain"] = self.failover_chain
            d["original_provider"] = self.original_provider
            d["final_provider"] = self.final_provider
            d["failover_indicator"] = self.failover_indicator
        return d


@dataclass
class SessionStats:
    """Aggregated stats across all requests since the proxy started."""

    session_requests: int = 0
    session_total_tokens_raw: int = 0
    session_total_tokens_sent: int = 0
    session_total_saved: int = 0
    session_total_cost_saved: float = 0.0
    session_start_time: datetime = field(default_factory=datetime.now)

    @property
    def session_total_percent(self) -> float:
        if self.session_total_tokens_raw == 0:
            return 0.0
        return (self.session_total_saved / self.session_total_tokens_raw) * 100

    def to_dict(self) -> dict:
        return {
            "session_requests": self.session_requests,
            "session_total_tokens_raw": self.session_total_tokens_raw,
            "session_total_tokens_sent": self.session_total_tokens_sent,
            "session_total_saved": self.session_total_saved,
            "session_total_cost_saved": self.session_total_cost_saved,
            "session_total_percent": self.session_total_percent,
            "session_start_time": self.session_start_time.isoformat(),
        }


class TelemetryCollector:
    """Thread-safe, in-memory stats collector for the TokenPak proxy."""

    def __init__(self, max_history: int = 500):
        self._max_history = max_history
        self._history: deque = deque(maxlen=max_history)
        self._session = SessionStats()
        self._lock = threading.Lock()

    def record(
        self,
        request_id: str,
        input_tokens_raw: int,
        input_tokens_sent: int,
        cost_saved: float = 0.0,
    ) -> RequestStats:
        """Record a completed proxy request and return its stats."""
        tokens_saved = max(0, input_tokens_raw - input_tokens_sent)
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

        with self._lock:
            self._history.append(stats)
            self._session.session_requests += 1
            self._session.session_total_tokens_raw += input_tokens_raw
            self._session.session_total_tokens_sent += input_tokens_sent
            self._session.session_total_saved += tokens_saved
            self._session.session_total_cost_saved += cost_saved

        return stats

    def get_last(self) -> Optional[RequestStats]:
        with self._lock:
            return self._history[-1] if self._history else None

    def get_session(self) -> SessionStats:
        with self._lock:
            return SessionStats(
                session_requests=self._session.session_requests,
                session_total_tokens_raw=self._session.session_total_tokens_raw,
                session_total_tokens_sent=self._session.session_total_tokens_sent,
                session_total_saved=self._session.session_total_saved,
                session_total_cost_saved=self._session.session_total_cost_saved,
                session_start_time=self._session.session_start_time,
            )

    def get_history(self, limit: int = 10) -> list:
        with self._lock:
            items = list(self._history)
        return items[-limit:]

    def reset_session(self) -> None:
        with self._lock:
            self._history.clear()
            self._session = SessionStats()

    @staticmethod
    def create_demo_stats() -> tuple:
        req = RequestStats(
            request_id="req-demo-001",
            timestamp=datetime.now(),
            input_tokens_raw=1715,
            input_tokens_sent=1403,
            tokens_saved=312,
            percent_saved=18.2,
            cost_saved=0.003,
        )
        sess = SessionStats(
            session_requests=47,
            session_total_tokens_raw=78432,
            session_total_tokens_sent=63521,
            session_total_saved=14911,
            session_total_cost_saved=1.24,
        )
        return req, sess


_collector: Optional[TelemetryCollector] = None


def get_collector() -> TelemetryCollector:
    global _collector
    if _collector is None:
        _collector = TelemetryCollector()
    return _collector
