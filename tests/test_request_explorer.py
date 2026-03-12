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


def _write_jsonl(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(row + "\n")


def test_load_requests_skips_bad_json(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    good = {"id": "req1", "model": "m", "input_tokens": 1, "output_tokens": 2}
    rows = [json.dumps(good), "{bad json", json.dumps({"id": "req2"})]
    _write_jsonl(path, rows)

    loaded = load_requests(path=path)
    assert len(loaded) == 2
    assert loaded[0]["id"] == "req1"
    assert loaded[1]["id"] == "req2"


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [json.dumps({"id": "req1"}), json.dumps({"id": "req2"})]
    _write_jsonl(path, rows)

    assert get_request_by_id("req2", path=path)["id"] == "req2"
    assert get_request_by_id("missing", path=path) is None


def test_cache_pct():
    view = to_view({"input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label():
    view_cached = to_view({"status": "success", "cache_read": 10})
    view_fresh = to_view({"status": "success", "cache_read": 0})
    view_error = to_view({"status": "error", "cache_read": 0})
    assert status_label(view_cached) == "cached"
    assert status_label(view_fresh) == "fresh"
    assert status_label(view_error) == "error"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert age_label(ts).endswith("s")

def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [json.dumps({"id": f"req{i}"}) for i in range(5)]
    with path.open("w") as f:
        for row in rows:
            f.write(row + "\n")

    loaded = load_requests(path=path, limit=2)
    assert [r["id"] for r in loaded] == ["req3", "req4"]
