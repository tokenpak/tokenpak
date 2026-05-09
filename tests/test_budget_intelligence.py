"""Tests for Budget Intelligence (Pro+ burn rate, ETA, trend, suggestions)."""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest

# TSR-05w Pro-tier speculative-contract skip reason (grep-able)
# ─────────────────────────────────────────────
# 3rd recurrence in the WS-E sweep — same pattern as TSR-05u (#134) and
# TSR-05v (#135). Tests below patch
# `tokenpak.infrastructure.license_activation.is_pro` — that path never
# existed in OSS:
#   - `git log -S 'def is_pro' -- tokenpak/`         → 0 hits
#   - `find tokenpak -path '*/infrastructure/*'`     → 0 results
# Pro-tier license activation lives in the closed-source `tokenpak-paid`
# daemon per Std 25; OSS doesn't carry an `is_pro` symbol.
#
# If a 4th recurrence appears, this should be promoted to a project-wide
# pytest marker (e.g. `@pytest.mark.requires_tokenpak_paid`) registered
# in conftest.py with auto-skip when the OSS-only flag is set.
SKIP_PRO_TIER_INFRASTRUCTURE_NOT_IN_OSS = (
    "Test patches `tokenpak.infrastructure.license_activation.is_pro` "
    "— that path never existed in OSS (Pro-tier license activation "
    "lives in closed-source tokenpak-paid per Std 25). Speculative "
    "contract; same Path B pattern as TSR-05u / TSR-05v / TSR-05r / TSR-05b."
)


