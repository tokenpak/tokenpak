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
        f.write(json.dumps({"id": "req_1"}) + "\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "req_1"


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": f"req_{i}"} for i in range(5)])
    rows = load_requests(path=path, limit=2)
    assert [r["id"] for r in rows] == ["req_3", "req_4"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": "a"}, {"id": "b"}])
    row = get_request_by_id("b", path=path)
    assert row is not None
    assert row["id"] == "b"


def test_to_view_defaults():
    view = to_view({"id": "x", "model": "m"})
    assert view.request_id == "x"
    assert view.model == "m"
    assert view.input_tokens == 0
    assert view.output_tokens == 0


def test_cache_pct():
    view = to_view({"id": "x", "model": "m", "input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label():
    view = to_view({"id": "x", "model": "m", "status": "error"})
    assert status_label(view) == "error"
    view = to_view({"id": "x", "model": "m", "cache_read": 10})
    assert status_label(view) == "cached"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    label = age_label(ts)
    assert label.endswith("s")
