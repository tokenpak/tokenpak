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


def test_load_requests_skips_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text("{bad json}\n" + json.dumps({"id": "ok"}) + "\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "ok"


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": "req_1"}, {"id": "req_2"}]
    _write_jsonl(path, rows)
    assert get_request_by_id("req_2", path=path)["id"] == "req_2"
    assert get_request_by_id("req_x", path=path) is None


def test_to_view_defaults():
    view = to_view({"id": "req_1", "model": "m"})
    assert view.request_id == "req_1"
    assert view.input_tokens == 0


def test_cache_pct():
    view = to_view({"id": "req", "input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label():
    view = to_view({"id": "req", "status": "success", "cache_read": 10})
    assert status_label(view) == "cached"
    view2 = to_view({"id": "req", "status": "error"})
    assert status_label(view2) == "error"


def test_age_label():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    assert age_label(ts).endswith("s")
