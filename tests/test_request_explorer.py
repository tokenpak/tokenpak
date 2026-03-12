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
    path.write_text("{bad}\n" + json.dumps({"id": "ok"}) + "\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "ok"


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": "a"}, {"id": "b"}])
    assert get_request_by_id("b", path=path)["id"] == "b"
    assert get_request_by_id("missing", path=path) is None


def test_to_view_and_cache_pct():
    row = {
        "id": "req",
        "model": "claude",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read": 25,
        "saved_cost": 0.5,
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    view = to_view(row)
    assert view.request_id == "req"
    assert cache_pct(view) == 25.0
    assert status_label(view) == "cached"


def test_status_label_error():
    row = {
        "id": "req",
        "model": "claude",
        "status": "error",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    view = to_view(row)
    assert status_label(view) == "error"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    assert age_label(ts).endswith("s")


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": str(i)} for i in range(5)]
    _write_jsonl(path, rows)
    loaded = load_requests(path=path, limit=2)
    assert [r["id"] for r in loaded] == ["3", "4"]
