# SPDX-License-Identifier: Apache-2.0
"""Tests for `tokenpak recommendations` CLI command (TIP-07)."""

from __future__ import annotations

import json
import time
import types
from pathlib import Path

import pytest

from tokenpak.cli.commands.recommendations import (
    cmd_recommendations,
    parse_window,
)
from tokenpak.telemetry.models import Cost, TelemetryEvent, Usage
from tokenpak.telemetry.storage import TelemetryDB


def _args(**kwargs) -> types.SimpleNamespace:
    defaults = dict(
        window="24h",
        model=None,
        platform=None,
        as_json=False,
        db_path=None,
    )
    defaults.update(kwargs)
    return types.SimpleNamespace(**defaults)


# ---------------------------------------------------------------------------
# parse_window
# ---------------------------------------------------------------------------


def test_parse_window_hours_default_unit():
    assert parse_window("24") == 24
    assert parse_window("12h") == 12
    assert parse_window("12H") == 12


def test_parse_window_days():
    assert parse_window("7d") == 7 * 24
    assert parse_window("1D") == 24


def test_parse_window_default_when_none():
    assert parse_window(None) == 24


def test_parse_window_rejects_garbage():
    with pytest.raises(ValueError):
        parse_window("forever")
    with pytest.raises(ValueError):
        parse_window("0h")
    with pytest.raises(ValueError):
        parse_window("-3h")


# ---------------------------------------------------------------------------
# cmd_recommendations — empty / missing DB
# ---------------------------------------------------------------------------


def test_cmd_runs_when_db_missing(tmp_path, capsys):
    args = _args(db_path=str(tmp_path / "no-such.db"))
    rc = cmd_recommendations(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "TokenPak Recommendations" in out
    assert "No recommendations" in out


def test_cmd_json_runs_when_db_missing(tmp_path, capsys):
    args = _args(db_path=str(tmp_path / "no-such.db"), as_json=True)
    rc = cmd_recommendations(args)
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    assert payload["count"] == 0
    assert payload["window_hours"] == 24
    assert payload["recommendations"] == []


def test_cmd_invalid_window_returns_2(tmp_path, capsys):
    args = _args(window="not-a-window", db_path=str(tmp_path / "x.db"))
    rc = cmd_recommendations(args)
    err = capsys.readouterr().err
    assert rc == 2
    assert "tokenpak recommendations" in err
    assert "invalid --window" in err


# ---------------------------------------------------------------------------
# cmd_recommendations — populated DB
# ---------------------------------------------------------------------------


def _seed_zero_cache_db(db_path: Path, n: int = 8) -> None:
    db = TelemetryDB(str(db_path))
    base = time.time()
    for i in range(n):
        tid = f"trace-{i}"
        db.insert_trace(
            TelemetryEvent(
                trace_id=tid,
                request_id=f"req-{i}",
                ts=base + i,
                provider="openai",
                model="gpt-5.5",
                agent_id="agent-trix",
                status="ok",
            ),
            Usage(
                trace_id=tid,
                input_billed=100,
                output_billed=50,
                cache_read=0,
                usage_source="provider",
                total_tokens_billed=150,
            ),
            Cost(trace_id=tid, cost_total=0.001),
            [],
        )
    db.close()


def test_cmd_human_output_lists_zero_cache_high_impact(tmp_path, capsys):
    db_path = tmp_path / "telemetry.db"
    _seed_zero_cache_db(db_path)
    args = _args(db_path=str(db_path), window="24h")
    rc = cmd_recommendations(args)
    out = capsys.readouterr().out
    assert rc == 0
    assert "High Impact" in out
    assert "0 cache reads" in out
    assert "Action:" in out


def test_cmd_json_output_includes_zero_cache_rec(tmp_path, capsys):
    db_path = tmp_path / "telemetry.db"
    _seed_zero_cache_db(db_path)
    args = _args(db_path=str(db_path), as_json=True)
    rc = cmd_recommendations(args)
    out = capsys.readouterr().out
    assert rc == 0
    payload = json.loads(out)
    ids = {r["id"] for r in payload["recommendations"]}
    assert "cache.zero-lookups" in ids
    assert payload["count"] >= 1
    rec = next(r for r in payload["recommendations"] if r["id"] == "cache.zero-lookups")
    assert rec["severity"] == "high"
    assert "n_traces" in rec["evidence"]


def test_cmd_window_flag_changes_horizon(tmp_path, capsys):
    db_path = tmp_path / "telemetry.db"
    db = TelemetryDB(str(db_path))
    old_ts = time.time() - 30 * 3600
    for i in range(8):
        tid = f"old-{i}"
        db.insert_trace(
            TelemetryEvent(
                trace_id=tid,
                request_id=f"req-{i}",
                ts=old_ts + i,
                provider="openai",
                model="gpt-5.5",
                agent_id="agent-trix",
                status="ok",
            ),
            Usage(
                trace_id=tid,
                input_billed=100,
                output_billed=50,
                cache_read=0,
                usage_source="provider",
                total_tokens_billed=150,
            ),
            Cost(trace_id=tid, cost_total=0.001),
            [],
        )
    db.close()

    # Default 24h window: events out of scope → no zero-cache rec
    rc = cmd_recommendations(_args(db_path=str(db_path), as_json=True, window="24h"))
    out = json.loads(capsys.readouterr().out)
    assert "cache.zero-lookups" not in {r["id"] for r in out["recommendations"]}

    # Widen to 72h: events in scope → rec fires
    rc = cmd_recommendations(_args(db_path=str(db_path), as_json=True, window="72h"))
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert "cache.zero-lookups" in {r["id"] for r in out["recommendations"]}
