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


def test_load_requests_filters_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text('{"id": "r1"}\n{bad json}\n{"id": "r2"}\n')
    rows = load_requests(path=path)
    assert [r["id"] for r in rows] == ["r1", "r2"]


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": f"r{i}"} for i in range(5)])
    rows = load_requests(path=path, limit=2)
    assert [r["id"] for r in rows] == ["r3", "r4"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": "r1"}, {"id": "r2"}])
    row = get_request_by_id("r2", path=path)
    assert row["id"] == "r2"


def test_to_view_defaults():
    view = to_view({"id": "r1", "model": "m"})
    assert view.request_id == "r1"
    assert view.model == "m"
    assert view.input_tokens == 0


def test_cache_pct():
    view = to_view({"id": "r1", "input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label_cached_and_error():
    cached = to_view({"id": "r1", "cache_read": 10, "status": "success"})
    error = to_view({"id": "r2", "status": "error"})
    assert status_label(cached) == "cached"
    assert status_label(error) == "error"


def test_age_label():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(seconds=20)).isoformat()
    assert age_label(ts) in {"20s", "19s", "21s"}
