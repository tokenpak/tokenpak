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


def _write(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def test_load_requests_missing_file(tmp_path: Path):
    path = tmp_path / "missing.jsonl"
    rows = load_requests(path=path)
    assert rows == []


def test_load_requests_skips_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write(path, [
        '{"id": "req1", "model": "claude"}',
        '{bad json',
        '{"id": "req2", "model": "haiku"}',
    ])
    rows = load_requests(path=path)
    assert len(rows) == 2
    assert rows[0]["id"] == "req1"
    assert rows[1]["id"] == "req2"


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write(path, [
        '{"id": "req1"}',
        '{"id": "req2"}',
        '{"id": "req3"}',
    ])
    rows = load_requests(path=path, limit=2)
    assert [r["id"] for r in rows] == ["req2", "req3"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write(path, [
        '{"id": "req1", "model": "claude"}',
        '{"id": "req2", "model": "haiku"}',
    ])
    row = get_request_by_id("req2", path=path)
    assert row is not None
    assert row["model"] == "haiku"


def test_view_helpers():
    now = datetime.now(timezone.utc)
    view = to_view({
        "id": "req1",
        "model": "claude",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read": 25,
        "saved_cost": 0.02,
        "status": "success",
        "timestamp": now.isoformat(),
        "session_id": "abc",
    })
    assert cache_pct(view) == 25.0
    assert status_label(view) == "cached"
    assert age_label(view.timestamp).endswith("s")


def test_age_label_days():
    old = datetime.now(timezone.utc) - timedelta(days=2)
    label = age_label(old.isoformat())
    assert label.endswith("d")
