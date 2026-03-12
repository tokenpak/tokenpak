"""request_ledger.py — Append-only per-request ledger for aggregation."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

REQUESTS_PATH = Path.home() / ".tokenpak" / "requests.jsonl"


def append_request(record: Dict[str, Any], path: Optional[Path] = None) -> None:
    p = path or REQUESTS_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(record)
    if not payload.get("timestamp"):
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
    with open(p, "a") as f:
        f.write(json.dumps(payload) + "\n")


__all__ = ["append_request", "REQUESTS_PATH"]
