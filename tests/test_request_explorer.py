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


def _write_jsonl(path: Path, rows: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(row + "\n")


def test_load_requests_skips_invalid(tmp_path: Path):
    rows = [
        json.dumps({"id": "req1", "model": "m1"}),
        "{bad json}",
        json.dumps({"id": "req2", "model": "m2"}),
    ]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)

    loaded = load_requests(path=path)
    assert len(loaded) == 2
    assert loaded[0]["id"] == "req1"
    assert loaded[1]["id"] == "req2"


def test_load_requests_limit(tmp_path: Path):
    rows = [json.dumps({"id": f"req{i}"}) for i in range(5)]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)

    loaded = load_requests(path=path, limit=2)
    assert [r["id"] for r in loaded] == ["req3", "req4"]


def test_get_request_by_id(tmp_path: Path):
    rows = [json.dumps({"id": "req1"}), json.dumps({"id": "req2"})]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)

    found = get_request_by_id("req2", path=path)
    assert found
    assert found["id"] == "req2"


def test_to_view_defaults():
    view = to_view({"id": "req", "model": "m"})
    assert view.request_id == "req"
    assert view.model == "m"
    assert view.input_tokens == 0
    assert view.output_tokens == 0


def test_cache_pct_and_status():
    view = to_view({"id": "req", "model": "m", "input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0
    assert status_label(view) == "cached"

    view = to_view({"id": "req", "model": "m", "status": "error"})
    assert status_label(view) == "error"


def test_age_label():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(seconds=30)).isoformat()
    assert age_label(ts).endswith("s")