from tokenpak.cli.commands.budget import (
    _calc_burn_rate,
    _calc_depletion_eta,
    _generate_suggestions,
    print_budget_intelligence,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _make_history(n_days: int, daily_cost: float) -> list[dict]:
    """Synthetic daily history with fixed cost per day."""
    today = date.today()
    return [
        {"day": (today - timedelta(days=i)).isoformat(), "requests": 10, "cost_usd": daily_cost}
        for i in range(n_days - 1, -1, -1)
    ]


# ---------------------------------------------------------------------------
# Burn rate calculation
# ---------------------------------------------------------------------------

def test_burn_rate_daily_avg():
    """Daily avg from 7d history should equal total / days."""
    history_7d = _make_history(7, 1.00)  # $1/day → avg = $1
    history_30d = _make_history(30, 1.00)
    with patch(
        "tokenpak.cli.commands.budget._budget_history",
        side_effect=lambda days=30: history_7d if days == 7 else history_30d,
    ):
        burn = _calc_burn_rate()
    assert abs(burn["daily_avg_7d"] - 1.0) < 0.001
    assert abs(burn["weekly_avg"] - 7.0) < 0.001


def test_burn_rate_trend_increasing():
    """If last 7d spend is higher than prior 7d, trend should be positive."""
    today = date.today()
    # Last 7 days: $2/day; Prior 7 days: $1/day
    def fake_history(days=30):
        rows = []
        for i in range(days - 1, -1, -1):
            d = today - timedelta(days=i)
            cost = 2.0 if i < 7 else 1.0
            rows.append({"day": d.isoformat(), "requests": 5, "cost_usd": cost})
        return rows

    with patch("tokenpak.cli.commands.budget._budget_history", side_effect=fake_history):
        burn = _calc_burn_rate()
    assert burn["trend_7d_pct"] > 0, "Trend should be positive (increasing spend)"


def test_burn_rate_trend_decreasing():
    """If last 7d spend is lower than prior 7d, trend should be negative."""
    today = date.today()

    def fake_history(days=30):
        rows = []
        for i in range(days - 1, -1, -1):
            d = today - timedelta(days=i)
            cost = 0.50 if i < 7 else 2.0
            rows.append({"day": d.isoformat(), "requests": 5, "cost_usd": cost})
        return rows

    with patch("tokenpak.cli.commands.budget._budget_history", side_effect=fake_history):
        burn = _calc_burn_rate()
    assert burn["trend_7d_pct"] < 0, "Trend should be negative (decreasing spend)"


# ---------------------------------------------------------------------------
# ETA calculation
# ---------------------------------------------------------------------------

def test_eta_calculation_correct():
    """ETA days should equal remaining / daily_avg."""
    burn = {
        "daily_avg_7d": 2.0,
        "trend_7d_pct": 0.0,
        "weekly_avg": 14.0,
        "monthly_projection": 60.0,
        "last7_total": 14.0,
        "prior7_total": 14.0,
        "today_usd": 2.0,
    }
    monthly_limit = 50.0
    with patch("tokenpak.cli.commands.budget._get_spent", return_value=10.0):
        eta = _calc_depletion_eta(monthly_limit, burn)
    assert eta is not None
    # remaining = 40, daily = 2.0 → 20 days
    assert abs(eta["days_remaining"] - 20.0) < 0.5
    assert eta["eta_date"] is not None


def test_eta_returns_none_when_no_limit():
    """Without a monthly limit, ETA should be None."""
    burn = {"daily_avg_7d": 1.0, "trend_7d_pct": 0.0}
    eta = _calc_depletion_eta(None, burn)
    assert eta is None


def test_eta_zero_burn_rate():
    """Zero burn rate should not raise, should return days_remaining=None."""
    burn = {"daily_avg_7d": 0.0, "trend_7d_pct": 0.0}
    with patch("tokenpak.cli.commands.budget._get_spent", return_value=5.0):
        eta = _calc_depletion_eta(50.0, burn)
    assert eta is not None
    assert eta["days_remaining"] is None


# ---------------------------------------------------------------------------
# Suggestions
# ---------------------------------------------------------------------------

def test_suggestions_generated_for_expensive_model():
    """Expensive model in breakdown should trigger a switch suggestion."""
    burn = {"daily_avg_7d": 0.50, "trend_7d_pct": 5.0}
    model_breakdown = [
        {"model": "claude-sonnet-4", "requests": 100, "total_cost": 3.50, "daily_avg": 0.50}
    ]
    suggestions = _generate_suggestions(burn, model_breakdown)
    assert len(suggestions) >= 1
    assert any("haiku" in s.lower() for s in suggestions)


def test_suggestions_trend_warning():
    """High trend spike should generate a warning suggestion."""
    burn = {"daily_avg_7d": 0.10, "trend_7d_pct": 35.0}
    suggestions = _generate_suggestions(burn, [])
    assert any("35.0%" in s or "trend" in s.lower() or "up" in s.lower() for s in suggestions)


def test_suggestions_max_three():
    """Never return more than 3 suggestions."""
    burn = {"daily_avg_7d": 1.0, "trend_7d_pct": 50.0}
    model_breakdown = [
        {"model": f"claude-opus-{i}", "requests": 50, "total_cost": 2.0, "daily_avg": 0.30}
        for i in range(10)
    ]
    suggestions = _generate_suggestions(burn, model_breakdown)
    assert len(suggestions) <= 3


# ---------------------------------------------------------------------------
# print_budget_intelligence — non-Pro gate
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason=SKIP_PRO_TIER_INFRASTRUCTURE_NOT_IN_OSS)
def test_intelligence_gated_non_pro(capsys):
    """Non-Pro license should print an upgrade prompt and not show data."""
    with patch("tokenpak.infrastructure.license_activation.is_pro", return_value=False):
        print_budget_intelligence()
    captured = capsys.readouterr()
    assert "Pro" in captured.out or "license" in captured.out.lower()


@pytest.mark.skip(reason=SKIP_PRO_TIER_INFRASTRUCTURE_NOT_IN_OSS)
def test_intelligence_json_output(capsys):
    """--json mode should produce parseable JSON with required keys."""
    burn = {
        "daily_avg_7d": 1.0,
        "weekly_avg": 7.0,
        "monthly_projection": 30.0,
        "trend_7d_pct": 5.0,
        "last7_total": 7.0,
        "prior7_total": 6.65,
        "today_usd": 1.0,
    }
    with (
        patch("tokenpak.infrastructure.license_activation.is_pro", return_value=True),
        patch("tokenpak.cli.commands.budget._load_config", return_value={"monthly_limit_usd": 50}),
        patch("tokenpak.cli.commands.budget._get_spent", return_value=10.0),
        patch("tokenpak.cli.commands.budget._calc_burn_rate", return_value=burn),
        patch("tokenpak.cli.commands.budget._get_model_daily_avg", return_value=[]),
        patch("tokenpak.cli.commands.budget._generate_suggestions", return_value=["test suggestion"]),
    ):
        print_budget_intelligence(raw=True)
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "burn_rate" in data
    assert "depletion_eta" in data
    assert "suggestions" in data
    assert "trend_7d_pct" in data
