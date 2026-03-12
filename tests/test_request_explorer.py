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


def test_load_requests_ignores_bad_json(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text('{"id": "a"}\n{bad json}\n')
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "a"


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": f"r{i}"} for i in range(5)]
    _write_jsonl(path, rows)
    loaded = load_requests(path=path, limit=2)
    assert [r["id"] for r in loaded] == ["r3", "r4"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": "x"}, {"id": "y"}]
    _write_jsonl(path, rows)
    found = get_request_by_id("y", path=path)
    assert found is not None
    assert found["id"] == "y"


def test_to_view_defaults():
    view = to_view({"id": "a", "model": "m"})
    assert view.request_id == "a"
    assert view.model == "m"
    assert view.input_tokens == 0
    assert view.output_tokens == 0


def test_cache_pct():
    view = to_view({"input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label_cached_and_error():
    cached = to_view({"input_tokens": 10, "cache_read": 5})
    assert status_label(cached) == "cached"
    err = to_view({"status": "error"})
    assert status_label(err) == "error"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    assert age_label(ts).endswith("s")
