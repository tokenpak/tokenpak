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


def test_load_requests_skips_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    with path.open("w") as f:
        f.write("{bad json}\n")
        f.write(json.dumps({"id": "r1"}) + "\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "r1"


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": f"r{i}"} for i in range(5)]
    _write_jsonl(path, rows)
    limited = load_requests(path=path, limit=2)
    assert [r["id"] for r in limited] == ["r3", "r4"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": "r1"}, {"id": "r2"}])
    found = get_request_by_id("r2", path=path)
    assert found is not None
    assert found["id"] == "r2"


def test_view_helpers():
    row = {
        "id": "r1",
        "model": "claude",
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read": 40,
        "saved_cost": 0.05,
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": "abc",
    }
    view = to_view(row)
    assert cache_pct(view) == 40.0
    assert status_label(view) == "cached"


def test_status_label_error():
    row = {"id": "r1", "status": "error", "timestamp": datetime.now(timezone.utc).isoformat()}
    view = to_view(row)
    assert status_label(view) == "error"


def test_age_label_format():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=45)).isoformat()
    assert age_label(ts).endswith("s")
