import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tokenpak.request_explorer import (
    RequestView,
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
    rows = [{"id": "r1"}, {"id": "r2"}]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)
    assert get_request_by_id("r2", path=path)["id"] == "r2"
    assert get_request_by_id("missing", path=path) is None


def test_to_view_casts_fields():
    row = {
        "id": "r1",
        "model": "claude",
        "input_tokens": "10",
        "output_tokens": None,
        "cache_read": "3",
        "saved_cost": "0.04",
        "status": "success",
        "timestamp": "2026-03-12T00:00:00Z",
    }
    view = to_view(row)
    assert isinstance(view, RequestView)
    assert view.input_tokens == 10
    assert view.output_tokens == 0
    assert view.cache_read == 3
    assert view.saved_cost == 0.04


def test_cache_pct():
    view = RequestView(
        request_id="r1",
        model="m",
        input_tokens=100,
        output_tokens=0,
        cache_read=25,
        saved_cost=0.0,
        status="success",
        timestamp="",
    )
    assert cache_pct(view) == 25.0


def test_status_label():
    base = RequestView(
        request_id="r1",
        model="m",
        input_tokens=0,
        output_tokens=0,
        cache_read=0,
        saved_cost=0.0,
        status="success",
        timestamp="",
    )
    assert status_label(base) == "fresh"
    cached = RequestView(**{**base.__dict__})
    cached.cache_read = 5
    assert status_label(cached) == "cached"
    err = RequestView(**{**base.__dict__})
    err.status = "error"
    assert status_label(err) == "error"


def test_age_label():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(seconds=30)).isoformat()
    assert age_label(ts).endswith("s")
