"""tokenpak.agent.proxy.stats — Compression telemetry logging.

Records per-request compression events to a rotating JSONL file and
maintains an in-memory rolling window for fast aggregation.
"""

from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_LOG_DIR = os.path.expanduser("~/.tokenpak")
DEFAULT_LOG_FILENAME = "compression_events.jsonl"
DEFAULT_LOG_PATH = os.path.join(DEFAULT_LOG_DIR, DEFAULT_LOG_FILENAME)
MAX_LOG_BYTES = 10 * 1024 * 1024  # 10 MB rotation threshold
ROLLING_WINDOW = 100  # events kept in memory for fast stats


# ---------------------------------------------------------------------------
# CompressionStats
# ---------------------------------------------------------------------------


class CompressionStats:
    """
    Thread-safe compression telemetry recorder.

    Usage::

        stats = CompressionStats()
        stats.record_compression(
            model="claude-sonnet-4-6",
            input_tokens=4200,
            output_tokens=1800,
            ratio=0.57,
            latency_ms=42,
            status="ok",
        )
        summary = stats.get_stats()
    """

    def __init__(
        self,
        log_path: Optional[str] = None,
        start_time: Optional[float] = None,
    ):
        self._log_path = Path(log_path or DEFAULT_LOG_PATH)
        self._lock = threading.Lock()
        self._recent: deque = deque(maxlen=ROLLING_WINDOW)
        self._total_requests: int = 0
        self._total_errors: int = 0
        self._start_time: float = start_time if start_time is not None else time.time()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record_compression(
        self,
        model: str,
        input_tokens: int,
        output_tokens: int,
        ratio: float,
        latency_ms: int,
        status: str = "ok",
    ) -> Dict[str, Any]:
        """
        Record one compression event.

        Parameters
        ----------
        model       : model identifier string
        input_tokens: raw input token count (before compression)
        output_tokens: response token count
        ratio       : compression ratio (tokens_saved / input_tokens), 0..1
        latency_ms  : end-to-end request latency in milliseconds
        status      : "ok" | "error"

        Returns
        -------
        The event dict that was written.
        """
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
        """
        Return aggregated stats over the rolling window (last 100 requests).

        Returns
        -------
        dict with keys: requests_total, requests_errors, avg_ratio,
                        avg_latency_ms, uptime_seconds
        """
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

    def read_events(self, limit: int = ROLLING_WINDOW) -> List[Dict[str, Any]]:
        """
        Read the last *limit* events from the JSONL log file on disk.
        Useful for CLI display when the server was restarted.
        """
        if not self._log_path.exists():
            return []
        lines: List[str] = []
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
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
            if len(events) >= limit:
                break
        events.reverse()
        return events

    def stats_from_file(self, limit: int = ROLLING_WINDOW) -> Dict[str, Any]:
        """
        Compute stats entirely from the on-disk JSONL (no in-memory state).
        Useful in CLI read-only mode (no running server).
        """
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
            "uptime_seconds": None,  # not available from file alone
            "window_size": total,
        }

    def flush_shutdown_record(self, record: Dict[str, Any]) -> None:
        """
        Append a ``event: shutdown`` record to the telemetry JSONL file.

        Called by ``ProxyServer.stop()`` during graceful shutdown to persist
        session-level stats before the process exits.
        """
        with self._lock:
            self._write_event(record)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write_event(self, event: Dict[str, Any]) -> None:
        """Append event to JSONL file; rotate if file exceeds MAX_LOG_BYTES."""
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            # Rotate if needed
            if self._log_path.exists() and self._log_path.stat().st_size >= MAX_LOG_BYTES:
                rotated = self._log_path.with_suffix(".jsonl.1")
                self._log_path.rename(rotated)
            with self._log_path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(event) + "\n")
        except OSError:
            pass  # never crash the caller due to telemetry write failure


# ---------------------------------------------------------------------------
# Module-level singleton (optional convenience)
# ---------------------------------------------------------------------------

_singleton: Optional[CompressionStats] = None
_singleton_lock = threading.Lock()


def get_compression_stats(log_path: Optional[str] = None) -> CompressionStats:
    """Return the module-level singleton CompressionStats instance."""
    global _singleton
    with _singleton_lock:
        if _singleton is None:
            _singleton = CompressionStats(log_path=log_path)
        return _singleton


def reset_singleton() -> None:
    """Reset the module-level singleton (for testing)."""
    global _singleton
    with _singleton_lock:
        _singleton = None
