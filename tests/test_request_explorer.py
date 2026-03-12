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


def _write_jsonl(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def test_load_requests_skips_malformed(tmp_path: Path):
    rows = [
        json.dumps({"id": "req1", "model": "claude"}),
        "{bad json}",
        json.dumps({"id": "req2", "model": "haiku"}),
    ]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)

    loaded = load_requests(path=path)
    assert len(loaded) == 2
    assert loaded[0]["id"] == "req1"
    assert loaded[1]["id"] == "req2"


def test_get_request_by_id(tmp_path: Path):
    rows = [json.dumps({"id": "req1", "model": "claude"})]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)

    found = get_request_by_id("req1", path=path)
    assert found is not None
    assert found["model"] == "claude"
    assert get_request_by_id("missing", path=path) is None


def test_view_helpers():
    now = datetime.now(timezone.utc)
    row = {
        "id": "req1",
        "model": "claude",
        "input_tokens": 100,
        "output_tokens": 40,
        "cache_read": 25,
        "saved_cost": 0.01,
        "status": "success",
        "timestamp": now.isoformat(),
        "session_id": "abc",
    }
    view = to_view(row)
    assert cache_pct(view) == 25.0
    assert status_label(view) == "cached"
    assert age_label(view.timestamp).endswith("s")


def test_status_label_error():
    row = {
        "id": "req1",
        "model": "claude",
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read": 0,
        "saved_cost": 0.0,
        "status": "error",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    view = to_view(row)
    assert status_label(view) == "error"


def test_age_label_days():
    ts = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    assert age_label(ts) == "2d"


def test_load_requests_limit(tmp_path: Path):
    rows = [json.dumps({"id": f"req{i}", "model": "m"}) for i in range(5)]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)

    loaded = load_requests(path=path, limit=2)
    assert len(loaded) == 2
    assert loaded[0]["id"] == "req3"
    assert loaded[1]["id"] == "req4"
