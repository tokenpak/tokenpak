"""
WS-5 Usage Metering Client — UsageMeter

Buffers token usage events locally and flushes them to the license server
on a 24h heartbeat. Designed for the modular-tree proxy pipeline.

Key behaviours:
- record()  — append event to in-memory buffer (non-blocking, no I/O)
- flush()   — POST buffered events to <license_server>/usage; on failure,
              keeps the buffer intact and persists it to disk
- On-disk fallback at ~/.tokenpak/usage_buffer.jsonl (survives restarts)
- Background thread calls flush() every 24h (starts on import via singleton)
- Graceful degradation: network errors never raise; logged at WARNING level

Integration point (tokenpak/proxy/proxy.py):
    from tokenpak._internal.license.usage_meter import get_usage_meter
    get_usage_meter().record(license_id, tokens_in, tokens_out, model)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_BUFFER_PATH = Path.home() / ".tokenpak" / "usage_buffer.jsonl"
_HEARTBEAT_INTERVAL = 86400  # 24 hours in seconds
_FLUSH_TIMEOUT = 10  # seconds per HTTP request


# ─────────────────────────────────────────────────────────────────────────────
# UsageMeter
# ─────────────────────────────────────────────────────────────────────────────


class UsageMeter:
    """
    Buffers and ships usage events to the license server.

    Thread-safe.  A single instance is typically used as a module-level
    singleton (see get_usage_meter()).
    """

    def __init__(
        self,
        license_server_url: Optional[str] = None,
        buffer_path: Optional[Path] = None,
        heartbeat_interval: float = _HEARTBEAT_INTERVAL,
        _start_heartbeat: bool = True,
    ):
        self._server_url = (
            license_server_url
            or os.environ.get("TOKENPAK_LICENSE_SERVER", "http://localhost:8900")
        )
        self._buffer_path = buffer_path or Path(
            os.environ.get(
                "TOKENPAK_USAGE_BUFFER",
                str(_DEFAULT_BUFFER_PATH),
            )
        )
        self._heartbeat_interval = heartbeat_interval
        self._lock = threading.Lock()
        self._buffer: List[dict] = []

        # Load any events that survived a previous crash
        self._load_disk_buffer()

        if _start_heartbeat:
            self._start_heartbeat_thread()

    # ── Public API ────────────────────────────────────────────────────────────

    def record(
        self,
        license_id: str,
        tokens_in: int,
        tokens_out: int,
        model: str,
        ts: Optional[str] = None,
    ) -> None:
        """
        Append a usage event to the local buffer.

        Non-blocking — does no I/O on the hot path.
        """
        if not license_id:
            return
        event = {
            "license_id": license_id,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "model": model,
            "ts": ts or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        with self._lock:
            self._buffer.append(event)

    def flush(self) -> bool:
        """
        POST buffered events to the license server.

        Returns True if all events were shipped successfully.
        On any failure, keeps the buffer intact and persists it to disk
        so the next heartbeat can retry (graceful degradation).
        """
        with self._lock:
            if not self._buffer:
                return True
            events_to_ship = list(self._buffer)

        success_count = 0
        for event in events_to_ship:
            try:
                resp = requests.post(
                    f"{self._server_url}/usage",
                    json=event,
                    timeout=_FLUSH_TIMEOUT,
                )
                if resp.status_code in (200, 201):
                    success_count += 1
                else:
                    logger.warning(
                        "usage_meter: server rejected event (status=%d body=%s)",
                        resp.status_code,
                        resp.text[:200],
                    )
            except Exception as exc:
                logger.warning("usage_meter: flush failed for event: %s", exc)

        with self._lock:
            if success_count == len(events_to_ship):
                # All shipped — clear buffer and remove disk file
                self._buffer.clear()
                self._clear_disk_buffer()
                return True
            else:
                # Partial or full failure — persist remaining events to disk
                shipped_ids = set(range(success_count))  # events shipped in order
                remaining = events_to_ship[success_count:]
                # Keep any events added to buffer during flush + remaining
                self._buffer = remaining + [
                    e for e in self._buffer if e not in events_to_ship
                ]
                self._persist_disk_buffer()
                return False

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    def _daily_heartbeat(self) -> None:
        """Background thread: flush every heartbeat_interval seconds."""
        while True:
            time.sleep(self._heartbeat_interval)
            try:
                self.flush()
            except Exception as exc:
                logger.warning("usage_meter: heartbeat flush error: %s", exc)

    def _start_heartbeat_thread(self) -> None:
        t = threading.Thread(
            target=self._daily_heartbeat,
            name="tokenpak-usage-heartbeat",
            daemon=True,
        )
        t.start()
        self._heartbeat_thread = t

    # ── Disk buffer ───────────────────────────────────────────────────────────

    def _persist_disk_buffer(self) -> None:
        """Write current buffer to disk (called under self._lock)."""
        try:
            self._buffer_path.parent.mkdir(parents=True, exist_ok=True)
            with self._buffer_path.open("w") as fh:
                for event in self._buffer:
                    fh.write(json.dumps(event) + "\n")
        except Exception as exc:
            logger.warning("usage_meter: could not persist buffer to disk: %s", exc)

    def _load_disk_buffer(self) -> None:
        """Load any persisted events from disk into the in-memory buffer."""
        if not self._buffer_path.exists():
            return
        try:
            events = []
            with self._buffer_path.open("r") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        events.append(json.loads(line))
            with self._lock:
                self._buffer.extend(events)
            logger.info(
                "usage_meter: loaded %d buffered events from disk", len(events)
            )
        except Exception as exc:
            logger.warning("usage_meter: could not load disk buffer: %s", exc)

    def _clear_disk_buffer(self) -> None:
        """Remove the on-disk buffer file after a successful flush."""
        try:
            if self._buffer_path.exists():
                self._buffer_path.unlink()
        except Exception as exc:
            logger.warning("usage_meter: could not remove disk buffer: %s", exc)

    # ── Helpers for testing ───────────────────────────────────────────────────

    def _buffer_snapshot(self) -> List[dict]:
        """Return a copy of the current buffer (for testing)."""
        with self._lock:
            return list(self._buffer)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

_instance: Optional[UsageMeter] = None
_instance_lock = threading.Lock()


def get_usage_meter() -> UsageMeter:
    """Return the module-level UsageMeter singleton (lazy-initialised)."""
    global _instance
    if _instance is None:
        with _instance_lock:
            if _instance is None:
                _instance = UsageMeter()
    return _instance
