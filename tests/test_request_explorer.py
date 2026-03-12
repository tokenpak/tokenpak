import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

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
    path.write_text("{bad json}\n" + json.dumps({"id": "a"}) + "\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "a"


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": "req1"}, {"id": "req2"}]
    _write_jsonl(path, rows)
    assert get_request_by_id("req2", path=path)["id"] == "req2"
    assert get_request_by_id("missing", path=path) is None


def test_cache_pct_and_status():
    row = {
        "id": "r",
        "model": "m",
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_read": 25,
        "saved_cost": 0.1,
        "status": "success",
        "timestamp": "2026-03-01T00:00:00Z",
    }
    view = to_view(row)
    assert cache_pct(view) == 25.0
    assert status_label(view) == "cached"


def test_status_error():
    row = {
        "id": "r",
        "model": "m",
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_read": 0,
        "saved_cost": 0.1,
        "status": "error",
        "timestamp": "2026-03-01T00:00:00Z",
    }
    view = to_view(row)
    assert status_label(view) == "error"


def test_age_label_seconds():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(seconds=10)).isoformat()
    assert age_label(ts).endswith("s")


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": f"r{i}"} for i in range(5)]
    _write_jsonl(path, rows)
    limited = load_requests(path=path, limit=2)
    assert [r["id"] for r in limited] == ["r3", "r4"]
