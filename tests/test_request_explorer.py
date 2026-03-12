from datetime import datetime, timedelta, timezone
from pathlib import Path

from tokenpak.request_explorer import (
    age_label,
    cache_pct,
    format_tokens,
    get_request_by_id,
    load_requests,
    status_label,
    to_view,
)


def test_format_tokens():
    assert format_tokens(999) == "999"
    assert format_tokens(1_200) == "1K"
    assert format_tokens(1_500_000) == "1.5M"


def test_cache_pct():
    view = to_view({"input_tokens": 100, "cache_read": 25})
    assert cache_pct(view) == 25.0


def test_status_label_cached():
    view = to_view({"status": "success", "cache_read": 10})
    assert status_label(view) == "cached"


def test_status_label_error():
    view = to_view({"status": "error", "cache_read": 0})
    assert status_label(view) == "error"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
    assert age_label(ts).endswith("s")


def test_load_requests_and_get_by_id(tmp_path: Path):
    path = tmp_path / "requests.jsonl"
    path.write_text(
        "{\"id\":\"req1\",\"timestamp\":\"2026-03-01T00:00:00Z\",\"input_tokens\":5,\"output_tokens\":2}\n"
        "{\"id\":\"req2\",\"timestamp\":\"2026-03-01T00:00:00Z\",\"input_tokens\":6,\"output_tokens\":3}\n"
    )
    rows = load_requests(path=path)
    assert len(rows) == 2
    assert get_request_by_id("req2", path=path)["id"] == "req2"
