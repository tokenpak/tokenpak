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


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def test_load_requests_skips_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, ["{\"id\": \"r1\"}", "not-json", "{\"id\": \"r2\"}"])
    rows = load_requests(path=path)
    assert [r["id"] for r in rows] == ["r1", "r2"]


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, ["{\"id\": \"r1\"}", "{\"id\": \"r2\"}", "{\"id\": \"r3\"}"])
    rows = load_requests(path=path, limit=2)
    assert [r["id"] for r in rows] == ["r2", "r3"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, ["{\"id\": \"r1\"}", "{\"id\": \"r2\"}"])
    assert get_request_by_id("r2", path=path)["id"] == "r2"
    assert get_request_by_id("r3", path=path) is None


def test_cache_pct():
    view = to_view({"id": "r1", "model": "m", "input_tokens": 100, "output_tokens": 10, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label():
    view_cached = to_view({"id": "r1", "model": "m", "cache_read": 5, "status": "success"})
    view_error = to_view({"id": "r2", "model": "m", "status": "error"})
    view_fresh = to_view({"id": "r3", "model": "m", "status": "success"})
    assert status_label(view_cached) == "cached"
    assert status_label(view_error) == "error"
    assert status_label(view_fresh) == "fresh"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert age_label(ts).endswith("s")
