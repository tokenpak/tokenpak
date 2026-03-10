"""Anonymous Metrics Reporter — daily batch sync.

Sends pending MetricsRecords to the configured ingest endpoint in one
batched POST. Non-blocking: runs in a daemon thread. Retries up to 3 times
with exponential back-off before giving up for the day.

Endpoint: POST /v1/metrics/ingest
Payload:
    {
        "schema_version": "1.0",
        "records": [ { ...MetricsRecord fields, no local_id/synced } ]
    }

Response 2xx → records marked synced locally.
Response 4xx → data rejected; records marked synced to avoid infinite retries.
Response 5xx / network error → retry (up to 3 total attempts).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import List, Optional

from tokenpak.telemetry.anon_metrics import MetricsRecord, get_store

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

INGEST_URL = os.environ.get(
    "TOKENPAK_METRICS_URL",
    "https://api.tokenpak.dev/v1/metrics/ingest",
)
MAX_RETRIES = 3
BASE_BACKOFF_S = 2.0  # seconds; doubles each retry
BATCH_LIMIT = 500  # max records per upload


# ---------------------------------------------------------------------------
# Core sync
# ---------------------------------------------------------------------------


def _post(url: str, payload: dict, timeout: int = 15) -> int:
    """HTTP POST JSON. Returns HTTP status code."""
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", "User-Agent": "tokenpak-reporter/1.0"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status


def sync_batch(
    records: Optional[List[MetricsRecord]] = None,
    url: str = INGEST_URL,
    dry_run: bool = False,
) -> dict:
    """Sync pending records to the ingest endpoint.

    Args:
        records: explicit list (for testing). If None, loads from store.
        url: override ingest URL.
        dry_run: if True, build the payload but don't POST.

    Returns dict with keys: uploaded, skipped, errors, synced_ids.
    """
    store = get_store()
    if records is None:
        records = store.get_pending(limit=BATCH_LIMIT)

    result = {"uploaded": 0, "skipped": 0, "errors": [], "synced_ids": []}

    if not records:
        return result

    payload = {
        "schema_version": records[0].schema_version if records else "1.0",
        "sent_at": datetime.now(timezone.utc).isoformat(),
        "record_count": len(records),
        "records": [r.to_upload_dict() for r in records],
    }

    if dry_run:
        result["uploaded"] = len(records)
        result["synced_ids"] = [r.local_id for r in records]
        return result

    last_error: Optional[Exception] = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            status = _post(url, payload)
            if 200 <= status < 300 or (400 <= status < 500):
                # 2xx = success, 4xx = rejected (don't retry — mark as done to avoid loop)
                synced_ids = [r.local_id for r in records]
                store.mark_synced(synced_ids)
                result["uploaded"] = len(records)
                result["synced_ids"] = synced_ids
                if 400 <= status < 500:
                    result["errors"].append(f"Server rejected batch with HTTP {status}")  # type: ignore
                return result
            # 5xx: retry
            last_error = Exception(f"HTTP {status}")
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = exc

        if attempt < MAX_RETRIES:
            time.sleep(BASE_BACKOFF_S * (2 ** (attempt - 1)))

    result["errors"].append(f"Failed after {MAX_RETRIES} attempts: {last_error}")  # type: ignore
    result["skipped"] = len(records)
    return result


# ---------------------------------------------------------------------------
# Background / non-blocking daily runner
# ---------------------------------------------------------------------------

_lock = threading.Lock()
_last_sync_date: str = ""  # "YYYY-MM-DD" of last successful sync


def _should_sync_today() -> bool:
    global _last_sync_date
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return _last_sync_date != today


def _run_daily_sync() -> None:
    """Called from daemon thread. Performs sync if not already done today."""
    global _last_sync_date
    with _lock:
        if not _should_sync_today():
            return
        try:
            from tokenpak.agent.config import get_metrics_enabled

            if not get_metrics_enabled():
                return
        except Exception:
            return

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        try:
            result = sync_batch()
            if result["uploaded"] > 0:
                logger.info(
                    "metrics: synced %d records (errors: %s)",
                    result["uploaded"],
                    result["errors"],
                )
            _last_sync_date = today
        except Exception as exc:
            logger.warning("metrics: daily sync failed: %s", exc)


def schedule_daily_sync() -> None:
    """Spawn a daemon thread to run the daily batch sync (non-blocking)."""
    t = threading.Thread(target=_run_daily_sync, daemon=True, name="metrics-reporter")
    t.start()
