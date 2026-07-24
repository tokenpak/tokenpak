# SPDX-License-Identifier: Apache-2.0
"""Locks the attribution-clear shape of the rolling-cap 402 body.

Regression guard: the legacy 402 wording ``(agent=worker-a, used=60.15, cap=60)``
was misread as "worker-a spent $60.15" when the $60.15 was the FLEET aggregate
and ``agent=worker-a`` was merely the triggering caller. These tests assert the
dimension-aware wording + structured fields so an operator reads it correctly.
"""

from __future__ import annotations

import json

from tokenpak.proxy.spend_guard.block_response import (
    ERR_ROLLING_CAP_BLOCKED,
    build_rolling_cap_block,
)
from tokenpak.proxy.spend_guard.rolling_caps import CapBreach


def _decode(breach: CapBreach) -> dict:
    return json.loads(build_rolling_cap_block(breach).decode("utf-8"))["error"]


def _fleet_breach() -> CapBreach:
    return CapBreach(
        cap_dimension="per_fleet_cost_usd",
        agent_id="worker-a",
        window_seconds=3600,
        used=60.15,
        cap=60.0,
        projected_add=0.524,
        retry_after_seconds=1800,
    )


def _agent_breach() -> CapBreach:
    return CapBreach(
        cap_dimension="per_agent_cost_usd",
        agent_id="worker-a",
        window_seconds=3600,
        used=20.5,
        cap=20.0,
        projected_add=0.3,
        retry_after_seconds=1800,
    )


def test_fleet_breach_does_not_imply_caller_is_spender():
    err = _decode(_fleet_breach())
    msg = err["message"]
    # The misleading legacy pattern must be gone for fleet breaches.
    assert "(agent=worker-a," not in msg
    # Caller is named as the trigger, not the spender.
    assert "triggered_by=worker-a" in msg
    assert "NOT necessarily the biggest spender" in msg
    # Aggregate is explicitly labelled fleet-wide.
    assert "fleet_used" in msg
    assert "SUM of all tagged agents" in msg


def test_fleet_breach_structured_fields():
    err = _decode(_fleet_breach())
    assert err["type"] == ERR_ROLLING_CAP_BLOCKED
    assert err["scope"] == "fleet"
    assert err["triggered_by"] == "worker-a"
    assert err["fleet_used"] == 60.15
    assert err["fleet_cap"] == 60.0
    assert err["window_seconds"] == 3600
    # Legacy fields retained for backward-compat.
    assert err["agent_id"] == "worker-a"
    assert err["used"] == 60.15
    assert err["cap"] == 60.0
    assert err["projected_add"] == 0.524
    assert err["bypass_directive"] == "[TIP: allow=once]"


def test_agent_breach_keeps_spender_semantics():
    err = _decode(_agent_breach())
    assert err["scope"] == "agent"
    assert err["triggered_by"] == "worker-a"
    # For per-agent caps, the caller IS the spender — fleet_* are null.
    assert err["fleet_used"] is None
    assert err["fleet_cap"] is None
    assert "this IS worker-a's rolling usage" in err["message"]


def test_contributing_agents_included_only_when_present():
    breach = _fleet_breach()
    # Absent by default.
    assert "contributing_agents" not in _decode(breach)
    # Present when the breach carries it.
    breach.contributing_agents = [{"agent": "worker-b", "cost_usd": 41.2}]
    err = _decode(breach)
    assert err["contributing_agents"] == [{"agent": "worker-b", "cost_usd": 41.2}]
