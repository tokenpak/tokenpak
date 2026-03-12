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


def test_load_requests_skips_bad_json(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text("{bad json}\n" + json.dumps({"id": "ok"}) + "\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "ok"


def test_get_request_by_id(tmp_path: Path):
    rows = [{"id": "a"}, {"id": "b"}]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)
    assert get_request_by_id("b", path=path) == {"id": "b"}
    assert get_request_by_id("c", path=path) is None


def test_to_view_defaults():
    view = to_view({"id": "x", "model": "m"})
    assert view.request_id == "x"
    assert view.input_tokens == 0
    assert view.output_tokens == 0
    assert view.cache_read == 0


def test_cache_pct():
    view = to_view({"id": "x", "model": "m", "input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label():
    view = to_view({"id": "x", "model": "m", "status": "error"})
    assert status_label(view) == "error"
    view2 = to_view({"id": "x", "model": "m", "status": "success", "cache_read": 10})
    assert status_label(view2) == "cached"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert age_label(ts).endswith("s")
