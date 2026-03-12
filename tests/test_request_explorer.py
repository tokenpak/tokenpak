from datetime import datetime, timedelta, timezone

from tokenpak.request_explorer import (
    RequestView,
    age_label,
    cache_pct,
    status_label,
    to_view,
)


def test_cache_pct():
    view = RequestView("id", "model", 100, 10, 25, 0.0, "success", "")
    assert cache_pct(view) == 25.0


def test_status_label_cached():
    view = RequestView("id", "model", 100, 10, 5, 0.0, "success", "")
    assert status_label(view) == "cached"


def test_status_label_error():
    view = RequestView("id", "model", 100, 10, 0, 0.0, "error", "")
    assert status_label(view) == "error"


def test_status_label_fresh():
    view = RequestView("id", "model", 100, 10, 0, 0.0, "success", "")
    assert status_label(view) == "fresh"


def test_age_label_seconds():
    ts = (datetime.now(timezone.utc) - timedelta(seconds=5)).isoformat()
    assert age_label(ts).endswith("s")


def test_to_view_defaults():
    row = {"id": "r1", "model": "m1"}
    view = to_view(row)
    assert view.request_id == "r1"
    assert view.model == "m1"
    assert view.input_tokens == 0
    assert view.output_tokens == 0
