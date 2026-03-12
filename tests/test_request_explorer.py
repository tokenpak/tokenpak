from datetime import datetime, timezone, timedelta

from tokenpak.request_explorer import (
    RequestView,
    cache_pct,
    status_label,
    age_label,
    to_view,
)


def test_cache_pct():
    view = RequestView(request_id="r1", model="m", input_tokens=100, output_tokens=10, cache_read=25, saved_cost=0.0, status="success", timestamp="")
    assert cache_pct(view) == 25.0


def test_status_label_cached():
    view = RequestView(request_id="r1", model="m", input_tokens=100, output_tokens=10, cache_read=10, saved_cost=0.0, status="success", timestamp="")
    assert status_label(view) == "cached"


def test_status_label_error():
    view = RequestView(request_id="r1", model="m", input_tokens=100, output_tokens=10, cache_read=0, saved_cost=0.0, status="error", timestamp="")
    assert status_label(view) == "error"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=30)).isoformat()
    assert age_label(ts).endswith("s")


def test_to_view_defaults():
    row = {"id": "req1", "model": "claude", "input_tokens": "5", "output_tokens": None, "cache_read": "2", "saved_cost": "0.1", "status": "success", "timestamp": "2026-03-01T00:00:00Z"}
    view = to_view(row)
    assert view.request_id == "req1"
    assert view.input_tokens == 5
    assert view.output_tokens == 0
    assert view.cache_read == 2
