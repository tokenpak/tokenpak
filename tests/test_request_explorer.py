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


def test_load_requests_skips_bad_json(tmp_path: Path):
    rows = [
        json.dumps({"id": "req1", "model": "m1"}),
        "{bad json}",
        json.dumps({"id": "req2", "model": "m2"}),
    ]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)

    loaded = load_requests(path=path)
    assert [r["id"] for r in loaded] == ["req1", "req2"]


def test_load_requests_limit(tmp_path: Path):
    rows = [json.dumps({"id": f"req{i}"}) for i in range(5)]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)

    loaded = load_requests(path=path, limit=2)
    assert [r["id"] for r in loaded] == ["req3", "req4"]


def test_get_request_by_id(tmp_path: Path):
    rows = [json.dumps({"id": "abc", "model": "m"})]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)

    found = get_request_by_id("abc", path=path)
    assert found is not None
    assert found["model"] == "m"


def test_to_view_and_cache_pct():
    row = {
        "id": "req",
        "model": "m",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read": 25,
        "saved_cost": 0.1,
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "session_id": "abc",
    }
    view = to_view(row)
    assert view.request_id == "req"
    assert cache_pct(view) == 25.0


def test_status_label_cached_vs_error():
    row = {
        "id": "req",
        "model": "m",
        "input_tokens": 10,
        "output_tokens": 2,
        "cache_read": 3,
        "saved_cost": 0.01,
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    view = to_view(row)
    assert status_label(view) == "cached"

    row["status"] = "error"
    view = to_view(row)
    assert status_label(view) == "error"


def test_age_label_formats():
    now = datetime.now(timezone.utc)
    assert age_label((now - timedelta(seconds=30)).isoformat()).endswith("s")
    assert age_label((now - timedelta(minutes=5)).isoformat()).endswith("m")
    assert age_label((now - timedelta(hours=2)).isoformat()).endswith("h")
    assert age_label((now - timedelta(days=2)).isoformat()).endswith("d")
