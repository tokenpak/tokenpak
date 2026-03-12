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
            f.write(__import__("json").dumps(row) + "\n")


def test_load_requests_skips_bad_lines(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text("{bad json}\n" + __import__("json").dumps({"id": "a"}) + "\n")
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
    rows = [{"id": "req_1"}, {"id": "req_2"}]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)
    assert get_request_by_id("req_2", path=path)["id"] == "req_2"
    assert get_request_by_id("missing", path=path) is None


def test_to_view_defaults():
    view = to_view({"id": "req", "model": "m"})
    assert view.request_id == "req"
    assert view.model == "m"
    assert view.input_tokens == 0
    assert view.output_tokens == 0


def test_cache_pct_and_status():
    view = to_view({"id": "r", "input_tokens": 100, "cache_read": 20, "status": "success"})
    assert cache_pct(view) == 20.0
    assert status_label(view) == "cached"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert age_label(ts).endswith("s")
