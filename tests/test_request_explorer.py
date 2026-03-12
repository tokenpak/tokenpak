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


def test_load_requests_ignores_malformed(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    with path.open("w") as f:
        f.write("{bad json}\n")
        f.write(json.dumps({"id": "ok", "model": "m"}) + "\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "ok"


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": f"r{i}", "model": "m"} for i in range(5)])
    rows = load_requests(path=path, limit=2)
    assert [r["id"] for r in rows] == ["r3", "r4"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": "r1", "model": "m"}, {"id": "r2", "model": "n"}])
    row = get_request_by_id("r2", path=path)
    assert row is not None
    assert row["model"] == "n"


def test_cache_pct_and_status_label():
    view = to_view({"id": "r1", "model": "m", "input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0
    assert status_label(view) == "cached"


def test_status_label_error():
    view = to_view({"id": "r1", "model": "m", "status": "error"})
    assert status_label(view) == "error"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert age_label(ts).endswith("s")
