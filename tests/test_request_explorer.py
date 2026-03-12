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


def test_load_requests_skips_bad_json(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text("{bad}\n" + json.dumps({"id": "req_1"}) + "\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "req_1"


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": f"req_{i}"} for i in range(5)]
    _write_jsonl(path, rows)
    loaded = load_requests(path=path, limit=2)
    assert [r["id"] for r in loaded] == ["req_3", "req_4"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": "req_a"}, {"id": "req_b"}]
    _write_jsonl(path, rows)
    found = get_request_by_id("req_b", path=path)
    assert found is not None
    assert found["id"] == "req_b"


def test_cache_pct_and_status_label():
    view = to_view({"id": "req", "model": "m", "input_tokens": 100, "output_tokens": 5, "cache_read": 25, "saved_cost": 0.1, "status": "success"})
    assert cache_pct(view) == 25.0
    assert status_label(view) == "cached"

    err_view = to_view({"id": "req", "model": "m", "input_tokens": 100, "output_tokens": 5, "cache_read": 0, "saved_cost": 0.1, "status": "error"})
    assert status_label(err_view) == "error"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert age_label(ts) == "30s"


def test_age_label_days():
    ts = (datetime.now(timezone.utc) - timedelta(days=2, hours=1)).isoformat()
    assert age_label(ts).endswith("d")
