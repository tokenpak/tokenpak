# SPDX-License-Identifier: MIT
"""tests/cli/test_per_session_policy.py

CCG-12: Unit tests for per-session policy CLI commands.

Covers:
  - cmd_session_budget_set writes max_cost to session_policies
  - cmd_session_mode_set writes mode to session_policies
  - cmd_session_route_pin writes route_provider to session_policies
  - UPSERT preserves unrelated fields when only one field is updated
  - budget set --session --max-cost delegates to per-session handler
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

# Make sure the tokenpak package is importable from the repo root.
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture()
def tmp_monitor_db(tmp_path, monkeypatch):
    """Create a temporary monitor.db with the session_policies table."""
    db_path = str(tmp_path / "monitor.db")
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE session_policies (
            session_id TEXT PRIMARY KEY,
            max_cost REAL,
            mode TEXT,
            route_provider TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()

    monkeypatch.setenv("TOKENPAK_DB", db_path)
    # Patch the CLI helper to return this path directly.
    import tokenpak.cli as _cli
    monkeypatch.setattr(_cli, "_get_monitor_db", lambda: db_path)
    return db_path


def _read_policy(db_path: str, session_id: str) -> dict | None:
    conn = sqlite3.connect(db_path)
    row = conn.execute(
        "SELECT session_id, max_cost, mode, route_provider FROM session_policies WHERE session_id = ?",
        (session_id,),
    ).fetchone()
    conn.close()
    if row:
        return {"session_id": row[0], "max_cost": row[1], "mode": row[2], "route_provider": row[3]}
    return None


class TestSessionBudgetSet:
    def test_writes_max_cost(self, tmp_monitor_db, capsys):
        from tokenpak.cli import cmd_session_budget_set
        args = SimpleNamespace(session="sess-001", max_cost=5.00)
        cmd_session_budget_set(args)
        policy = _read_policy(tmp_monitor_db, "sess-001")
        assert policy is not None
        assert policy["max_cost"] == pytest.approx(5.00)

    def test_output_line(self, tmp_monitor_db, capsys):
        from tokenpak.cli import cmd_session_budget_set
        args = SimpleNamespace(session="sess-002", max_cost=2.50)
        cmd_session_budget_set(args)
        out = capsys.readouterr().out
        assert "max_cost" in out
        assert "sess-002" in out

    def test_budget_set_delegates_when_session_and_max_cost_given(self, tmp_monitor_db, capsys):
        """budget set --session X --max-cost Y must route to per-session handler."""
        from tokenpak.cli import cmd_budget_set
        args = SimpleNamespace(
            session="sess-003",
            max_cost=3.00,
            daily=None,
            monthly=None,
            alert_at=None,
            hard_stop=None,
        )
        cmd_budget_set(args)
        policy = _read_policy(tmp_monitor_db, "sess-003")
        assert policy is not None
        assert policy["max_cost"] == pytest.approx(3.00)


class TestSessionModeSet:
    def test_writes_mode(self, tmp_monitor_db, capsys):
        from tokenpak.cli import cmd_session_mode_set
        args = SimpleNamespace(session="sess-010", mode="transparent")
        cmd_session_mode_set(args)
        policy = _read_policy(tmp_monitor_db, "sess-010")
        assert policy is not None
        assert policy["mode"] == "transparent"

    def test_writes_safe_mode(self, tmp_monitor_db):
        from tokenpak.cli import cmd_session_mode_set
        args = SimpleNamespace(session="sess-011", mode="safe")
        cmd_session_mode_set(args)
        policy = _read_policy(tmp_monitor_db, "sess-011")
        assert policy["mode"] == "safe"

    def test_writes_aggressive_mode(self, tmp_monitor_db):
        from tokenpak.cli import cmd_session_mode_set
        args = SimpleNamespace(session="sess-012", mode="aggressive")
        cmd_session_mode_set(args)
        policy = _read_policy(tmp_monitor_db, "sess-012")
        assert policy["mode"] == "aggressive"


class TestSessionRoutePin:
    def test_writes_route_provider(self, tmp_monitor_db, capsys):
        from tokenpak.cli import cmd_session_route_pin
        args = SimpleNamespace(session="sess-020", provider="anthropic")
        cmd_session_route_pin(args)
        policy = _read_policy(tmp_monitor_db, "sess-020")
        assert policy is not None
        assert policy["route_provider"] == "anthropic"

    def test_output_line(self, tmp_monitor_db, capsys):
        from tokenpak.cli import cmd_session_route_pin
        args = SimpleNamespace(session="sess-021", provider="openai")
        cmd_session_route_pin(args)
        out = capsys.readouterr().out
        assert "openai" in out
        assert "sess-021" in out


class TestUpsertPreservesFields:
    def test_mode_update_preserves_max_cost(self, tmp_monitor_db):
        """Setting mode must not null out a previously set max_cost."""
        from tokenpak.cli import cmd_session_budget_set, cmd_session_mode_set
        sid = "sess-030"
        cmd_session_budget_set(SimpleNamespace(session=sid, max_cost=7.00))
        cmd_session_mode_set(SimpleNamespace(session=sid, mode="safe"))
        policy = _read_policy(tmp_monitor_db, sid)
        assert policy["max_cost"] == pytest.approx(7.00)
        assert policy["mode"] == "safe"

    def test_route_update_preserves_mode_and_cost(self, tmp_monitor_db):
        from tokenpak.cli import cmd_session_budget_set, cmd_session_mode_set, cmd_session_route_pin
        sid = "sess-031"
        cmd_session_budget_set(SimpleNamespace(session=sid, max_cost=10.00))
        cmd_session_mode_set(SimpleNamespace(session=sid, mode="aggressive"))
        cmd_session_route_pin(SimpleNamespace(session=sid, provider="google"))
        policy = _read_policy(tmp_monitor_db, sid)
        assert policy["max_cost"] == pytest.approx(10.00)
        assert policy["mode"] == "aggressive"
        assert policy["route_provider"] == "google"
