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


def _write_jsonl(path: Path, rows: list[dict], extra_lines: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
        if extra_lines:
            for line in extra_lines:
                f.write(line + "\n")


def test_load_requests_skips_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": "r1"}], extra_lines=["{bad json}"])
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "r1"


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    rows = [{"id": f"r{i}"} for i in range(5)]
    _write_jsonl(path, rows)
    tail = load_requests(path=path, limit=2)
    assert [r["id"] for r in tail] == ["r3", "r4"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": "r1"}, {"id": "r2"}])
    row = get_request_by_id("r2", path=path)
    assert row is not None
    assert row["id"] == "r2"


def test_to_view_defaults():
    view = to_view({})
    assert view.request_id == ""
    assert view.input_tokens == 0
    assert view.output_tokens == 0


def test_cache_pct():
    view = to_view({"input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label_cached():
    view = to_view({"cache_read": 10, "status": "success"})
    assert status_label(view) == "cached"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert age_label(ts).endswith("s")
