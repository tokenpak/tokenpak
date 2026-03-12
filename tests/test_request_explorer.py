import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tokenpak.request_explorer import (
    cache_pct,
    age_label,
    load_requests,
    get_request_by_id,
    status_label,
    to_view,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_load_requests_skips_bad_json(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text("{bad json}\n" + json.dumps({"id": "req1"}) + "\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "req1"


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": "req1"}, {"id": "req2"}])
    row = get_request_by_id("req2", path=path)
    assert row is not None
    assert row["id"] == "req2"


def test_cache_pct():
    view = to_view({"id": "req", "model": "m", "input_tokens": 100, "output_tokens": 0, "cache_read": 25, "saved_cost": 0.0, "status": "success", "timestamp": ""})
    assert cache_pct(view) == 25.0


def test_status_label_cached():
    view = to_view({"id": "req", "model": "m", "input_tokens": 0, "output_tokens": 0, "cache_read": 10, "saved_cost": 0.0, "status": "success", "timestamp": ""})
    assert status_label(view) == "cached"


def test_status_label_error():
    view = to_view({"id": "req", "model": "m", "input_tokens": 0, "output_tokens": 0, "cache_read": 0, "saved_cost": 0.0, "status": "error", "timestamp": ""})
    assert status_label(view) == "error"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=12)).isoformat()
    assert age_label(ts).endswith("s")
