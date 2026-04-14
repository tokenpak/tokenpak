# SPDX-License-Identifier: Apache-2.0
"""Tests for tokenpak.companion.budget.tracker — BudgetTracker + CostEstimate."""
from __future__ import annotations

import datetime
import sqlite3
from pathlib import Path

import pytest

from tokenpak.companion.budget.tracker import BudgetTracker, CostEstimate, _resolve_rates


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tracker(tmp_path: Path, budget: float = 0.0) -> BudgetTracker:
    return BudgetTracker(db_path=tmp_path / "budget.db", daily_budget=budget)


# ---------------------------------------------------------------------------
# estimate() — cost accuracy
# ---------------------------------------------------------------------------

def test_estimate_sonnet_1m_input_equals_3_usd(tmp_path):
    """1M input tokens at sonnet rate = $3.00."""
    t = _tracker(tmp_path)
    est = t.estimate(input_tokens=1_000_000, model="sonnet")
    assert est.estimated_cost_usd == pytest.approx(3.0, abs=1e-6)


def test_estimate_sonnet_full_model_name(tmp_path):
    """claude-sonnet-4-6 resolves to the same rate as 'sonnet'."""
    t = _tracker(tmp_path)
    est = t.estimate(input_tokens=1_000_000, model="claude-sonnet-4-6")
    assert est.estimated_cost_usd == pytest.approx(3.0, abs=1e-6)


def test_estimate_opus_1m_input(tmp_path):
    """1M input tokens at opus rate = $15.00."""
    t = _tracker(tmp_path)
    est = t.estimate(input_tokens=1_000_000, model="claude-opus-4-6")
    assert est.estimated_cost_usd == pytest.approx(15.0, abs=1e-6)


def test_estimate_haiku_1m_input(tmp_path):
    """1M input tokens at haiku rate = $0.80."""
    t = _tracker(tmp_path)
    est = t.estimate(input_tokens=1_000_000, model="claude-haiku-4-5")
    assert est.estimated_cost_usd == pytest.approx(0.80, abs=1e-6)


def test_estimate_zero_tokens(tmp_path):
    """Zero tokens → zero cost."""
    t = _tracker(tmp_path)
    est = t.estimate(input_tokens=0, model="sonnet")
    assert est.estimated_cost_usd == 0.0


def test_estimate_returns_cost_estimate_dataclass(tmp_path):
    """estimate() returns a CostEstimate instance."""
    t = _tracker(tmp_path)
    est = t.estimate(input_tokens=100, model="sonnet")
    assert isinstance(est, CostEstimate)


# ---------------------------------------------------------------------------
# Cache-read tokens at 10% rate
# ---------------------------------------------------------------------------

def test_estimate_cache_read_10pct_sonnet(tmp_path):
    """Cached tokens at sonnet are charged at 10% of input rate ($0.30/1M)."""
    t = _tracker(tmp_path)
    # 1M all-cached input: 1_000_000 * 0.30 / 1_000_000 = $0.30
    est = t.estimate(input_tokens=1_000_000, cached_tokens=1_000_000, model="sonnet")
    assert est.estimated_cost_usd == pytest.approx(0.30, abs=1e-6)


def test_estimate_cache_read_10pct_opus(tmp_path):
    """Cached tokens at opus rate = 10% of $15 = $1.50/1M."""
    t = _tracker(tmp_path)
    est = t.estimate(input_tokens=1_000_000, cached_tokens=1_000_000, model="claude-opus-4-6")
    assert est.estimated_cost_usd == pytest.approx(1.50, abs=1e-6)


def test_estimate_cache_read_10pct_haiku(tmp_path):
    """Cached tokens at haiku rate = 10% of $0.80 = $0.08/1M."""
    t = _tracker(tmp_path)
    est = t.estimate(input_tokens=1_000_000, cached_tokens=1_000_000, model="claude-haiku-4-5")
    assert est.estimated_cost_usd == pytest.approx(0.08, abs=1e-6)


