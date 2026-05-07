# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak telemetry export CLI command.

Covers: JSON output format, date filtering (since/until/combined),
empty result, provider filter, CSV format, summary stats, no-DB handling.
"""

from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from tokenpak.cli.commands.telemetry import (
    _parse_date_to_ts,
    cmd_telemetry_export,
)
from tokenpak.telemetry.storage import TelemetryDB

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> Path:
    """Create a real telemetry.db with known data."""
    db_path = tmp_path / "telemetry.db"
    db = TelemetryDB(str(db_path))

    # Insert 3 traces across two providers
    from tokenpak.telemetry.models import Cost, TelemetryEvent, Usage

    events = [
        TelemetryEvent(
            trace_id="t1",
            request_id="r1",
            ts=1_700_000_000.0,  # 2023-11-14
            provider="anthropic",
            model="claude-3-haiku",
            agent_id="agent-a",
            status="ok",
        ),
        TelemetryEvent(
            trace_id="t2",
            request_id="r2",
            ts=1_700_086_400.0,  # 2023-11-15
            provider="openai",
            model="gpt-4o",
            agent_id="agent-b",
            status="ok",
        ),
        TelemetryEvent(
            trace_id="t3",
            request_id="r3",
            ts=1_700_172_800.0,  # 2023-11-16
            provider="anthropic",
            model="claude-3-5-sonnet",
            agent_id="agent-a",
            status="ok",
        ),
    ]
    usages = [
        Usage(trace_id="t1", input_billed=100, output_billed=50, total_tokens_billed=150),
        Usage(trace_id="t2", input_billed=200, output_billed=80, total_tokens_billed=280),
        Usage(trace_id="t3", input_billed=300, output_billed=120, total_tokens_billed=420),
    ]
    costs = [
        Cost(trace_id="t1", cost_total=0.001500),
        Cost(trace_id="t2", cost_total=0.003200),
        Cost(trace_id="t3", cost_total=0.004800),
    ]

    for e, u, c in zip(events, usages, costs):
        db.insert_trace(e, u, c, [])
    db.close()
    return db_path


def _args(**kwargs) -> types.SimpleNamespace:
    defaults = dict(format="json", since=None, until=None, provider=None, _db_path=None)
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


def _capture(capsys, args):
    cmd_telemetry_export(args)
    return capsys.readouterr().out


# ---------------------------------------------------------------------------
# _parse_date_to_ts
# ---------------------------------------------------------------------------


def test_parse_date_valid():
    ts = _parse_date_to_ts("2026-01-01", "since")
    assert isinstance(ts, float)
    assert ts > 0


def test_parse_date_invalid():
    with pytest.raises(ValueError, match="--since"):
        _parse_date_to_ts("01/01/2026", "since")


def test_parse_date_invalid_until_label():
    with pytest.raises(ValueError, match="--until"):
        _parse_date_to_ts("not-a-date", "until")


# ---------------------------------------------------------------------------
# JSON output format
# ---------------------------------------------------------------------------


def test_json_output_envelope(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    out = _capture(capsys, _args(_db_path=db_path))
    data = json.loads(out)
    assert "meta" in data
    assert "data" in data
    assert "summary" in data["meta"]
    assert "generated_at" in data["meta"]
    assert "filters" in data["meta"]


def test_json_output_all_rows(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    out = _capture(capsys, _args(_db_path=db_path))
    data = json.loads(out)
    assert len(data["data"]) == 3


def test_json_summary_stats(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    out = _capture(capsys, _args(_db_path=db_path))
    summary = json.loads(out)["meta"]["summary"]
    assert summary["total_requests"] == 3
    assert summary["total_tokens"] == 150 + 280 + 420
    assert abs(summary["total_cost_usd"] - 0.0095) < 1e-6


def test_json_row_fields(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    out = _capture(capsys, _args(_db_path=db_path))
    row = json.loads(out)["data"][0]
    for field in ("trace_id", "ts", "ts_iso", "provider", "model", "agent_id",
                  "status", "input_tokens", "output_tokens", "total_tokens", "cost_usd"):
        assert field in row, f"Missing field: {field}"


# ---------------------------------------------------------------------------
# Date filtering — --since
# ---------------------------------------------------------------------------


def test_since_filter_excludes_earlier(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    # 2023-11-15 → should exclude t1 (2023-11-14), include t2 and t3
    out = _capture(capsys, _args(_db_path=db_path, since="2023-11-15"))
    data = json.loads(out)
    trace_ids = {r["trace_id"] for r in data["data"]}
    assert "t1" not in trace_ids
    assert "t2" in trace_ids
    assert "t3" in trace_ids


def test_since_filter_summary(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    out = _capture(capsys, _args(_db_path=db_path, since="2023-11-15"))
    summary = json.loads(out)["meta"]["summary"]
    assert summary["total_requests"] == 2


# ---------------------------------------------------------------------------
# Date filtering — --until
# ---------------------------------------------------------------------------


def test_until_filter_excludes_later(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    # 2023-11-15 → include t1 and t2, exclude t3 (2023-11-16)
    out = _capture(capsys, _args(_db_path=db_path, until="2023-11-15"))
    data = json.loads(out)
    trace_ids = {r["trace_id"] for r in data["data"]}
    assert "t3" not in trace_ids
    assert "t1" in trace_ids
    assert "t2" in trace_ids


# ---------------------------------------------------------------------------
# Date filtering — combined since + until
# ---------------------------------------------------------------------------


def test_since_until_combined(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    # 2023-11-15 to 2023-11-15 → only t2
    out = _capture(capsys, _args(_db_path=db_path, since="2023-11-15", until="2023-11-15"))
    data = json.loads(out)
    assert len(data["data"]) == 1
    assert data["data"][0]["trace_id"] == "t2"


# ---------------------------------------------------------------------------
# Provider filter
# ---------------------------------------------------------------------------


def test_provider_filter_anthropic(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    out = _capture(capsys, _args(_db_path=db_path, provider="anthropic"))
    data = json.loads(out)
    trace_ids = {r["trace_id"] for r in data["data"]}
    assert "t2" not in trace_ids  # openai
    assert "t1" in trace_ids
    assert "t3" in trace_ids


def test_provider_filter_openai(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    out = _capture(capsys, _args(_db_path=db_path, provider="openai"))
    data = json.loads(out)
    assert len(data["data"]) == 1
    assert data["data"][0]["provider"] == "openai"


def test_provider_filter_in_json_filters(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    out = _capture(capsys, _args(_db_path=db_path, provider="anthropic"))
    filters = json.loads(out)["meta"]["filters"]
    assert filters["provider"] == "anthropic"


# ---------------------------------------------------------------------------
# CSV format
# ---------------------------------------------------------------------------


def test_csv_has_header(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    out = _capture(capsys, _args(_db_path=db_path, format="csv"))
    lines = out.strip().splitlines()
    assert lines[0].startswith("trace_id,")


def test_csv_row_count(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    out = _capture(capsys, _args(_db_path=db_path, format="csv"))
    # header + 3 data rows + 1 summary comment
    lines = [l for l in out.strip().splitlines() if not l.startswith("#")]
    assert len(lines) == 4  # 1 header + 3 rows


def test_csv_summary_trailer(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    out = _capture(capsys, _args(_db_path=db_path, format="csv"))
    summary_lines = [l for l in out.splitlines() if l.startswith("# summary:")]
    assert len(summary_lines) == 1
    assert "3 requests" in summary_lines[0]
    assert "850 tokens" in summary_lines[0]


def test_csv_summary_cost_format(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    out = _capture(capsys, _args(_db_path=db_path, format="csv"))
    summary_line = next(l for l in out.splitlines() if l.startswith("# summary:"))
    assert "$" in summary_line


# ---------------------------------------------------------------------------
# Empty result
# ---------------------------------------------------------------------------


def test_empty_result_json(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    # Filter to a date far in the future
    out = _capture(capsys, _args(_db_path=db_path, since="2099-01-01"))
    data = json.loads(out)
    assert data["data"] == []
    assert data["meta"]["summary"]["total_requests"] == 0
    assert data["meta"]["summary"]["total_tokens"] == 0
    assert data["meta"]["summary"]["total_cost_usd"] == 0.0


def test_empty_result_csv(tmp_path, capsys):
    db_path = _make_db(tmp_path)
    out = _capture(capsys, _args(_db_path=db_path, format="csv", since="2099-01-01"))
    assert "# summary: 0 requests" in out


# ---------------------------------------------------------------------------
# No DB (graceful handling)
# ---------------------------------------------------------------------------


def test_no_db_json(tmp_path, capsys):
    missing = tmp_path / "nonexistent.db"
    out = _capture(capsys, _args(_db_path=missing))
    data = json.loads(out)
    assert data["data"] == []
    assert data["meta"]["summary"]["total_requests"] == 0


def test_no_db_csv(tmp_path, capsys):
    missing = tmp_path / "nonexistent.db"
    out = _capture(capsys, _args(_db_path=missing, format="csv"))
    assert "# summary: 0 requests" in out
    assert "trace_id" in out  # header still present
