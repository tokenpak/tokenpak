# SPDX-License-Identifier: Apache-2.0
"""Rolling-cap measurement failure semantics + usage-DB path resolution.

The caps are only as good as the measurement feeding them. These tests lock:

- usage DB present but unreadable (corrupt/locked) → BLOCK (fail closed)
  with a loud operator-actionable warning naming the DB path;
- usage DB missing entirely (fresh install) → allow, info-logged;
- the caps read the resolver-chosen usage DB (the file the proxy actually
  writes), not a hardcoded legacy path.
"""

from __future__ import annotations

import json
import logging
import sqlite3

import pytest

from tokenpak.proxy.spend_guard import rolling_caps as rc
from tokenpak.proxy.spend_guard.block_response import build_rolling_cap_block
from tokenpak.proxy.spend_guard.rolling_caps import (
    CAP_DIMENSION_UNMEASURABLE,
    RollingCapsConfig,
    check_rolling_caps,
    compute_rolling_usage,
)


def _cfg(**overrides) -> RollingCapsConfig:
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


def _mk_valid_db(path, rows=()):
    conn = sqlite3.connect(str(path))
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
    for ts, cost, session_id in rows:
        conn.execute(
            "INSERT INTO requests (timestamp, model, input_tokens, output_tokens,"
            " estimated_cost, session_id) VALUES (?, 'm', 1000, 100, ?, ?)",
            (ts, cost, session_id),
        )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _clean_caches():
    rc.reset_caches_for_testing()
    yield
    rc.reset_caches_for_testing()


# ---------------------------------------------------------------------------
# Corrupt / unreadable usage DB → fail closed
# ---------------------------------------------------------------------------


def test_corrupt_usage_db_blocks_and_warns(tmp_path, caplog, capsys):
    corrupt = tmp_path / "monitor.db"
    corrupt.write_bytes(b"this is not a sqlite database " * 8)

    with caplog.at_level(logging.WARNING):
        breach = check_rolling_caps(
            agent_id="agent-a",
            projected_cost_usd=0.10,
            projected_input_tokens=100,
            projected_output_tokens=10,
            projected_cache_read_tokens=0,
            config=_cfg(),
            monitor_db_path=str(corrupt),
        )

    assert breach is not None, "corrupt usage DB must block, not evaluate vs $0"
    assert breach.cap_dimension == CAP_DIMENSION_UNMEASURABLE
    # Loud + operator-actionable: names the DB path, in the log AND on stderr.
    assert str(corrupt) in caplog.text
    assert "UNMEASURABLE" in caplog.text
    err = capsys.readouterr().err
    assert "tokenpak: WARN" in err
    assert str(corrupt) in err


def test_unmeasurable_block_body_is_structured(tmp_path):
    corrupt = tmp_path / "monitor.db"
    corrupt.write_bytes(b"\x00garbage\x00" * 32)
    breach = check_rolling_caps(
        agent_id="agent-a",
        projected_cost_usd=0.10,
        projected_input_tokens=100,
        projected_output_tokens=10,
        projected_cache_read_tokens=0,
        config=_cfg(),
        monitor_db_path=str(corrupt),
    )
    payload = json.loads(build_rolling_cap_block(breach).decode("utf-8"))["error"]
    assert payload["type"] == "tokenpak_spend_guard_rolling_cap_blocked"
    assert payload["cap_dimension"] == CAP_DIMENSION_UNMEASURABLE
    assert "unreadable" in payload["message"]
    assert "rolling_cap_unmeasurable" in payload["message"]


def test_compute_returns_none_on_unreadable_db(tmp_path):
    corrupt = tmp_path / "monitor.db"
    corrupt.write_bytes(b"not sqlite")
    assert compute_rolling_usage("agent-a", 3600, monitor_db_path=str(corrupt)) is None


# ---------------------------------------------------------------------------
# Missing usage DB on a fresh install → allow
# ---------------------------------------------------------------------------


def test_missing_usage_db_fresh_install_allows(tmp_path, caplog):
    missing = tmp_path / "never-created" / "monitor.db"
    with caplog.at_level(logging.INFO):
        breach = check_rolling_caps(
            agent_id="agent-a",
            projected_cost_usd=0.10,
            projected_input_tokens=100,
            projected_output_tokens=10,
            projected_cache_read_tokens=0,
            config=_cfg(),
            monitor_db_path=str(missing),
        )
    assert breach is None, "fresh install (no usage DB) must allow"
    assert "fresh install" in caplog.text


# ---------------------------------------------------------------------------
# Resolver-chosen path regression: caps must read the DB the proxy writes
# ---------------------------------------------------------------------------


def test_caps_read_resolver_chosen_usage_db(tmp_path, monkeypatch):
    """With no explicit path, caps must follow the shared path resolver.

    Regression for the hardcoded-legacy-path bug: the proxy wrote usage to
    the resolver-chosen DB while the caps read a hardcoded legacy location,
    so every cap evaluated against $0 on canonical installs.
    """
    assert rc._DEFAULT_MONITOR_DB == rc._LEGACY_DEFAULT_MONITOR_DB, (
        "this test requires the unpatched default so the resolver engages"
    )
    resolver_db = tmp_path / "resolver" / "monitor.db"
    resolver_db.parent.mkdir(parents=True)
    import datetime as dt

    now_iso = dt.datetime.now().isoformat()
    _mk_valid_db(resolver_db, rows=[(now_iso, 59.90, "sess-big")])
    # The shared resolver honors this env var as its first candidate.
    # HOME is redirected so the developer machine's real DBs (later
    # candidates) can never leak into the test.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TOKENPAK_DB", str(resolver_db))

    usage = compute_rolling_usage("agent-a", 3600, monitor_db_path=None)
    assert usage is not None
    assert usage["fleet_cost_usd"] == pytest.approx(59.90), (
        "caps did not read the resolver-chosen usage DB"
    )

    breach = check_rolling_caps(
        agent_id="agent-a",
        projected_cost_usd=0.50,
        projected_input_tokens=1000,
        projected_output_tokens=100,
        projected_cache_read_tokens=0,
        config=_cfg(),
        monitor_db_path=None,
    )
    assert breach is not None
    assert breach.cap_dimension == "per_fleet_cost_usd"


def test_existing_but_invalid_resolver_candidate_fails_closed(tmp_path, monkeypatch):
    """A corrupt DB at the resolver path must NOT be misread as fresh install."""
    assert rc._DEFAULT_MONITOR_DB == rc._LEGACY_DEFAULT_MONITOR_DB
    corrupt = tmp_path / "monitor.db"
    corrupt.write_bytes(b"corrupt beyond the resolver's validity check " * 8)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("TOKENPAK_DB", str(corrupt))

    breach = check_rolling_caps(
        agent_id="agent-a",
        projected_cost_usd=0.10,
        projected_input_tokens=100,
        projected_output_tokens=10,
        projected_cache_read_tokens=0,
        config=_cfg(),
        monitor_db_path=None,
    )
    assert breach is not None
    assert breach.cap_dimension == CAP_DIMENSION_UNMEASURABLE