def test_estimate_mixed_fresh_and_cached(tmp_path):
    """Fresh tokens charged at full rate, cached at 10%."""
    t = _tracker(tmp_path)
    # 500k fresh + 500k cached at sonnet
    # fresh: 500_000 * 3.0 / 1_000_000 = $1.50
    # cached: 500_000 * 0.30 / 1_000_000 = $0.15
    # total: $1.65
    est = t.estimate(input_tokens=1_000_000, cached_tokens=500_000, model="sonnet")
    assert est.estimated_cost_usd == pytest.approx(1.65, abs=1e-6)


# ---------------------------------------------------------------------------
# record() — SQLite persistence
# ---------------------------------------------------------------------------

def test_record_persists_to_db(tmp_path):
    """record() writes a row to companion_costs."""
    db_path = tmp_path / "budget.db"
    t = _tracker(tmp_path)
    t.record(input_tokens=1_000_000, output_tokens=0, model="sonnet", session_id="s1")

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute("SELECT * FROM companion_costs").fetchall()
    conn.close()
    assert len(rows) == 1


def test_record_cross_instance_retrieval(tmp_path):
    """Cost recorded by one BudgetTracker instance is readable by a new one."""
    db_path = tmp_path / "budget.db"
    t1 = BudgetTracker(db_path=db_path, daily_budget=0.0)
    t1.record(input_tokens=1_000_000, output_tokens=0, model="sonnet", session_id="s1")

    # New instance, same DB
    t2 = BudgetTracker(db_path=db_path, daily_budget=0.0)
    est = t2.estimate(input_tokens=0, model="sonnet")
    assert est.daily_total_usd == pytest.approx(3.0, abs=1e-4)


def test_record_updates_session_cost(tmp_path):
    """record() updates the in-memory session_cost."""
    t = _tracker(tmp_path)
    assert t.session_cost == 0.0
    t.record(input_tokens=1_000_000, output_tokens=0, model="sonnet")
    assert t.session_cost == pytest.approx(3.0, abs=1e-6)


def test_record_output_tokens_costed(tmp_path):
    """Output tokens are charged at output rate."""
    t = _tracker(tmp_path)
    # 1M output @ sonnet = $15.00
    t.record(input_tokens=0, output_tokens=1_000_000, model="sonnet")
    assert t.session_cost == pytest.approx(15.0, abs=1e-6)


def test_record_increments_session_requests(tmp_path):
    """record() increments session_requests counter."""
    t = _tracker(tmp_path)
    assert t.session_requests == 0
    t.record(input_tokens=100, model="sonnet")
    t.record(input_tokens=200, model="sonnet")
    assert t.session_requests == 2


# ---------------------------------------------------------------------------
# Daily total — cross-session aggregation
# ---------------------------------------------------------------------------

def test_daily_total_aggregates_across_sessions(tmp_path):
    """Two separate BudgetTracker instances (two sessions) sum correctly."""
    db_path = tmp_path / "budget.db"

    # Session 1: 1M input = $3.00
    t1 = BudgetTracker(db_path=db_path, daily_budget=0.0)
    t1.record(input_tokens=1_000_000, output_tokens=0, model="sonnet", session_id="s1")

    # Session 2: 1M input = $3.00
    t2 = BudgetTracker(db_path=db_path, daily_budget=0.0)
    t2.record(input_tokens=1_000_000, output_tokens=0, model="sonnet", session_id="s2")

    # New reader: should see $6.00 daily total
    t3 = BudgetTracker(db_path=db_path, daily_budget=0.0)
    est = t3.estimate(input_tokens=0, model="sonnet")
    assert est.daily_total_usd == pytest.approx(6.0, abs=1e-4)


def test_daily_total_excludes_yesterday(tmp_path, monkeypatch):
    """Records from yesterday are NOT counted in today's daily total."""
    db_path = tmp_path / "budget.db"

    # Write a row for yesterday directly
    yesterday = (datetime.date.today() - datetime.timedelta(days=1)).isoformat()
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS companion_costs "
        "(id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL, "
        "date TEXT NOT NULL, session_id TEXT NOT NULL DEFAULT '', "
        "model TEXT NOT NULL DEFAULT '', input_tokens INTEGER NOT NULL DEFAULT 0, "
        "cached_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, "
        "estimated_cost REAL NOT NULL DEFAULT 0.0)"
    )
    conn.execute(
        "INSERT INTO companion_costs (timestamp, date, session_id, model, "
        "input_tokens, cached_tokens, output_tokens, estimated_cost) "
        "VALUES (?, ?, '', 'sonnet', 1000000, 0, 0, 3.0)",
        (0.0, yesterday),
    )
    conn.commit()
    conn.close()

    t = BudgetTracker(db_path=db_path, daily_budget=0.0)
    est = t.estimate(input_tokens=0, model="sonnet")
    assert est.daily_total_usd == pytest.approx(0.0, abs=1e-4)


