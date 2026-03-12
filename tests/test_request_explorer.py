import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tokenpak import request_explorer as rx


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
    _write_jsonl(path, [{"id": "a"}], extra_lines=["{bad json"])
    rows = rx.load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "a"


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": "a"}, {"id": "b"}, {"id": "c"}])
    rows = rx.load_requests(path=path, limit=2)
    assert [r["id"] for r in rows] == ["b", "c"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, [{"id": "req1"}, {"id": "req2"}])
    assert rx.get_request_by_id("req2", path=path)["id"] == "req2"
    assert rx.get_request_by_id("missing", path=path) is None


def test_to_view_defaults():
    view = rx.to_view({"id": "req", "model": "m"})
    assert view.request_id == "req"
    assert view.model == "m"
    assert view.input_tokens == 0
    assert view.output_tokens == 0


def test_cache_pct_and_status():
    view = rx.RequestView("req", "m", 100, 50, 20, 0.1, "success", "", "")
    assert rx.cache_pct(view) == 20.0
    assert rx.status_label(view) == "cached"
    view2 = rx.RequestView("req", "m", 100, 50, 0, 0.1, "error", "", "")
    assert rx.status_label(view2) == "error"


def test_age_label():
    now = datetime.now(timezone.utc)
    ts = (now - timedelta(seconds=30)).isoformat()
    assert rx.age_label(ts).endswith("s")
    ts2 = (now - timedelta(hours=2)).isoformat()
    assert rx.age_label(ts2).endswith("h")
