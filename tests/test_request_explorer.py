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


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def test_load_requests_skips_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [
        json.dumps({"id": "a", "model": "m1"}),
        "{bad json}",
        json.dumps({"id": "b", "model": "m2"}),
    ]
    _write_lines(path, rows)

    loaded = load_requests(path=path)
    assert len(loaded) == 2
    assert loaded[0]["id"] == "a"
    assert loaded[1]["id"] == "b"


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [json.dumps({"id": str(i)}) for i in range(5)]
    _write_lines(path, rows)

    loaded = load_requests(path=path, limit=2)
    assert [r["id"] for r in loaded] == ["3", "4"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [json.dumps({"id": "req_1", "model": "m1"})]
    _write_lines(path, rows)

    result = get_request_by_id("req_1", path=path)
    assert result is not None
    assert result["model"] == "m1"


def test_cache_pct_and_status_label():
    view = to_view({"id": "1", "model": "m", "input_tokens": 100, "cache_read": 25, "status": "success"})
    assert cache_pct(view) == 25.0
    assert status_label(view) == "cached"

    view2 = to_view({"id": "2", "model": "m", "input_tokens": 100, "cache_read": 0, "status": "success"})
    assert status_label(view2) == "fresh"

    view3 = to_view({"id": "3", "model": "m", "input_tokens": 100, "status": "error"})
    assert status_label(view3) == "error"


def test_age_label_seconds_minutes_hours_days():
    now = datetime.now(timezone.utc)
    assert age_label((now - timedelta(seconds=10)).isoformat()).endswith("s")
    assert age_label((now - timedelta(minutes=5)).isoformat()).endswith("m")
    assert age_label((now - timedelta(hours=2)).isoformat()).endswith("h")
    assert age_label((now - timedelta(days=2)).isoformat()).endswith("d")


def test_to_view_defaults():
    view = to_view({})
    assert view.request_id == ""
    assert view.model == ""
    assert view.input_tokens == 0
    assert view.output_tokens == 0
    assert view.cache_read == 0
