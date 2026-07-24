"""Unit-truth regression for the fleet rollup savings math.

The ``would_have_saved`` column in monitor.db stores TOKENS (the proxy
writes ``input_tokens - sent_input_tokens``). The fleet status reader used
to divide it by 100_000 as if it were micro-dollars, overstating savings by
orders of magnitude. These tests pin the corrected read-side behavior:
tokens are converted to USD via the model's registry input rate.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime

import pytest

from tokenpak.cli._impl import _saved_pct, _would_have_saved_usd, run_fleet
from tokenpak.models import get_rates


def _make_monitor_db(path, *, model, estimated_cost, would_have_saved_tokens):
    conn = sqlite3.connect(str(path))
    conn.execute(
        """
        CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            model TEXT NOT NULL,
            agent_id TEXT DEFAULT '',
            input_tokens INTEGER DEFAULT 0,
            output_tokens INTEGER DEFAULT 0,
            cache_read_tokens INTEGER DEFAULT 0,
            cache_creation_tokens INTEGER DEFAULT 0,
            estimated_cost REAL DEFAULT 0,
            would_have_saved INTEGER DEFAULT 0
        )
        """
    )
    conn.execute(
        "INSERT INTO requests (timestamp, model, agent_id, input_tokens, output_tokens, "
        "estimated_cost, would_have_saved) VALUES (?, ?, 'worker-a', 2000000, 100, ?, ?)",
        (
            datetime.now().isoformat(),
            model,
            estimated_cost,
            would_have_saved_tokens,
        ),
    )
    conn.commit()
    conn.close()


def test_would_have_saved_usd_converts_tokens_via_registry_rate():
    model = "claude-sonnet-4-6"
    tokens = 1_000_000
    expected = tokens * get_rates(model)["input"] / 1_000_000
    assert _would_have_saved_usd(model, tokens) == pytest.approx(expected)
    assert _would_have_saved_usd(model, 0) == 0.0
    assert _would_have_saved_usd(model, -5) == 0.0


def test_saved_pct_uses_token_units_not_micro_dollars():
    model = "claude-sonnet-4-6"
    tokens = 1_000_000
    cost = get_rates(model)["input"]  # actual spend == value of avoided tokens
    # Correct math: saved USD == cost -> 50.0% saved.
    assert _saved_pct(cost, tokens, model=model) == "50.0%"
    # The old micro-dollar misread (tokens / 100_000 treated as dollars)
    # would have claimed a wildly different percentage.
    old_wrong = (tokens / 100_000) / (cost + tokens / 100_000) * 100
    assert f"{old_wrong:.1f}%" != "50.0%"


def test_saved_pct_edge_contract_preserved():
    # cost == 0 with savings recorded stays 'TBD' (no confident claim).
    assert _saved_pct(0.0, 12345, model="claude-sonnet-4-6") == "TBD"
    # Nothing spent, nothing saved.
    assert _saved_pct(0.0, 0, model="claude-sonnet-4-6") == "n/a"


def test_run_fleet_json_reports_token_based_savings(tmp_path, capsys):
    model = "claude-sonnet-4-6"
    rate = get_rates(model)["input"]
    tokens = 1_000_000
    cost = rate  # spend exactly the value of the avoided tokens -> 50%
    db = tmp_path / "monitor.db"
    _make_monitor_db(db, model=model, estimated_cost=cost, would_have_saved_tokens=tokens)

    run_fleet(since_days=7, as_json=True, db_path=str(db))
    payload = json.loads(capsys.readouterr().out)

    assert payload["source"] == "live_requests"
    assert payload["row_count"] == 1
    row = payload["rows"][0]
    assert row["would_have_saved"] == tokens
    assert row["would_have_saved_unit"] == "tokens"
    assert row["would_have_saved_usd"] == pytest.approx(rate, abs=1e-6)
    assert row["saved_pct"] == "50.0%"
    # Regression: the old micro-dollar read would have produced 10 "dollars"
    # from 1M tokens and a ~76.9% claim here.
    assert row["saved_pct"] != f"{(tokens / 100_000) / (cost + tokens / 100_000) * 100:.1f}%"


def test_run_fleet_table_smoke(tmp_path, capsys):
    model = "claude-sonnet-4-6"
    rate = get_rates(model)["input"]
    db = tmp_path / "monitor.db"
    _make_monitor_db(db, model=model, estimated_cost=rate, would_have_saved_tokens=1_000_000)

    run_fleet(since_days=7, as_json=False, db_path=str(db))
    out = capsys.readouterr().out
    assert "50.0%" in out
