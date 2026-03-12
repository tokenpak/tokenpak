import json
from datetime import datetime, timezone
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
    path.write_text("{bad json}\n" + json.dumps({"id": "req1"}) + "\n")
    rows = load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "req1"


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": "req1"}, {"id": "req2"}])
    row = get_request_by_id("req2", path=path)
    assert row is not None
    assert row["id"] == "req2"


def test_to_view_defaults():
    view = to_view({"id": "req1"})
    assert view.request_id == "req1"
    assert view.model == ""
    assert view.input_tokens == 0


def test_cache_pct():
    view = to_view({"id": "req1", "input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label():
    view = to_view({"id": "req1", "status": "error"})
    assert status_label(view) == "error"
    view2 = to_view({"id": "req2", "cache_read": 10})
    assert status_label(view2) == "cached"


def test_age_label():
    ts = datetime.now(timezone.utc).isoformat()
    assert age_label(ts).endswith("s")
