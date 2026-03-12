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


def test_load_requests_missing_file(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    assert load_requests(path=path) == []


def test_load_requests_handles_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text('{"id": "ok"}\n{bad json}\n')
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "ok"


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": "req1"}, {"id": "req2"}])
    assert get_request_by_id("req2", path=path)["id"] == "req2"
    assert get_request_by_id("missing", path=path) is None


def test_view_helpers():
    row = {
        "id": "req_1",
        "model": "claude",
        "input_tokens": 100,
        "output_tokens": 25,
        "cache_read": 20,
        "saved_cost": 0.12,
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    view = to_view(row)
    assert cache_pct(view) == 20.0
    assert status_label(view) == "cached"


def test_status_label_error():
    row = {"id": "req_1", "status": "error", "timestamp": datetime.now(timezone.utc).isoformat()}
    view = to_view(row)
    assert status_label(view) == "error"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert age_label(ts).endswith("s")
