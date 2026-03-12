from datetime import datetime, timedelta, timezone
from pathlib import Path

from tokenpak import request_explorer as re


def test_load_requests_skips_bad_json(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text("{bad json}\n" + '{"id": "req1"}\n')
    rows = re.load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "req1"


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text('{"id": "abc", "model": "m1"}\n')
    row = re.get_request_by_id("abc", path=path)
    assert row is not None
    assert row["model"] == "m1"


def test_cache_pct():
    view = re.RequestView(
        request_id="id",
        model="m",
        input_tokens=100,
        output_tokens=10,
        cache_read=20,
        saved_cost=0.0,
        status="success",
        timestamp="",
    )
    assert re.cache_pct(view) == 20.0


def test_status_label():
    view = re.RequestView(
        request_id="id",
        model="m",
        input_tokens=10,
        output_tokens=2,
        cache_read=0,
        saved_cost=0.0,
        status="success",
        timestamp="",
    )
    assert re.status_label(view) == "fresh"
    view.cache_read = 5
    assert re.status_label(view) == "cached"
    view.status = "error"
    assert re.status_label(view) == "error"


def test_age_label():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert re.age_label(ts).endswith("s")
