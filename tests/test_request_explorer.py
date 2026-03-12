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


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_load_requests_ignores_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text('{"id": "a"}\n{bad json}\n')
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "a"


def test_get_request_by_id(tmp_path: Path):
    rows = [{"id": "req_1"}, {"id": "req_2"}]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)
    found = get_request_by_id("req_2", path=path)
    assert found is not None
    assert found["id"] == "req_2"


def test_to_view_defaults():
    view = to_view({"id": "r", "model": "m"})
    assert view.request_id == "r"
    assert view.model == "m"
    assert view.input_tokens == 0
    assert view.output_tokens == 0


def test_cache_pct():
    view = to_view({"id": "r", "model": "m", "input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label_cached():
    view = to_view({"id": "r", "model": "m", "cache_read": 10, "status": "success"})
    assert status_label(view) == "cached"


def test_age_label_seconds():
    now = datetime.now(timezone.utc)
    view = to_view({"id": "r", "model": "m", "timestamp": (now - timedelta(seconds=30)).isoformat()})
    assert age_label(view.timestamp).endswith("s")
