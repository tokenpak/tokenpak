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


def _write_jsonl(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(row + "\n")


def test_load_requests_skips_bad_lines(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [
        json.dumps({"id": "a1", "model": "m1"}),
        "{bad json",
        json.dumps({"id": "a2", "model": "m2"}),
    ]
    _write_jsonl(path, rows)

    loaded = load_requests(path=path)
    assert len(loaded) == 2
    assert loaded[0]["id"] == "a1"
    assert loaded[1]["id"] == "a2"


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [json.dumps({"id": f"r{i}"}) for i in range(5)]
    _write_jsonl(path, rows)

    loaded = load_requests(path=path, limit=2)
    assert [row["id"] for row in loaded] == ["r3", "r4"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [json.dumps({"id": "req_a", "model": "m1"})]
    _write_jsonl(path, rows)

    found = get_request_by_id("req_a", path=path)
    assert found is not None
    assert found["model"] == "m1"
    assert get_request_by_id("missing", path=path) is None


def test_cache_pct_and_status_label():
    view = to_view({"id": "r1", "model": "m", "input_tokens": 100, "cache_read": 20, "status": "success"})
    assert cache_pct(view) == 20.0
    assert status_label(view) == "cached"

    view2 = to_view({"id": "r2", "model": "m", "input_tokens": 100, "cache_read": 0, "status": "success"})
    assert status_label(view2) == "fresh"

    view3 = to_view({"id": "r3", "model": "m", "input_tokens": 100, "cache_read": 0, "status": "error"})
    assert status_label(view3) == "error"


def test_age_label():
    now = datetime.now(timezone.utc)
    assert age_label((now - timedelta(seconds=5)).isoformat()).endswith("s")
    assert age_label((now - timedelta(minutes=5)).isoformat()).endswith("m")
    assert age_label((now - timedelta(hours=2)).isoformat()).endswith("h")
