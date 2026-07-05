"""Install-level anonymous metrics reporter.

Sends a periodic heartbeat to metrics.tokenpak.ai/metrics every 24h when
the user has opted in (metrics.enabled = True).

Schema shipped:
    {
        "install_id":   str,   # stable UUID from ~/.tokenpak/install_id
        "version":      str,   # tokenpak version
        "os":           str,   # linux / darwin / windows
        "python":       str,   # e.g. "3.11"
        "started_at":   str,   # ISO 8601 of this heartbeat
        "requests_24h": int,   # proxy requests in last 24h (from local telemetry DB)
        "models":       list   # distinct model names used (from local telemetry DB)
    }

On send failure: payload is appended to ~/.tokenpak/metrics_buffer.jsonl and
retried on the next heartbeat. Failures NEVER propagate to the caller.

Usage (called from serve.py once at startup):
    from tokenpak.telemetry.install_reporter import schedule_install_heartbeat
    schedule_install_heartbeat()
"""

from __future__ import annotations

import json
import logging
import os
import platform
import sys
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

INGEST_URL = os.environ.get(
    "TOKENPAK_METRICS_INGEST_URL",
    "https://metrics.tokenpak.ai/metrics",
)
HEARTBEAT_INTERVAL_S = 60 * 60 * 24  # 24 hours
BUFFER_PATH = Path(os.path.expanduser("~/.tokenpak/metrics_buffer.jsonl"))
INSTALL_ID_PATH = Path(os.path.expanduser("~/.tokenpak/install_id"))


# ---------------------------------------------------------------------------
# Install ID
# ---------------------------------------------------------------------------


def _get_install_id() -> str:
    """Return a stable, randomly-generated install ID (UUID4).
    Created once on first use; stored in ~/.tokenpak/install_id.
    """
    if INSTALL_ID_PATH.exists():
        val = INSTALL_ID_PATH.read_text().strip()
        if val:
            return val
    new_id = str(uuid.uuid4())
    INSTALL_ID_PATH.parent.mkdir(parents=True, exist_ok=True)
    INSTALL_ID_PATH.write_text(new_id)
    return new_id


# ---------------------------------------------------------------------------
# Payload
# ---------------------------------------------------------------------------


def _get_version() -> str:
    try:
        from tokenpak import __version__
        return str(__version__)
    except Exception:
        return "unknown"


def _get_requests_24h() -> int:
    """Count proxy requests recorded in the local telemetry DB in the last 24h."""
    try:
        import sqlite3

        from tokenpak.core.paths import get_db_path
        db_path = get_db_path("telemetry.db")
        if not db_path.exists():
            return 0
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM tp_events WHERE ts >= ?", (cutoff,)
            ).fetchone()
            return int(row[0]) if row else 0
    except Exception:
        return 0


def _get_models_24h() -> list[str]:
    """Return distinct model names used in the last 24h."""
    try:
        import sqlite3

        from tokenpak.core.paths import get_db_path
        db_path = get_db_path("telemetry.db")
        if not db_path.exists():
            return []
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).timestamp()
        with sqlite3.connect(str(db_path)) as conn:
            rows = conn.execute(
                "SELECT DISTINCT model FROM tp_events WHERE ts >= ? AND model != ''",
                (cutoff,),
            ).fetchall()
            return [r[0] for r in rows]
    except Exception:
        return []


def _build_payload() -> dict:
    return {
        "install_id": _get_install_id(),
        "version": _get_version(),
        "os": platform.system().lower(),
        "python": f"{sys.version_info.major}.{sys.version_info.minor}",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "requests_24h": _get_requests_24h(),
        "models": _get_models_24h(),
    }


# ---------------------------------------------------------------------------
# Buffer (fallback on failure)
# ---------------------------------------------------------------------------


def _buffer_payload(payload: dict) -> None:
    """Append payload to local buffer file for retry next cycle."""
    try:
        BUFFER_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(BUFFER_PATH, "a") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception:
        pass


def _flush_buffer(url: str, timeout: int) -> None:
    """Attempt to send any buffered payloads. Remove successfully sent ones."""
    if not BUFFER_PATH.exists():
        return
    try:
        lines = BUFFER_PATH.read_text().splitlines()
        if not lines:
            return
        remaining = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
                _http_post(url, payload, timeout)
            except Exception:
                remaining.append(line)
        if remaining:
            BUFFER_PATH.write_text("\n".join(remaining) + "\n")
        else:
            BUFFER_PATH.unlink(missing_ok=True)
    except Exception:
        pass


def _http_post(url: str, payload: dict, timeout: int = 10) -> None:
    import urllib.request
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "tokenpak-install-reporter/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        if resp.status >= 400:
            raise OSError(f"HTTP {resp.status}")


# ---------------------------------------------------------------------------
# Heartbeat loop
# ---------------------------------------------------------------------------


def _heartbeat_loop(url: str, interval: int) -> None:
    """Run in a daemon thread. Never raises."""
    while True:
        try:
            from tokenpak.agent.config import get_metrics_enabled
            if not get_metrics_enabled():
                time.sleep(interval)
                continue
        except Exception:
            time.sleep(interval)
            continue

        payload = _build_payload()
        try:
            _flush_buffer(url, timeout=10)
            _http_post(url, payload, timeout=10)
            logger.debug("install metrics: heartbeat sent (install_id=%s)", payload["install_id"][:8])
        except Exception as exc:
            logger.debug("install metrics: send failed (%s) — buffered", exc)
            _buffer_payload(payload)

        time.sleep(interval)


_thread: Optional[threading.Thread] = None
_started = False
_lock = threading.Lock()


def schedule_install_heartbeat(
    url: str = INGEST_URL,
    interval: int = HEARTBEAT_INTERVAL_S,
) -> None:
    """Spawn the heartbeat daemon thread (idempotent — safe to call multiple times)."""
    global _thread, _started
    with _lock:
        if _started:
            return
        _started = True
        _thread = threading.Thread(
            target=_heartbeat_loop,
            args=(url, interval),
            daemon=True,
            name="tokenpak-install-reporter",
        )
        _thread.start()
