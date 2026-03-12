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


def test_load_requests_skips_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text("{bad json}\n" + json.dumps({"id": "r1"}) + "\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "r1"


def test_load_requests_limit(tmp_path: Path):
    rows = [{"id": f"r{i}"} for i in range(5)]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)
    limited = load_requests(path=path, limit=2)
    assert [r["id"] for r in limited] == ["r3", "r4"]


def test_get_request_by_id(tmp_path: Path):
    rows = [{"id": "a"}, {"id": "b"}]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)
    found = get_request_by_id("b", path=path)
    assert found["id"] == "b"


def test_to_view_defaults():
    view = to_view({"id": "x", "model": "m"})
    assert view.request_id == "x"
    assert view.input_tokens == 0
    assert view.output_tokens == 0


def test_cache_pct_and_status():
    view = to_view({"id": "x", "model": "m", "input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0
    assert status_label(view) == "cached"

    view2 = to_view({"id": "y", "model": "m", "status": "error"})
    assert status_label(view2) == "error"


def test_age_label():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(seconds=30)).isoformat()
    assert age_label(ts).endswith("s")
