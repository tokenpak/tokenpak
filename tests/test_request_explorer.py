from datetime import datetime, timedelta, timezone
from pathlib import Path

from tokenpak import request_explorer as rex


def _write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n")


def test_load_requests_skips_bad_json(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_lines(path, ["{bad json}", '{"id": "a", "model": "m"}'])
    rows = rex.load_requests(path=path)
    assert len(rows) == 1
    assert rows[0]["id"] == "a"


def test_load_requests_limit(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_lines(path, [
        '{"id": "a"}',
        '{"id": "b"}',
        '{"id": "c"}',
    ])
    rows = rex.load_requests(path=path, limit=2)
    assert [r["id"] for r in rows] == ["b", "c"]


def test_get_request_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    _write_lines(path, ['{"id": "a", "model": "m"}'])
    row = rex.get_request_by_id("a", path=path)
    assert row is not None
    assert row["model"] == "m"


def test_cache_pct_and_status_label():
    view = rex.RequestView(
        request_id="req",
        model="m",
        input_tokens=100,
        output_tokens=10,
        cache_read=25,
        saved_cost=0.01,
        status="success",
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    assert rex.cache_pct(view) == 25.0
    assert rex.status_label(view) == "cached"


def test_status_label_error():
    view = rex.RequestView(
        request_id="req",
        model="m",
        input_tokens=0,
        output_tokens=0,
        cache_read=0,
        saved_cost=0.0,
        status="error",
        timestamp="",
    )
    assert rex.status_label(view) == "error"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert rex.age_label(ts).endswith("s")
