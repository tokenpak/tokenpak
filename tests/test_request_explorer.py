from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

from tokenpak.request_explorer import (
    load_requests,
    get_request_by_id,
    to_view,
    cache_pct,
    status_label,
    age_label,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_load_requests_handles_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text('{"id":"ok"}\n{bad json}\n')
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "ok"


def test_get_request_by_id(tmp_path: Path):
    rows = [{"id": "req1"}, {"id": "req2"}]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)
    assert get_request_by_id("req2", path=path)["id"] == "req2"


def test_cache_pct_calculation():
    view = to_view({"id": "r", "input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label_cached_vs_error():
    cached = to_view({"id": "r", "status": "success", "cache_read": 10})
    fresh = to_view({"id": "r", "status": "success", "cache_read": 0})
    err = to_view({"id": "r", "status": "error", "cache_read": 0})
    assert status_label(cached) == "cached"
    assert status_label(fresh) == "fresh"
    assert status_label(err) == "error"


def test_age_label_seconds():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(seconds=10)).isoformat()
    assert age_label(ts) == "10s"


def test_age_label_minutes():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(minutes=5)).isoformat()
    assert age_label(ts) == "5m"
