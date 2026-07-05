# SPDX-License-Identifier: Apache-2.0
"""Launcher Codex local-database lock preflight (regression-repair packet).

Pins the in-scope launcher surface restored + hardened by the repair:
before exec-ing a real launch the launcher preflights Codex's own SQLite
databases and, on contention, waits/retries with a bounded budget rather
than letting Codex die on a raw "database is locked" error.

The wait loop's seams (prober / esc / sleep / clock / interactive) are
injected so behavior is deterministic without a real TTY, clock, or key
input.  Scope note: this does not exercise the ``codex mcp`` / ``codex
features`` probe-avoidance behavior — that lives in ``hooks.py`` /
``mcp_config.py``, outside this packet's ``expected_files_changed``.
"""
from __future__ import annotations

from tokenpak.companion.codex import launcher
from tokenpak.companion.codex import state_lock as sl


def _status(locked=False, stopped=None):
    return sl.LockStatus(
        home="/h",
        db_path="/h/state_5.sqlite",
        exists=True,
        locked=locked,
        holder_pids=[123] if locked else [],
        stopped_pids=stopped or [],
        detail="lock detail",
    )


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def tick(self, dt):
        self.t += dt


# ── preflight verdicts ─────────────────────────────────────────────────


def test_preflight_clear_home_proceeds():
    rc = launcher._preflight_state_lock(
        prober=lambda: _status(locked=False),
        interactive=False,
        sleep=lambda s: None,
        monotonic=_Clock(),
    )
    assert rc is None


def test_preflight_stopped_holder_short_circuits(capsys):
    rc = launcher._preflight_state_lock(
        prober=lambda: _status(locked=True, stopped=[999]),
        interactive=False,
        sleep=lambda s: None,
        monotonic=_Clock(),
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "locked" in err.lower()
    # A suspended holder never releases → we do not print the waiting banner.
    assert "Waiting to connect" not in err


def test_preflight_live_holder_clears_within_wait():
    clock = _Clock()
    seq = iter([_status(locked=True), _status(locked=True), _status(locked=False)])
    rc = launcher._preflight_state_lock(
        prober=lambda: next(seq),
        interactive=False,
        timeout_s=30,
        poll_interval_s=0.5,
        sleep=clock.tick,
        monotonic=clock,
    )
    assert rc is None


def test_preflight_noninteractive_times_out(capsys):
    clock = _Clock()
    rc = launcher._preflight_state_lock(
        prober=lambda: _status(locked=True),
        interactive=False,
        timeout_s=2.0,
        poll_interval_s=0.5,
        sleep=clock.tick,
        monotonic=clock,
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "still locked after" in err


def test_preflight_tty_esc_cancels(capsys):
    clock = _Clock()
    rc = launcher._preflight_state_lock(
        prober=lambda: _status(locked=True),
        interactive=True,
        timeout_s=30,
        poll_interval_s=0.5,
        esc_pressed=lambda: True,
        sleep=clock.tick,
        monotonic=clock,
    )
    assert rc == 130
    err = capsys.readouterr().err
    assert "Press Esc to cancel" in err
    assert "cancelled" in err.lower()


def test_preflight_stopped_holder_appears_mid_wait():
    clock = _Clock()
    seq = iter([_status(locked=True), _status(locked=True, stopped=[7])])
    rc = launcher._preflight_state_lock(
        prober=lambda: next(seq),
        interactive=False,
        timeout_s=30,
        poll_interval_s=0.5,
        sleep=clock.tick,
        monotonic=clock,
    )
    assert rc == 1


# ── main() wires the preflight before exec, and --install-only skips it ─


class _FakeConfig:
    def __init__(self, journal_dir):
        self.journal_dir = journal_dir
        self.hooks_enabled = False
        self.profile = "balanced"
        self.budget_daily_usd = 0.0

    def profile_overrides(self):
        return None


def _stub_setup(monkeypatch, tmp_path):
    """Stub the launcher's setup steps so main() reaches the preflight."""
    from tokenpak.companion.codex import (
        agents_md,
        mcp_config,
        rates_snapshot,
        skills_installer,
    )

    cfg = _FakeConfig(tmp_path / "journal")
    monkeypatch.setattr(
        launcher.CompanionConfig, "from_env", classmethod(lambda cls: cfg)
    )
    monkeypatch.setattr(rates_snapshot, "refresh", lambda: tmp_path / "rates.json")
    monkeypatch.setattr(mcp_config, "get_env_vars", lambda config: {})
    monkeypatch.setattr(mcp_config, "register", lambda env_vars=None: True)
    monkeypatch.setattr(agents_md, "install_agents_md", lambda target="global": tmp_path / "AGENTS.md")
    monkeypatch.setattr(skills_installer, "install_skills", lambda target_dir=None: [])


def test_main_aborts_when_preflight_blocks(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda: 7)

    def _no_exec(*a, **k):
        raise AssertionError("must not exec when preflight blocks")

    monkeypatch.setattr(launcher.os, "execvpe", _no_exec)
    assert launcher.main([]) == 7


def test_main_install_only_skips_preflight(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    called = {"n": 0}

    def _preflight():
        called["n"] += 1
        return None

    monkeypatch.setattr(launcher, "_preflight_state_lock", _preflight)
    rc = launcher.main(["--install-only"])
    assert rc == 0
    assert called["n"] == 0, "install-only must not run the lock preflight"


def test_main_execs_when_preflight_clear(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda: None)
    captured = {}

    class _Exec(Exception):
        pass

    def _fake_exec(file, argv, env):
        captured["file"] = file
        captured["argv"] = argv
        raise _Exec

    monkeypatch.setattr(launcher.os, "execvpe", _fake_exec)
    import pytest

    with pytest.raises(_Exec):
        launcher.main([])
    assert captured["file"] == "codex"
    assert captured["argv"][0] == "codex"
