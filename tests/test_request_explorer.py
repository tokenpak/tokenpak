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
    path.write_text('{"id":"a"}\n{bad json}\n{"id":"b"}\n')
    rows = load_requests(path=path)
    assert len(rows) == 2
    assert rows[0]["id"] == "a"
    assert rows[1]["id"] == "b"


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": "req_1"}, {"id": "req_2"}])
    row = get_request_by_id("req_2", path=path)
    assert row is not None
    assert row["id"] == "req_2"


def test_to_view_defaults():
    view = to_view({"id": "req", "model": "m"})
    assert view.request_id == "req"
    assert view.model == "m"
    assert view.input_tokens == 0
    assert view.output_tokens == 0


def test_cache_pct():
    view = to_view({"id": "req", "model": "m", "input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label():
    view = to_view({"id": "req", "model": "m", "status": "error"})
    assert status_label(view) == "error"
    view = to_view({"id": "req", "model": "m", "cache_read": 5})
    assert status_label(view) == "cached"


def test_age_label():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    view = to_view({"id": "req", "model": "m", "timestamp": ts})
    assert age_label(view.timestamp).endswith("s")
