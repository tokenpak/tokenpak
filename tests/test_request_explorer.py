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


def _write_jsonl(path: Path, rows: list[dict], add_bad: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
        if add_bad:
            f.write("{bad json}\n")


def test_load_requests_skips_bad_json(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": "req1"}, {"id": "req2"}]
    _write_jsonl(path, rows, add_bad=True)
    loaded = load_requests(path=path)
    assert len(loaded) == 2


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": f"req{i}"} for i in range(5)]
    _write_jsonl(path, rows)
    loaded = load_requests(path=path, limit=2)
    assert [r["id"] for r in loaded] == ["req3", "req4"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": "req1"}, {"id": "req2"}]
    _write_jsonl(path, rows)
    assert get_request_by_id("req2", path=path)["id"] == "req2"


def test_cache_pct():
    view = to_view({"id": "req", "model": "m", "input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label_cached():
    view = to_view({"id": "req", "model": "m", "input_tokens": 10, "cache_read": 1, "status": "success"})
    assert status_label(view) == "cached"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    assert age_label(ts).endswith("s")
