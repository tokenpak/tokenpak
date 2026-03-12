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
    path = tmp_path / "requests.jsonl"
    rows = [{"id": "req_1"}, {"id": "req_2"}]
    _write_jsonl(path, rows)
    assert get_request_by_id("req_2", path=path)["id"] == "req_2"
    assert get_request_by_id("missing", path=path) is None


def test_cache_pct_and_status_label():
    view = to_view({"id": "x", "model": "m", "input_tokens": 100, "output_tokens": 5, "cache_read": 50})
    assert cache_pct(view) == 50.0
    assert status_label(view) == "cached"

    view2 = to_view({"id": "x", "model": "m", "input_tokens": 100, "output_tokens": 5, "status": "error"})
    assert status_label(view2) == "error"


def test_age_label():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=42)).isoformat()
    assert age_label(ts).endswith("s")


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": f"r{i}"} for i in range(5)]
    _write_jsonl(path, rows)
    loaded = load_requests(path=path, limit=2)
    assert [r["id"] for r in loaded] == ["r3", "r4"]
