"""Regression replays for the 2026-05-15 overnight + 2026-05-13 burst spend incidents.

These tests synthesize the per-incident pattern and assert the rolling-cap
engine blocks BEFORE cumulative damage matches the historical loss.
"""

from __future__ import annotations

import pytest

from tokenpak.proxy.spend_guard.rolling_caps import (
    RollingCapsConfig,
    check_rolling_caps,
    record_session_agent,
)
from tests.proxy.spend_guard.conftest import insert_request


def default_cfg(**overrides) -> RollingCapsConfig:
    base = RollingCapsConfig(
        enabled=True, window_seconds=3600,
        per_agent_max_cost_usd=20.0,
        per_agent_max_tokens_total=5_000_000,
        per_agent_max_cache_read_tokens=4_000_000,
        per_fleet_max_cost_usd=60.0,
        per_fleet_max_tokens_total=15_000_000,
        per_fleet_max_cache_read_tokens=12_000_000,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def test_rolling_cap_2026_05_15_overnight_regression(tmp_monitor_db):
    """Replay the 2026-05-15 overnight pattern.

    Suki cron fired 23 times in 8 hours = ~2.875 cycles/hour. Per cycle:
    input ~865k, cache_read ~5M, cost ~$8. Within a 1-hour window we see
    ~3 Suki cycles cumulating to ~$24. Per-agent cost cap ($20) should
    block by cycle 3 (when cumulative pushes over $20).

    PASS condition: cap fires at-or-before cycle 3 of an hour's run.
    """
    cfg = default_cfg()
    blocked_at = None
    for cycle in range(1, 6):  # try 5 cycles within the hour
        sid = f"suki-cycle-{cycle}"
        record_session_agent(sid, "suki")
        # Each cycle: $8 cost, ~865k input, ~5M cache_read
        # Check the cap BEFORE inserting (simulating preflight)
        breach = check_rolling_caps(
            agent_id="suki",
            projected_cost_usd=8.0,
            projected_input_tokens=865_000,
            projected_output_tokens=15_000,
            projected_cache_read_tokens=5_000_000,
            config=cfg,
            monitor_db_path=tmp_monitor_db,
        )
        if breach is not None:
            blocked_at = cycle
            print(f"  BLOCKED at cycle {cycle}: {breach.cap_dimension} "
                  f"(used={breach.used:.2f}, cap={breach.cap:.2f})")
            break
        # Not blocked yet — simulate the cycle completing + inserting its row
        insert_request(tmp_monitor_db, sid, cost=8.0,
                       input_tokens=865_000, output_tokens=15_000,
                       cache_read_tokens=5_000_000, seconds_ago=60 - cycle)
    assert blocked_at is not None, "rolling cap should have fired within 5 cycles"
    # Per packet pass criteria: block by cycle ~3
    assert blocked_at <= 3, (
        f"2026-05-15 pattern should block by cycle 3 (would have caught the "
        f"overnight incident); blocked at cycle {blocked_at}"
    )


def test_rolling_cap_2026_05_13_burst_regression(tmp_monitor_db):
    """Replay the 2026-05-13 burst spike.

    Original pattern: Suki burning ~$216/hour via rapid-fire concurrent
    cycles. Roughly $35/cycle bursts. Rolling cap at $20/agent/hour
    blocks within 1-2 cycles.

    PASS condition: cap fires within 2 cycles.
    """
    cfg = default_cfg()
    blocked_at = None
    for cycle in range(1, 5):
        sid = f"suki-burst-{cycle}"
        record_session_agent(sid, "suki")
        breach = check_rolling_caps(
            agent_id="suki",
            projected_cost_usd=35.0,  # burst cycle is bigger
            projected_input_tokens=2_000_000,
            projected_output_tokens=50_000,
            projected_cache_read_tokens=8_000_000,
            config=cfg,
            monitor_db_path=tmp_monitor_db,
        )
        if breach is not None:
            blocked_at = cycle
            print(f"  BLOCKED at cycle {cycle}: {breach.cap_dimension} "
                  f"(used={breach.used:.2f}, cap={breach.cap:.2f}, "
                  f"would_add={breach.projected_add:.2f})")
            break
        insert_request(tmp_monitor_db, sid, cost=35.0,
                       input_tokens=2_000_000, output_tokens=50_000,
                       cache_read_tokens=8_000_000, seconds_ago=60 - cycle)
    assert blocked_at is not None
    assert blocked_at <= 2, (
        f"2026-05-13 burst pattern should block within 2 cycles; "
        f"blocked at cycle {blocked_at}"
    )


def test_normal_bounded_cycle_passes(tmp_monitor_db):
    """Sanity: a normal small cycle (Aya-class evidence work) should NOT
    breach. 1 cycle at $0.50, ~100k tokens, 800k cache_read."""
    cfg = default_cfg()
    record_session_agent("aya-normal-1", "aya")
    breach = check_rolling_caps(
        agent_id="aya",
        projected_cost_usd=0.50,
        projected_input_tokens=100_000,
        projected_output_tokens=2_000,
        projected_cache_read_tokens=800_000,
        config=cfg,
        monitor_db_path=tmp_monitor_db,
    )
    assert breach is None, f"normal Aya cycle should not trip cap; got {breach}"


def test_rolling_cap_supplements_per_session_cap(tmp_monitor_db):
    """Sanity: rolling caps SUPPLEMENT, they don't REPLACE the per-session
    cap. The per-session cap lives in policy.SpendGuardConfig (e.g.
    session_block_cost_usd) and continues to be evaluated downstream.
    This test verifies the rolling cap module never touches that field.
    """
    from tokenpak.proxy.spend_guard.policy import SpendGuardConfig
    cfg = SpendGuardConfig()
    # Default value should be the legacy 0.0 (disabled by default), and
    # the new rolling-cap defaults should be live and non-zero.
    assert cfg.rolling_caps_enabled is True
    assert cfg.rolling_caps_per_agent_max_cost_usd == 20.0
    assert cfg.rolling_caps_per_fleet_max_cost_usd == 60.0
    # Setting the rolling cap does NOT change the per-session field.
    cfg.rolling_caps_per_agent_max_cost_usd = 99.0
    assert cfg.session_block_cost_usd == 0.0
