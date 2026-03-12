import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

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
    path.write_text('{"id": "a"}\n{bad json}\n')
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "a"


def test_load_requests_limit(tmp_path: Path):
    rows = [{"id": str(i)} for i in range(5)]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)
    loaded = load_requests(path=path, limit=2)
    assert [r["id"] for r in loaded] == ["3", "4"]


def test_get_request_by_id(tmp_path: Path):
    rows = [{"id": "a"}, {"id": "b"}]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)
    found = get_request_by_id("b", path=path)
    assert found is not None
    assert found["id"] == "b"


def test_view_helpers():
    row = {
        "id": "r1",
        "model": "claude",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read": 25,
        "saved_cost": 0.02,
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    view = to_view(row)
    assert view.request_id == "r1"
    assert cache_pct(view) == 25.0
    assert status_label(view) == "cached"


def test_status_label_error():
    row = {"id": "r2", "status": "error", "timestamp": ""}
    view = to_view(row)
    assert status_label(view) == "error"


def test_age_label_recent():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert age_label(ts).endswith("s")
