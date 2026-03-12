import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tokenpak.request_explorer import (
    age_label,
    cache_pct,
    get_request_by_id,
    load_requests,
    status_label,
    to_view,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_load_requests_ignores_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text("{bad}\n" + json.dumps({"id": "a"}) + "\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "a"


def test_get_request_by_id(tmp_path: Path):
    rows = [{"id": "a"}, {"id": "b"}]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)
    result = get_request_by_id("b", path=path)
    assert result
    assert result["id"] == "b"


def test_cache_pct_and_status():
    row = {
        "id": "r1",
        "model": "claude",
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_read": 25,
        "saved_cost": 0.01,
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    view = to_view(row)
    assert cache_pct(view) == 25.0
    assert status_label(view) == "cached"


def test_status_error():
    row = {
        "id": "r2",
        "model": "claude",
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_read": 0,
        "saved_cost": 0.0,
        "status": "error",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    view = to_view(row)
    assert status_label(view) == "error"


def test_age_label(tmp_path: Path):
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert age_label(ts).endswith("s")
