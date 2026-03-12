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
    good = {"id": "req1", "model": "m1"}
    lines = [json.dumps(good), "{bad json}", ""]
    _write_lines(path, lines)

    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "req1"


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    lines = [json.dumps({"id": "req_a"}), json.dumps({"id": "req_b"})]
    _write_lines(path, lines)

    row = get_request_by_id("req_b", path=path)
    assert row is not None
    assert row["id"] == "req_b"


def test_cache_pct_calculation():
    view = to_view({"input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label_cached():
    view = to_view({"status": "success", "cache_read": 10})
    assert status_label(view) == "cached"


def test_status_label_error():
    view = to_view({"status": "error"})
    assert status_label(view) == "error"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert age_label(ts).endswith("s")