# ---------------------------------------------------------------------------
# over_budget detection
# ---------------------------------------------------------------------------

def test_over_budget_flag_when_estimate_exceeds_remaining(tmp_path):
    """over_budget=True when daily_total + estimated_cost > daily_budget."""
    db_path = tmp_path / "budget.db"
    # Record $4.00 already spent (budget is $5.00)
    t1 = BudgetTracker(db_path=db_path, daily_budget=5.0)
    t1.record(
        input_tokens=0,
        output_tokens=0,
        model="sonnet",
        session_id="s1",
    )
    # Manually insert a $4.00 entry
    conn = sqlite3.connect(str(db_path))
    today = datetime.date.today().isoformat()
    conn.execute(
        "INSERT INTO companion_costs (timestamp, date, session_id, model, "
        "input_tokens, cached_tokens, output_tokens, estimated_cost) "
        "VALUES (?, ?, '', 'sonnet', 0, 0, 0, 4.0)",
        (0.0, today),
    )
    conn.commit()
    conn.close()

    # Now estimate $2.00 more (4 + 2 = 6 > 5 budget)
    t2 = BudgetTracker(db_path=db_path, daily_budget=5.0)
    # 666667 tokens @ $3/1M ≈ $2.00
    est = t2.estimate(input_tokens=666_667, model="sonnet")
    assert est.over_budget is True


def test_over_budget_false_when_within_budget(tmp_path):
    """over_budget=False when there is budget remaining."""
    t = _tracker(tmp_path, budget=10.0)
    est = t.estimate(input_tokens=1_000_000, model="sonnet")  # $3 of $10 budget
    assert est.over_budget is False


def test_over_budget_false_when_no_budget_set(tmp_path):
    """over_budget=False when daily_budget=0 (unlimited)."""
    t = _tracker(tmp_path, budget=0.0)
    t.record(input_tokens=10_000_000, output_tokens=0, model="sonnet")
    est = t.estimate(input_tokens=10_000_000, model="sonnet")
    assert est.over_budget is False


def test_budget_remaining_decreases_after_record(tmp_path):
    """budget_remaining decreases as costs are recorded."""
    db_path = tmp_path / "budget.db"
    t1 = BudgetTracker(db_path=db_path, daily_budget=10.0)
    t1.record(input_tokens=1_000_000, output_tokens=0, model="sonnet")  # $3.00

    t2 = BudgetTracker(db_path=db_path, daily_budget=10.0)
    est = t2.estimate(input_tokens=0, model="sonnet")
    assert est.budget_remaining_usd == pytest.approx(7.0, abs=1e-4)


# ---------------------------------------------------------------------------
# _resolve_rates() — model name resolution
# ---------------------------------------------------------------------------

def test_resolve_exact_match():
    """Exact model name resolves to correct rates."""
    rates = _resolve_rates("claude-sonnet-4-6")
    assert rates["input"] == 3.0
    assert rates["output"] == 15.0
    assert rates["cached"] == 0.30


def test_resolve_versioned_suffix():
    """Versioned model name (date suffix) resolves correctly."""
    rates = _resolve_rates("claude-sonnet-4-6-20251022")
    assert rates["input"] == 3.0


def test_resolve_short_form():
    """Short-form 'sonnet' key resolves correctly."""
    rates = _resolve_rates("sonnet")
    assert rates["input"] == 3.0


def test_resolve_unknown_model_defaults_to_sonnet():
    """Unknown model defaults to sonnet rates."""
    rates = _resolve_rates("claude-unknown-99-99")
    assert rates["input"] == 3.0
