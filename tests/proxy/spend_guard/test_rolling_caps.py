"""Unit tests for rolling-cap evaluation.

Covers the 9 unit-test cases from
the rolling-cap design.
"""

from __future__ import annotations

import json

import pytest

from tests.proxy.spend_guard.conftest import insert_request
from tokenpak.proxy.spend_guard.rolling_caps import (
    CapBreach,
    RollingCapsConfig,
    check_rolling_caps,
    compute_rolling_usage,
    record_session_agent,
)

# ----- Configurations used across multiple tests -----


def default_cfg(**overrides) -> RollingCapsConfig:
    base = RollingCapsConfig(
        enabled=True,
        window_seconds=3600,
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


# ----- 1. per-agent cost cap fires when projection pushes over -----


def test_rolling_cap_per_agent_cost_blocks(tmp_monitor_db):
    """Agent agent-a accumulated $19.95 in the window; new request at $0.30
    would push to $20.25 → block on per_agent_cost_usd cap (=$20.0)."""
    for i in range(20):
        sid = f"agent-a-session-{i}"
        record_session_agent(sid, "agent-a")
        # 20 sessions × ~$1.00 each = ~$20 right at the cap edge
        insert_request(tmp_monitor_db, sid, cost=1.0, seconds_ago=60 + i)
    # Pre-check actual rolling sum to make the assertion deterministic
    usage = compute_rolling_usage("agent-a", 3600, monitor_db_path=tmp_monitor_db)
    assert 19.0 <= usage["agent_cost_usd"] <= 21.0, f"unexpected pre-check usage: {usage}"
    # Now project a new request at $0.30 that should breach
    breach = check_rolling_caps(
        agent_id="agent-a",
        projected_cost_usd=0.30,
        projected_input_tokens=1000,
        projected_output_tokens=100,
        projected_cache_read_tokens=0,
        config=default_cfg(),
        monitor_db_path=tmp_monitor_db,
    )
    assert breach is not None
    assert breach.cap_dimension == "per_agent_cost_usd"
    assert breach.agent_id == "agent-a"
    assert breach.cap == 20.0


# ----- 2. per-agent below threshold passes -----


def test_rolling_cap_per_agent_below_threshold(tmp_monitor_db):
    """Agent agent-a accumulated $18.00; new request at $0.50 → $18.50 still
    under $20.0 → no breach."""
    for i in range(18):
        sid = f"agent-a-low-{i}"
        record_session_agent(sid, "agent-a")
        insert_request(tmp_monitor_db, sid, cost=1.0, seconds_ago=60 + i)
    breach = check_rolling_caps(
        agent_id="agent-a",
        projected_cost_usd=0.50,
        projected_input_tokens=1000,
        projected_output_tokens=100,
        projected_cache_read_tokens=0,
        config=default_cfg(),
        monitor_db_path=tmp_monitor_db,
    )
    assert breach is None


# ----- 3. per-fleet cost cap fires -----


def test_rolling_cap_per_fleet_cost_blocks(tmp_monitor_db):
    """3 different agents each at ~$19 → fleet total ~$57; new $4 → breach."""
    for agent, cost_each in [("agent-a", 19.0), ("agent-b", 19.0), ("agent-c", 19.0)]:
        for i in range(19):
            sid = f"{agent}-session-{i}"
            record_session_agent(sid, agent)
            insert_request(tmp_monitor_db, sid, cost=1.0, seconds_ago=60 + i)
    breach = check_rolling_caps(
        agent_id="agent-a",
        projected_cost_usd=4.0,  # would push fleet to ~$61 > $60 cap
        projected_input_tokens=1000,
        projected_output_tokens=100,
        projected_cache_read_tokens=0,
        config=default_cfg(),
        monitor_db_path=tmp_monitor_db,
    )
    assert breach is not None
    # Could fire per-agent first (per ordering) if any agent is over; in
    # this synthesis agent-a is at $19, fleet at $57; +$4 puts agent at $23
    # (over $20). So per_agent_cost_usd fires first.
    assert breach.cap_dimension in ("per_agent_cost_usd", "per_fleet_cost_usd")


# ----- 3b. fleet cap fires when no agent is at risk individually -----


def test_rolling_cap_per_fleet_cost_blocks_when_agents_low(tmp_monitor_db):
    """5 agents each at $15 → fleet $75 > $60 cap, but each agent under $20."""
    cfg = default_cfg(per_agent_max_cost_usd=20.0, per_fleet_max_cost_usd=60.0)
    for agent in ["agent-a", "agent-b", "agent-c", "agent-d", "agent-e"]:
        for i in range(15):
            sid = f"{agent}-fleet-{i}"
            record_session_agent(sid, agent)
            insert_request(tmp_monitor_db, sid, cost=1.0, seconds_ago=60 + i)
    breach = check_rolling_caps(
        agent_id="agent-e",  # at $15 individually, fleet at $75
        projected_cost_usd=0.10,
        projected_input_tokens=100,
        projected_output_tokens=10,
        projected_cache_read_tokens=0,
        config=cfg,
        monitor_db_path=tmp_monitor_db,
    )
    assert breach is not None
    assert breach.cap_dimension == "per_fleet_cost_usd"


# ----- 4. per-agent token total cap -----


def test_rolling_cap_per_agent_tokens_blocks(tmp_monitor_db):
    """Agent at 4,900,000 tokens; +200k → 5.1M > 5.0M cap."""
    for i in range(10):
        sid = f"agent-a-tok-{i}"
        record_session_agent(sid, "agent-a")
        # 490k tokens/row × 10 = 4.9M
        insert_request(
            tmp_monitor_db,
            sid,
            cost=0.01,
            input_tokens=400_000,
            output_tokens=90_000,
            cache_read_tokens=0,
            seconds_ago=60 + i,
        )
    breach = check_rolling_caps(
        agent_id="agent-a",
        projected_cost_usd=0.01,
        projected_input_tokens=150_000,
        projected_output_tokens=50_000,
        projected_cache_read_tokens=0,
        config=default_cfg(),
        monitor_db_path=tmp_monitor_db,
    )
    assert breach is not None
    assert breach.cap_dimension == "per_agent_tokens_total"


# ----- 5. per-agent cache_read cap -----


def test_rolling_cap_per_agent_cache_read_blocks(tmp_monitor_db):
    """Agent has 3.9M cache_read; +200k cache_read → 4.1M > 4.0M cap."""
    for i in range(10):
        sid = f"agent-a-cr-{i}"
        record_session_agent(sid, "agent-a")
        insert_request(
            tmp_monitor_db,
            sid,
            cost=0.01,
            input_tokens=1000,
            output_tokens=100,
            cache_read_tokens=390_000,
            seconds_ago=60 + i,
        )
    # Use a relaxed token-total cap so it doesn't fire first
    cfg = default_cfg(per_agent_max_tokens_total=20_000_000)
    breach = check_rolling_caps(
        agent_id="agent-a",
        projected_cost_usd=0.01,
        projected_input_tokens=1000,
        projected_output_tokens=100,
        projected_cache_read_tokens=200_000,
        config=cfg,
        monitor_db_path=tmp_monitor_db,
    )
    assert breach is not None
    assert breach.cap_dimension == "per_agent_cache_read_tokens"


# ----- 6. window aging — rows outside the window don't count -----


def test_rolling_cap_window_aging(tmp_monitor_db):
    """Old rows outside the 1h window must not count toward the rolling sum."""
    # 30 rows at $1 each but 2 hours old → outside 1h window
    for i in range(30):
        sid = f"agent-a-old-{i}"
        record_session_agent(sid, "agent-a")
        insert_request(tmp_monitor_db, sid, cost=1.0, seconds_ago=7200 + i)
    breach = check_rolling_caps(
        agent_id="agent-a",
        projected_cost_usd=1.0,  # well under $20 if nothing else counts
        projected_input_tokens=1000,
        projected_output_tokens=100,
        projected_cache_read_tokens=0,
        config=default_cfg(),
        monitor_db_path=tmp_monitor_db,
    )
    assert breach is None, f"old rows should have aged out; got breach: {breach}"


# ----- 7. unknown agent (no header) — only fleet cap applies -----


def test_rolling_cap_unknown_agent(tmp_monitor_db):
    """Request with no agent_id → per-agent caps SKIPPED; fleet still applies."""
    # Big fleet-wide spend; unmapped session
    for i in range(70):
        # Note: no record_session_agent here — these are 'unattributed'
        sid = f"orphan-{i}"
        insert_request(tmp_monitor_db, sid, cost=1.0, seconds_ago=60 + i)
    # No agent_id provided
    breach = check_rolling_caps(
        agent_id="",
        projected_cost_usd=0.10,
        projected_input_tokens=1000,
        projected_output_tokens=100,
        projected_cache_read_tokens=0,
        config=default_cfg(per_fleet_max_cost_usd=60.0),
        monitor_db_path=tmp_monitor_db,
    )
    assert breach is not None
    assert breach.cap_dimension == "per_fleet_cost_usd"
    assert breach.agent_id == "unknown"


# ----- 8. disabled rolling caps are a no-op -----


def test_rolling_cap_disabled(tmp_monitor_db):
    """rolling_caps.enabled=False → no breach even with massive usage."""
    for i in range(100):
        sid = f"agent-a-disabled-{i}"
        record_session_agent(sid, "agent-a")
        insert_request(tmp_monitor_db, sid, cost=10.0, seconds_ago=60 + i)
    cfg = default_cfg(enabled=False)
    breach = check_rolling_caps(
        agent_id="agent-a",
        projected_cost_usd=100.0,
        projected_input_tokens=100_000,
        projected_output_tokens=10_000,
        projected_cache_read_tokens=0,
        config=cfg,
        monitor_db_path=tmp_monitor_db,
    )
    assert breach is None


# ----- 9. zero-cap fields are treated as 'cap disabled', not 'cap=0' -----


def test_rolling_cap_zero_value_means_disabled(tmp_monitor_db):
    """A zero cap value disables that particular dimension."""
    for i in range(20):
        sid = f"agent-a-zero-{i}"
        record_session_agent(sid, "agent-a")
        insert_request(tmp_monitor_db, sid, cost=1.0, seconds_ago=60 + i)
    cfg = default_cfg(per_agent_max_cost_usd=0.0, per_fleet_max_cost_usd=0.0)
    # All caps at 0 → no breach
    breach = check_rolling_caps(
        agent_id="agent-a",
        projected_cost_usd=100.0,
        projected_input_tokens=100_000,
        projected_output_tokens=10_000,
        projected_cache_read_tokens=0,
        config=cfg,
        monitor_db_path=tmp_monitor_db,
    )
    assert breach is None
