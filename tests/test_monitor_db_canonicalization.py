"""Tests for monitor.db reader canonicalization.

Verifies that the previously-hardcoded readers now resolve the monitor DB
through ``tokenpak._paths`` instead of literal legacy paths, so every reader
agrees on the same store and none defaults to the pre-dot ``~/tokenpak/``
location.
"""

from __future__ import annotations

import importlib
import sqlite3

import pytest


def _make_monitor_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute(
        "CREATE TABLE requests (id INTEGER PRIMARY KEY, timestamp TEXT, model TEXT)"
    )
    conn.execute(
        "INSERT INTO requests (timestamp, model) VALUES ('2026-06-01T00:00:00', 'm')"
    )
    conn.commit()
    conn.close()


def test_canonical_resolves_to_tpk(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    canonical = tmp_path / ".tpk" / "monitor.db"
    _make_monitor_db(canonical)

    from tokenpak import _paths

    importlib.reload(_paths)
    resolved = _paths.monitor_db(mode="read")
    assert resolved is not None
    assert resolved.resolve() == canonical.resolve()


def test_impl_resolver_no_legacy_predot_default(tmp_path, monkeypatch):
    # With no DB anywhere, the resolver default must be canonical ~/.tpk, never
    # the legacy ~/tokenpak/monitor.db that the hardcoded reader used to return.
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.delenv("TOKENPAK_DB", raising=False)

    from tokenpak.cli import _impl

    importlib.reload(_impl)
    resolved = _impl._resolve_db_path()
    assert resolved.endswith(".tpk/monitor.db")
    assert "/tokenpak/monitor.db" not in resolved  # no pre-dot legacy default


def test_impl_resolver_finds_canonical_db(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.delenv("TOKENPAK_DB", raising=False)
    canonical = tmp_path / ".tpk" / "monitor.db"
    _make_monitor_db(canonical)

    from tokenpak import _paths
    from tokenpak.cli import _impl

    importlib.reload(_paths)
    importlib.reload(_impl)
    assert _impl._resolve_db_path() == str(canonical)


@pytest.mark.parametrize("modname", [
    "tokenpak.cli.commands.budget",
    "tokenpak.cli.commands.optimize",
])
def test_command_default_monitor_db_is_canonical(modname, tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.delenv("TOKENPAK_DB", raising=False)

    mod = importlib.import_module(modname)
    importlib.reload(mod)
    # No legacy ~/.tokenpak/data default; resolver-backed canonical instead.
    assert mod._MONITOR_DB.endswith(".tpk/monitor.db")
    assert ".tokenpak/data/monitor.db" not in mod._MONITOR_DB


def test_command_monitor_db_respects_env_override(tmp_path, monkeypatch):
    custom = tmp_path / "custom.db"
    monkeypatch.setenv("TOKENPAK_DB", str(custom))

    import tokenpak.cli.commands.budget as budget

    importlib.reload(budget)
    assert budget._MONITOR_DB == str(custom)


def test_doctor_claude_code_paths_are_canonical(tmp_path, monkeypatch):
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.delenv("TOKENPAK_DB", raising=False)

    from tokenpak import _paths
    from tokenpak.cli.commands import doctor_claude_code as dcc

    importlib.reload(_paths)
    importlib.reload(dcc)
    # No active DB -> canonical fresh-install path, not legacy ~/.tokenpak.
    assert str(dcc._monitor_db_path()).endswith(".tpk/monitor.db")
    # pid path follows the resolved home (canonical when no legacy exists)
    assert str(dcc._proxy_pid_path()).endswith(".tpk/proxy.pid")
