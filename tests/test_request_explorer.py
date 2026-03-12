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


def test_load_requests_skips_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text("{bad json}\n" + json.dumps({"id": "req1"}) + "\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "req1"


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": f"req{i}"} for i in range(3)]
    _write_jsonl(path, rows)
    loaded = load_requests(path=path, limit=2)
    assert [r["id"] for r in loaded] == ["req1", "req2"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": "req1"}, {"id": "req2"}]
    _write_jsonl(path, rows)
    found = get_request_by_id("req2", path=path)
    assert found is not None
    assert found["id"] == "req2"


def test_to_view_and_cache_pct():
    row = {
        "id": "req1",
        "model": "claude",
        "input_tokens": 100,
        "output_tokens": 25,
        "cache_read": 20,
        "saved_cost": 0.02,
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": "abc",
    }
    view = to_view(row)
    assert view.request_id == "req1"
    assert cache_pct(view) == 20.0


def test_status_label_cached():
    row = {
        "id": "req1",
        "model": "claude",
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read": 1,
        "saved_cost": 0.0,
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    view = to_view(row)
    assert status_label(view) == "cached"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    label = age_label(ts)
    assert label.endswith("s")
