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
    path.write_text("{bad json}\n" + json.dumps({"id": "r1"}) + "\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "r1"


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": f"r{i}"} for i in range(5)]
    _write_jsonl(path, rows)
    result = load_requests(path=path, limit=2)
    assert [r["id"] for r in result] == ["r3", "r4"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": "r1"}, {"id": "r2"}]
    _write_jsonl(path, rows)
    result = get_request_by_id("r2", path=path)
    assert result is not None
    assert result["id"] == "r2"


def test_cache_pct():
    view = to_view({"input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label():
    view = to_view({"status": "success", "cache_read": 10})
    assert status_label(view) == "cached"
    view = to_view({"status": "success", "cache_read": 0})
    assert status_label(view) == "fresh"
    view = to_view({"status": "error", "cache_read": 10})
    assert status_label(view) == "error"


def test_age_label():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert age_label(ts).endswith("s")
