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
        f.write(json.dumps({"id": "req1"}) + "\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "req1"


def test_load_requests_limit(tmp_path: Path):
    rows = [{"id": f"req{i}"} for i in range(5)]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)
    loaded = load_requests(path=path, limit=2)
    assert [r["id"] for r in loaded] == ["req3", "req4"]


def test_get_request_by_id(tmp_path: Path):
    rows = [{"id": "req1"}, {"id": "req2"}]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)
    assert get_request_by_id("req2", path=path)["id"] == "req2"


def test_cache_pct_and_status_label():
    view = to_view({
        "id": "req1",
        "model": "claude",
        "input_tokens": 100,
        "output_tokens": 10,
        "cache_read": 25,
        "saved_cost": 0.1,
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    assert cache_pct(view) == 25.0
    assert status_label(view) == "cached"


def test_status_label_error():
    view = to_view({"id": "req2", "status": "error"})
    assert status_label(view) == "error"


def test_age_label():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert age_label(ts) in {"5s", "6s", "4s"}
