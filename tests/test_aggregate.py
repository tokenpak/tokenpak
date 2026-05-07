
import pytest

pytest.importorskip("tokenpak.aggregate", reason="module not available in current build")
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tokenpak.aggregate import (
    aggregate_records,
    load_requests,
    parse_since,
    render_table,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def test_parse_since_duration():
    since = parse_since("7d")
    assert since is not None
    delta = datetime.now(timezone.utc) - since
    assert delta.days in (6, 7)


def test_parse_since_iso_date():
    since = parse_since("2026-03-01")
    assert since is not None
    assert since.tzinfo is not None
    assert since.year == 2026
    assert since.month == 3
    assert since.day == 1


def test_load_requests_filters_by_since(tmp_path: Path):
    now = datetime.now(timezone.utc)
    rows = [
        {"timestamp": (now - timedelta(days=2)).isoformat(), "model": "m1", "input_tokens": 10, "output_tokens": 5},
        {"timestamp": (now - timedelta(hours=1)).isoformat(), "model": "m2", "input_tokens": 3, "output_tokens": 2},
    ]
    path = tmp_path / "requests.jsonl"
    _write_jsonl(path, rows)

    since = now - timedelta(days=1)
    loaded = load_requests(path=path, since=since)
    assert len(loaded) == 1
    assert loaded[0]["model"] == "m2"


def test_aggregate_records_totals():
    records = [
        {"agent": "main", "model": "claude", "input_tokens": 100, "output_tokens": 50, "cost": 0.01, "saved_cost": 0.002},
        {"agent": "main", "model": "claude", "input_tokens": 200, "output_tokens": 100, "cost": 0.03, "saved_cost": 0.006},
        {"agent": "embed", "model": "haiku", "input_tokens": 10, "output_tokens": 5, "cost": 0.001, "saved_cost": 0.0005},
    ]
    rows, totals = aggregate_records(records, machine="cali")
    assert totals["requests"] == 3
    assert totals["tokens"] == 465
    assert pytest.approx(totals["cost"], rel=1e-6) == 0.041
    assert pytest.approx(totals["saved"], rel=1e-6) == 0.0085

    # Ensure rows include both agent/model combos
    keys = {(r.agent, r.model) for r in rows}
    assert ("main", "claude") in keys
    assert ("embed", "haiku") in keys


def test_aggregate_records_handles_missing_fields():
    records = [
        {"model": "claude"},
        {"agent": "main", "model": "claude", "input_tokens": "3", "output_tokens": None, "cost": "0.2", "saved_cost": "0.1"},
    ]
    rows, totals = aggregate_records(records, machine="cali")
    assert totals["requests"] == 2
    assert totals["tokens"] == 3
    assert totals["cost"] == 0.2
    assert totals["saved"] == 0.1


def test_render_table_output():
    records = [
        {"agent": "main", "model": "claude", "input_tokens": 10, "output_tokens": 5, "cost": 0.01, "saved_cost": 0.002},
    ]
    rows, totals = aggregate_records(records, machine="cali")
    table = render_table(rows, totals)
    assert "Agent" in table
    assert "TOTAL" in table
    assert "claude" in table
