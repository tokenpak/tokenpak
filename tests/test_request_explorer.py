from datetime import datetime, timedelta, timezone
from pathlib import Path
import json

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


def test_load_requests_skips_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [
        json.dumps({"id": "r1", "model": "m1"}),
        "{broken json}",
        json.dumps({"id": "r2", "model": "m2"}),
    ]
    _write_jsonl(path, rows)

    data = load_requests(path=path)
    assert len(data) == 2
    assert data[0]["id"] == "r1"
    assert data[1]["id"] == "r2"


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [
        json.dumps({"id": "abc", "model": "m1"}),
        json.dumps({"id": "def", "model": "m2"}),
    ]
    _write_jsonl(path, rows)

    row = get_request_by_id("def", path=path)
    assert row is not None
    assert row["model"] == "m2"


def test_cache_pct():
    view = to_view({"id": "r1", "input_tokens": 200, "output_tokens": 10, "cache_read": 50, "saved_cost": 0.1})
    assert cache_pct(view) == 25.0


def test_status_label_cached():
    view = to_view({"id": "r1", "input_tokens": 100, "output_tokens": 10, "cache_read": 10, "status": "success"})
    assert status_label(view) == "cached"


def test_status_label_error():
    view = to_view({"id": "r1", "input_tokens": 100, "output_tokens": 10, "status": "error"})
    assert status_label(view) == "error"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    label = age_label(ts)
    assert label.endswith("s")
