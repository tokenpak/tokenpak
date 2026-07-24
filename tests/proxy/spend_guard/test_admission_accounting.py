# SPDX-License-Identifier: Apache-2.0
"""In-flight admission accounting — closes the check-then-spend window.

Usage rows land in the monitor DB only AFTER responses, and the usage
query is cached — so without in-flight accounting, N concurrent requests
all pass the cap against the same frozen snapshot. These tests exercise
the pending-spend counter WITHOUT relying on any between-cycle cache
reset: a single burst of concurrent admissions must self-limit.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from tokenpak.proxy.spend_guard import rolling_caps as rc
from tokenpak.proxy.spend_guard.rolling_caps import (
    RollingCapsConfig,
    admit_pending_spend,
    check_rolling_caps_and_admit,
    settle_pending_spend,
)


def _cfg(**overrides) -> RollingCapsConfig:
    base = RollingCapsConfig(
        enabled=True,
        window_seconds=3600,
        per_agent_max_cost_usd=0.0,  # 0 = dimension disabled
        per_agent_max_tokens_total=0,
        per_agent_max_cache_read_tokens=0,
        per_fleet_max_cost_usd=10.0,
        per_fleet_max_tokens_total=0,
        per_fleet_max_cache_read_tokens=0,
    )
    for k, v in overrides.items():
        setattr(base, k, v)
    return base


def _mk_empty_monitor_db(tmp_path) -> str:
    db = tmp_path / "monitor.db"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER,
            output_tokens INTEGER,
            estimated_cost REAL,
            cache_read_tokens INTEGER DEFAULT 0,
            session_id TEXT DEFAULT ''
        )"""
    )
    conn.commit()
    conn.close()
    return str(db)


@pytest.fixture(autouse=True)
def _clean_state():
    rc.reset_caches_for_testing()
    yield
    rc.reset_caches_for_testing()


def _burst(db_path: str, n: int, projected_cost: float, cfg: RollingCapsConfig):
    """Fire n concurrent check+admit calls; return (tickets, breaches)."""
    barrier = threading.Barrier(n)
    results: list = [None] * n

    def worker(i: int) -> None:
        barrier.wait()
        results[i] = check_rolling_caps_and_admit(
            agent_id="agent-a",
            projected_cost_usd=projected_cost,
            projected_input_tokens=1_000,
            projected_output_tokens=100,
            projected_cache_read_tokens=0,
            config=cfg,
            monitor_db_path=db_path,
        )

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    tickets = [ticket for (breach, ticket) in results if ticket is not None]
    breaches = [breach for (breach, ticket) in results if breach is not None]
    return tickets, breaches


def test_concurrent_burst_cannot_all_pass_frozen_snapshot(tmp_path):
    """Fleet cap $10, six concurrent $4 requests, zero recorded usage.

    Without in-flight accounting every request sees the same $0 snapshot
    and all six are admitted ($24 committed against a $10 cap). With it,
    exactly two fit ($4, then $8; the third would project $12 > $10).

    Deliberately does NOT reset any cache between admissions — the
    counter must bypass the usage cache on its own.
    """
    db = _mk_empty_monitor_db(tmp_path)
    tickets, breaches = _burst(db, n=6, projected_cost=4.0, cfg=_cfg())

    assert len(tickets) == 2, (
        f"{len(tickets)} of 6 concurrent requests admitted against a $10 cap "
        f"with $4 projected each — the check-then-spend window is open"
    )
    assert len(breaches) == 4
    assert all(b.cap_dimension == "per_fleet_cost_usd" for b in breaches)
    # In-flight spend is visible in the breach's usage figure.
    assert all(b.used == pytest.approx(8.0) for b in breaches)


def test_settle_releases_headroom(tmp_path):
    db = _mk_empty_monitor_db(tmp_path)
    tickets, _ = _burst(db, n=6, projected_cost=4.0, cfg=_cfg())
    assert len(tickets) == 2

    # A third request is still blocked while the two are in flight...
    breach, ticket = check_rolling_caps_and_admit(
        agent_id="agent-a",
        projected_cost_usd=4.0,
        projected_input_tokens=1_000,
        projected_output_tokens=100,
        projected_cache_read_tokens=0,
        config=_cfg(),
        monitor_db_path=db,
    )
    assert breach is not None and ticket is None

    # ...and admitted again once the in-flight spend settles. No cache
    # reset happens here: the pending counter bypasses the cache.
    for t in tickets:
        assert settle_pending_spend(t) is True
    breach, ticket = check_rolling_caps_and_admit(
        agent_id="agent-a",
        projected_cost_usd=4.0,
        projected_input_tokens=1_000,
        projected_output_tokens=100,
        projected_cache_read_tokens=0,
        config=_cfg(),
        monitor_db_path=db,
    )
    assert breach is None and ticket is not None


def test_settle_is_idempotent():
    ticket = admit_pending_spend("agent-a", 1.0, 1_000, 0)
    assert settle_pending_spend(ticket) is True
    assert settle_pending_spend(ticket) is False
    assert settle_pending_spend(None) is False
    assert settle_pending_spend("adm_never_issued") is False


def test_unsettled_tickets_are_ttl_reclaimed(tmp_path, monkeypatch):
    """A crashed request that never settles cannot inflate the counter forever."""
    db = _mk_empty_monitor_db(tmp_path)
    monkeypatch.setattr(rc, "_INFLIGHT_TTL_SEC", 0.0)  # expire immediately
    admit_pending_spend("agent-a", 9.9, 1_000, 0)

    breach, ticket = check_rolling_caps_and_admit(
        agent_id="agent-a",
        projected_cost_usd=4.0,
        projected_input_tokens=1_000,
        projected_output_tokens=100,
        projected_cache_read_tokens=0,
        config=_cfg(),
        monitor_db_path=db,
    )
    assert breach is None and ticket is not None
