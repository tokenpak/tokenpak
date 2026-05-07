
import pytest

pytest.importorskip("tokenpak.request_explorer", reason="module not available in current build")
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



def test_load_requests_ignores_bad_lines(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text('{"id": "a"}\nnot-json\n{"id": "b"}\n')
    rows = load_requests(path=path)
    assert len(rows) == 2
    assert rows[0]["id"] == "a"



def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": "a"}, {"id": "b"}])
    assert get_request_by_id("b", path=path)["id"] == "b"
    assert get_request_by_id("missing", path=path) is None



def test_to_view_and_cache_pct():
    row = {
        "id": "req1",
        "model": "claude",
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read": 25,
        "saved_cost": 0.12,
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    view = to_view(row)
    assert view.request_id == "req1"
    assert cache_pct(view) == 25.0



def test_status_label_cached():
    row = {
        "id": "req1",
        "model": "claude",
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read": 25,
        "saved_cost": 0.12,
        "status": "success",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    view = to_view(row)
    assert status_label(view) == "cached"



def test_status_label_error():
    row = {
        "id": "req1",
        "model": "claude",
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read": 0,
        "saved_cost": 0.12,
        "status": "error",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    view = to_view(row)
    assert status_label(view) == "error"



def test_age_label():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert age_label(ts).endswith("s")
