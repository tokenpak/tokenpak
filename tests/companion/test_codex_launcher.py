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

import builtins
import errno
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path

import pytest

from tokenpak import _cli_core
from tokenpak.companion.codex import launcher
from tokenpak.companion.codex import session_home as sh
from tokenpak.companion.codex import state_lock as sl


def _status(locked=False, stopped=None):
    stopped = stopped or []
    holders = stopped if stopped else ([123] if locked else [])
    return sl.LockStatus(
        home="/h",
        db_path="/h/state_5.sqlite",
        exists=True,
        locked=locked,
        holder_pids=holders,
        stopped_pids=stopped,
        running_pids=[123] if locked and not stopped else [],
        detail="lock detail",
    )


def _preflight_result(
    status=launcher.PreflightStatus.HOLDER_TIMEOUT_LAST_VERIFIED_LIVE,
    *,
    diagnostics_complete=True,
    exit_code=1,
):
    return launcher._preflight_evaluation(
        status=status,
        diagnostics_complete=diagnostics_complete,
        holder_pids=(123,) if status is not launcher.PreflightStatus.CLEAR else (),
        holder_state="running" if status is not launcher.PreflightStatus.CLEAR else "none",
        detail="test preflight result",
        remediation="test remediation" if exit_code is not None else None,
        exit_code=exit_code,
        diagnostic_epoch="test-epoch",
    )


def _allow_temporary_session_prompt(monkeypatch):
    monkeypatch.setattr(launcher, "_stdin_is_tty", lambda: True)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("TOKENPAK_NONINTERACTIVE", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t

    def tick(self, dt):
        self.t += dt


# ── preflight verdicts ─────────────────────────────────────────────────


def test_preflight_clear_home_proceeds():
    result = launcher._preflight_state_lock(
        prober=lambda: _status(locked=False),
        interactive=False,
        sleep=lambda s: None,
        monotonic=_Clock(),
    )
    assert result.is_clear
    assert result.evidence.status is launcher.PreflightStatus.CLEAR
    assert result.exit_code is None


def test_preflight_stopped_holder_short_circuits(capsys):
    result = launcher._preflight_state_lock(
        prober=lambda: _status(locked=True, stopped=[999]),
        interactive=False,
        sleep=lambda s: None,
        monotonic=_Clock(),
    )
    assert result.evidence.status is launcher.PreflightStatus.STOPPED_HOLDER
    assert result.exit_code == 1
    assert result.fallback_decision.eligible is True
    err = capsys.readouterr().err
    assert "locked" in err.lower()
    # A suspended holder never releases → we do not print the waiting banner.
    assert "Waiting to connect" not in err


def test_preflight_live_holder_clears_within_wait():
    clock = _Clock()
    seq = iter([_status(locked=True), _status(locked=True), _status(locked=False)])
    result = launcher._preflight_state_lock(
        prober=lambda: next(seq),
        interactive=False,
        timeout_s=30,
        poll_interval_s=0.5,
        sleep=clock.tick,
        monotonic=clock,
    )
    assert result.is_clear


def test_preflight_noninteractive_times_out(capsys):
    clock = _Clock()
    result = launcher._preflight_state_lock(
        prober=lambda: _status(locked=True),
        interactive=False,
        timeout_s=2.0,
        poll_interval_s=0.5,
        sleep=clock.tick,
        monotonic=clock,
    )
    assert result.evidence.status is launcher.PreflightStatus.HOLDER_TIMEOUT_LAST_VERIFIED_LIVE
    assert result.exit_code == 1
    assert result.fallback_decision.eligible is True
    err = capsys.readouterr().err
    assert "still locked after" in err


def test_preflight_wall_timeout_bounds_a_stuck_probe(capsys):
    def stuck_probe():
        time.sleep(0.25)
        return _status(locked=False)

    started = time.monotonic()
    result = launcher._preflight_state_lock(
        prober=stuck_probe,
        interactive=False,
        timeout_s=0.05,
    )
    elapsed = time.monotonic() - started

    assert result.evidence.status is launcher.PreflightStatus.INSPECTION_INCOMPLETE
    assert result.exit_code == 1
    assert result.fallback_decision.eligible is False
    assert elapsed < 0.15
    assert "wall-time limit" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        (PermissionError("denied"), launcher.PreflightStatus.PERMISSION_ERROR),
        (OSError(errno.ENOSPC, "full"), launcher.PreflightStatus.STORAGE_ERROR),
        (RuntimeError("unexpected"), launcher.PreflightStatus.UNKNOWN_FAILURE),
    ],
)
def test_preflight_probe_failures_are_typed_and_never_fallback_eligible(failure, expected_status):
    result = launcher._preflight_state_lock(
        prober=lambda: (_ for _ in ()).throw(failure),
        interactive=False,
        timeout_s=1,
    )

    assert result.evidence.status is expected_status
    assert result.evidence.diagnostics_complete is False
    assert result.fallback_decision.eligible is False
    assert result.exit_code == 1


def test_preflight_tty_esc_cancels(capsys):
    clock = _Clock()
    result = launcher._preflight_state_lock(
        prober=lambda: _status(locked=True),
        interactive=True,
        timeout_s=30,
        poll_interval_s=0.5,
        esc_pressed=lambda: True,
        sleep=clock.tick,
        monotonic=clock,
    )
    assert result.evidence.status is launcher.PreflightStatus.CANCELLED
    assert result.exit_code == 130
    assert result.fallback_decision.eligible is False
    err = capsys.readouterr().err
    assert "Press Esc to cancel" in err
    assert "cancelled" in err.lower()


def test_preflight_stopped_holder_appears_mid_wait():
    clock = _Clock()
    seq = iter([_status(locked=True), _status(locked=True, stopped=[7])])
    result = launcher._preflight_state_lock(
        prober=lambda: next(seq),
        interactive=False,
        timeout_s=30,
        poll_interval_s=0.5,
        sleep=clock.tick,
        monotonic=clock,
    )
    assert result.evidence.status is launcher.PreflightStatus.STOPPED_HOLDER
    assert result.exit_code == 1


def test_preflight_incomplete_result_mid_wait_short_circuits(capsys):
    clock = _Clock()
    incomplete = sl.LockStatus(
        home="/h",
        db_path="/h/state_5.sqlite",
        exists=True,
        locked=True,
        detail="inspection incomplete",
        diagnostics_complete=False,
        incomplete_reasons=["probe_timeout"],
    )
    seq = iter([_status(locked=True), incomplete])

    result = launcher._preflight_state_lock(
        prober=lambda: next(seq),
        interactive=False,
        timeout_s=30,
        poll_interval_s=0.5,
        sleep=clock.tick,
        monotonic=clock,
    )

    assert result.evidence.status is launcher.PreflightStatus.INSPECTION_INCOMPLETE
    assert result.exit_code == 1
    assert result.fallback_decision.eligible is False
    assert "inspection incomplete" in capsys.readouterr().err


@pytest.mark.parametrize("answer", ["y", "Y", "yes", "YES"])
def test_temporary_session_prompt_accepts_explicit_yes(monkeypatch, capsys, answer):
    _allow_temporary_session_prompt(monkeypatch)
    monkeypatch.setattr(builtins, "input", lambda: answer)

    assert launcher._prompt_for_temporary_session() is launcher.TemporarySessionChoice.ACCEPTED
    err = capsys.readouterr().err
    assert "shared local history" in err
    assert "temporary session without that prior history" in err
    assert "[y/N]" in err


@pytest.mark.parametrize("answer", ["", "n", "no", "later"])
def test_temporary_session_prompt_defaults_to_refusal(monkeypatch, answer):
    _allow_temporary_session_prompt(monkeypatch)
    monkeypatch.setattr(builtins, "input", lambda: answer)

    assert launcher._prompt_for_temporary_session() is launcher.TemporarySessionChoice.DECLINED


@pytest.mark.parametrize("env_name", ["CI", "TOKENPAK_NONINTERACTIVE"])
def test_temporary_session_prompt_never_prompts_automation(monkeypatch, env_name):
    _allow_temporary_session_prompt(monkeypatch)
    monkeypatch.setenv(env_name, "1")
    monkeypatch.setattr(
        builtins,
        "input",
        lambda: (_ for _ in ()).throw(AssertionError("automation was prompted")),
    )

    assert launcher._prompt_for_temporary_session() is launcher.TemporarySessionChoice.NOT_AVAILABLE


def test_temporary_session_prompt_never_prompts_non_tty_or_dumb_term(monkeypatch):
    _allow_temporary_session_prompt(monkeypatch)
    monkeypatch.setattr(
        builtins,
        "input",
        lambda: (_ for _ in ()).throw(AssertionError("suppressed launch was prompted")),
    )

    monkeypatch.setattr(launcher, "_stdin_is_tty", lambda: False)
    assert launcher._prompt_for_temporary_session() is launcher.TemporarySessionChoice.NOT_AVAILABLE

    monkeypatch.setattr(launcher, "_stdin_is_tty", lambda: True)
    monkeypatch.setenv("TERM", "dumb")
    assert launcher._prompt_for_temporary_session() is launcher.TemporarySessionChoice.NOT_AVAILABLE


def test_temporary_session_prompt_ctrl_c_cancels(monkeypatch, capsys):
    _allow_temporary_session_prompt(monkeypatch)
    monkeypatch.setattr(
        builtins,
        "input",
        lambda: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    assert launcher._prompt_for_temporary_session() is launcher.TemporarySessionChoice.CANCELLED
    assert "cancelled" in capsys.readouterr().err


# ── main() wires the preflight before exec, and --install-only skips it ─


class _FakeConfig:
    def __init__(self, journal_dir):
        self.journal_dir = journal_dir
        self.hooks_enabled = False
        self.profile = "balanced"
        self.budget_daily_usd = 0.0

    def profile_overrides(self):
        return None


def _stub_session_env(monkeypatch, tmp_path: Path) -> None:
    user_home = tmp_path / "user"
    source_home = user_home / ".codex"
    source_home.mkdir(parents=True)
    (source_home / "config.toml").write_text('model = "gpt-test"\n')
    (source_home / "auth.json").write_text('{"test": true}\n')
    (source_home / "auth.json").chmod(0o600)
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "tokenpak-home"))
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "isolated")
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.setattr(launcher, "_local_proxy_is_healthy", lambda: False)
    monkeypatch.chdir(tmp_path)


def _stub_setup(monkeypatch, tmp_path):
    """Stub the launcher's setup steps so main() reaches the preflight."""
    from tokenpak.companion.codex import (
        agents_md,
        mcp_config,
        rates_snapshot,
        skills_installer,
    )

    _stub_session_env(monkeypatch, tmp_path)
    cfg = _FakeConfig(tmp_path / "journal")
    monkeypatch.setattr(launcher.CompanionConfig, "from_env", classmethod(lambda cls: cfg))
    monkeypatch.setattr(rates_snapshot, "refresh", lambda: tmp_path / "rates.json")
    monkeypatch.setattr(mcp_config, "get_env_vars", lambda config: {})
    monkeypatch.setattr(mcp_config, "_register", lambda env_vars=None, codex_home=None: True)
    monkeypatch.setattr(
        agents_md,
        "_install_agents_md",
        lambda target="global", codex_home=None: Path(codex_home) / "AGENTS.md",
    )
    monkeypatch.setattr(skills_installer, "install_skills", lambda target_dir=None: [])
    monkeypatch.setattr(
        skills_installer,
        "_configure_skills",
        lambda config_path, skills_root=None: [],
    )
    monkeypatch.setattr(launcher, "_launcher_mode_state", lambda: ("inherit", None))


def test_main_aborts_when_preflight_blocks(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: 7)
    monkeypatch.setattr(
        launcher,
        "_prompt_for_temporary_session",
        lambda: (_ for _ in ()).throw(
            AssertionError("an untyped preflight result must never offer fallback")
        ),
    )

    def _must_not_run(*_args, **_kwargs):
        raise AssertionError("must not launch when preflight blocks")

    monkeypatch.setattr(launcher, "_run_codex_process", _must_not_run)
    assert launcher.main([]) == 7
    assert not (tmp_path / "user" / ".codex" / "codex.pid").exists()
    assert not list((tmp_path / "tokenpak-home").rglob("codex.pid"))


def test_shared_contention_can_use_temporary_session_for_one_invocation(
    monkeypatch, tmp_path, capsys
):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
    shared_home = tmp_path / "user" / ".codex"
    preflight_homes = []

    def block_shared_only(*, home, **_kwargs):
        preflight_homes.append(Path(home))
        return _preflight_result() if Path(home) == shared_home else None

    monkeypatch.setattr(launcher, "_preflight_state_lock", block_shared_only)
    monkeypatch.setattr(
        launcher,
        "_prompt_for_temporary_session",
        lambda: launcher.TemporarySessionChoice.ACCEPTED,
    )
    captured = {}

    def fake_run(argv, env, *, on_start=None):
        captured["argv"] = argv
        captured["env"] = env
        assert on_start is not None
        on_start(os.getpid())
        return 0, launcher.empty_usage()

    monkeypatch.setattr(launcher, "_run_codex_process", fake_run)
    monkeypatch.setattr(launcher, "_launcher_mode_state", lambda: ("approval-bypass", None))
    receipt_path = tmp_path / "fallback-receipt.json"

    assert (
        launcher.main(
            [],
            receipt_out=str(receipt_path),
            run_id="shared_fallback_receipt",
        )
        == 0
    )
    assert preflight_homes == [shared_home]
    assert captured["argv"] == ["codex", "--ask-for-approval", "never"]
    assert captured["env"]["TOKENPAK_CODEX_SESSION_MODE"] == "isolated"
    assert captured["env"]["CODEX_HOME"].startswith(
        str(tmp_path / "tokenpak-home" / "companion" / "codex" / "sessions")
    )
    assert os.environ["TOKENPAK_CODEX_SESSION_MODE"] == "shared"
    assert captured["env"]["TOKENPAK_CODEX_RUN_ID"] == "shared_fallback_receipt"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["tokenpak_setup"]["session_mode"] == "isolated"
    assert receipt["tokenpak_setup"]["codex_home"] == captured["env"]["CODEX_HOME"]
    assert receipt["tokenpak_setup"]["fallback_attempted"] is True
    assert receipt["tokenpak_setup"]["fallback_result"] == "provisioned"
    assert receipt["tokenpak_setup"]["selected_session_class"] == "temporary"
    assert receipt["tokenpak_setup"]["continuity_mode"] == "new_temporary_lineage"
    assert receipt["tokenpak_setup"]["prior_shared_history_attached"] is False
    assert (
        receipt["tokenpak_setup"]["original_preflight_result"]["evidence"]["status"]
        == "holder_timeout_last_verified_live"
    )
    assert "this invocation only" in capsys.readouterr().err
    assert not list((tmp_path / "tokenpak-home").rglob("codex.pid"))


def test_shared_contention_decline_preserves_original_exit(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: _preflight_result())
    monkeypatch.setattr(
        launcher,
        "_prompt_for_temporary_session",
        lambda: launcher.TemporarySessionChoice.DECLINED,
    )

    assert launcher.main([]) == 1
    assert not list((tmp_path / "tokenpak-home").rglob("codex.pid"))


def test_shared_contention_prompt_cancel_returns_130(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: _preflight_result())
    monkeypatch.setattr(
        launcher,
        "_prompt_for_temporary_session",
        lambda: launcher.TemporarySessionChoice.CANCELLED,
    )

    assert launcher.main([]) == 130
    assert not list((tmp_path / "tokenpak-home").rglob("codex.pid"))


def test_temporary_session_selection_failure_preserves_typed_original_receipt(
    monkeypatch, tmp_path
):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: _preflight_result())
    monkeypatch.setattr(
        launcher,
        "_prompt_for_temporary_session",
        lambda: launcher.TemporarySessionChoice.ACCEPTED,
    )
    original_select_paths = sh.select_paths

    def select_paths(mode=None, **kwargs):
        if mode == sh.MODE_ISOLATED:
            raise ValueError("injected temporary selection failure")
        return original_select_paths(mode=mode, **kwargs)

    monkeypatch.setattr(sh, "select_paths", select_paths)
    receipt_path = tmp_path / "selection-failure-receipt.json"

    assert (
        launcher.main(
            [],
            receipt_out=str(receipt_path),
            run_id="temporary_selection_failure",
        )
        == 1
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    setup = receipt["tokenpak_setup"]
    assert setup["fallback_result"] == "selection_failed"
    assert setup["selected_session_class"] == "shared"
    assert setup["continuity_mode"] == "shared_lineage_not_replaced"
    assert setup["original_preflight_result"]["evidence"]["status"] == (
        "holder_timeout_last_verified_live"
    )


def test_temporary_session_provision_failure_records_both_outcomes(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: _preflight_result())
    monkeypatch.setattr(
        launcher,
        "_prompt_for_temporary_session",
        lambda: launcher.TemporarySessionChoice.ACCEPTED,
    )
    monkeypatch.setattr(
        sh,
        "provision",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected temporary provisioning failure")
        ),
    )
    receipt_path = tmp_path / "provision-failure-receipt.json"

    assert (
        launcher.main(
            [],
            receipt_out=str(receipt_path),
            run_id="temporary_provision_failure",
        )
        == 1
    )
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    setup = receipt["tokenpak_setup"]
    assert setup["fallback_result"] == "setup_failed"
    assert setup["selected_session_class"] == "temporary"
    assert setup["continuity_mode"] == "new_temporary_lineage"
    assert setup["prior_shared_history_attached"] is False
    assert setup["original_preflight_result"]["fallback_decision"]["eligible"] is True


def test_shared_mode_real_holder_probe_refuses_before_setup(monkeypatch, tmp_path, capsys):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
    database = tmp_path / "user" / ".codex" / "state_77.sqlite"
    holder = sqlite3.connect(database, isolation_level=None)
    holder.execute("CREATE TABLE records (id INTEGER)")
    holder.execute("BEGIN EXCLUSIVE")
    real_preflight = launcher._preflight_state_lock

    def fast_preflight(**kwargs):
        clock = _Clock()

        def probe_once():
            status = sl.probe(kwargs["home"])
            clock.t = 5.0
            return status

        return real_preflight(
            prober=probe_once,
            interactive=False,
            timeout_s=5,
            sleep=lambda _seconds: None,
            monotonic=clock,
            home=kwargs["home"],
        )

    monkeypatch.setattr(launcher, "_preflight_state_lock", fast_preflight)
    monkeypatch.setattr(
        launcher.CompanionConfig,
        "from_env",
        classmethod(
            lambda cls: (_ for _ in ()).throw(
                AssertionError("shared contention must block before setup")
            )
        ),
    )
    try:
        assert launcher.main(["--install-only"]) == 1
        stderr = capsys.readouterr().err
        assert f"PID {os.getpid()} (running)" in stderr
        assert "holder PID unavailable" not in stderr
        assert not (database.parent / "codex.pid").exists()
    finally:
        holder.execute("ROLLBACK")
        holder.close()


@pytest.mark.parametrize("platform_id", ["darwin", "win32"])
def test_shared_mode_existing_database_launches_with_portable_clear_probe(
    monkeypatch, tmp_path, platform_id
):
    from tokenpak.companion.codex import state_lock

    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
    database = tmp_path / "user" / ".codex" / "state_77.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE records (id INTEGER)")
    connection.close()
    monkeypatch.setattr(sys, "platform", platform_id)
    monkeypatch.setattr(state_lock, "_portable_codex_processes", lambda *_args: ([], True))

    assert launcher.main(["--install-only"]) == 0


def test_shared_mode_gives_each_reusable_preflight_a_fresh_deadline(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
    calls: list[dict[str, object]] = []

    def clear_preflight(**kwargs):
        calls.append(kwargs)
        assert "deadline" not in kwargs
        return None

    monkeypatch.setattr(launcher, "_preflight_state_lock", clear_preflight)

    assert launcher.main(["--install-only"]) == 0
    assert len(calls) >= 3


def test_invalid_mode_fails_before_preflight_or_filesystem_write(monkeypatch, tmp_path):
    _stub_session_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "automatic")
    monkeypatch.setattr(
        launcher,
        "_preflight_state_lock",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid mode must fail before preflight")
        ),
    )

    assert launcher.main([]) == 2
    assert not (tmp_path / "tokenpak-home").exists()


def test_main_install_only_preflights_and_releases_lease(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    called = {"n": 0}

    def _preflight(**_kwargs):
        called["n"] += 1
        return None

    monkeypatch.setattr(launcher, "_preflight_state_lock", _preflight)
    rc = launcher.main(["--install-only"])
    assert rc == 0
    assert called["n"] == 1
    assert not list((tmp_path / "tokenpak-home").rglob("codex.pid"))


def test_shared_install_preserves_existing_config_and_skips_skill_refs(monkeypatch, tmp_path):
    from tokenpak.companion.codex import skills_installer

    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: None)
    config = tmp_path / "user" / ".codex" / "config.toml"
    original = config.read_bytes()
    monkeypatch.setattr(
        skills_installer,
        "_configure_skills",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("shared mode must not mutate config for skill discovery")
        ),
    )

    assert launcher.main(["--install-only"]) == 0
    assert config.read_bytes() == original


def test_main_supervises_when_preflight_clear(monkeypatch, tmp_path, capsys):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: None)
    captured = {}

    def _fake_run(argv, env, *, on_start=None):
        captured["argv"] = argv
        captured["env"] = env
        assert on_start is not None
        on_start(os.getpid())
        return 0, launcher.empty_usage()

    monkeypatch.setattr(launcher, "_run_codex_process", _fake_run)
    assert launcher.main([]) == 0
    assert captured["argv"][0] == "codex"
    assert captured["env"]["TOKENPAK_CODEX_SESSION_MODE"] == "isolated"
    assert captured["env"]["CODEX_HOME"].startswith(
        str(tmp_path / "tokenpak-home" / "companion" / "codex" / "sessions")
    )
    assert not list((tmp_path / "tokenpak-home").rglob("codex.pid"))
    stderr = capsys.readouterr().err
    for label in (
        "session mode:",
        "workspace:",
        "CODEX_HOME:",
        "source home:",
        "config:",
        "auth:",
        "MCP config:",
        "hooks:",
        "AGENTS.md:",
        "skills:",
        "PID sentinel:",
    ):
        assert label in stderr


@pytest.mark.parametrize("mode", ["shared", "workspace", "isolated"])
def test_retention_sweep_precedes_selected_home_creation_for_every_mode(
    monkeypatch, tmp_path, mode
):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", mode)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: None)
    events: list[str] = []

    class Cleanup:
        removed = ()
        errors = ()

    def cleanup(*_args, preserve_home=None, **_kwargs):
        events.append("cleanup")
        if mode in {"workspace", "isolated"}:
            assert preserve_home is not None
            assert not Path(preserve_home).exists()
        return Cleanup()

    def refuse(paths):
        events.append("acquire")
        raise RuntimeError("stop after ordering assertion")

    monkeypatch.setattr(sh, "cleanup_isolated_homes", cleanup)
    monkeypatch.setattr(sh.SessionLease, "acquire", staticmethod(refuse))

    assert launcher.main(["--install-only"]) == 1
    assert events == ["cleanup", "acquire"]


@pytest.mark.parametrize("mode", ["shared", "workspace"])
def test_switching_modes_runs_retention_without_another_isolated_launch(
    monkeypatch, tmp_path, mode
):
    _stub_setup(monkeypatch, tmp_path)
    source = tmp_path / "user" / ".codex"
    tokenpak_home = tmp_path / "tokenpak-home"
    orphan = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id=f"orphan-before-{mode}",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(orphan)
    old = time.time() - sh.RETENTION_MAX_AGE_S - 60
    os.utime(orphan.home, (old, old))
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", mode)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: None)

    assert launcher.main(["--install-only"]) == 0
    assert not orphan.home.exists()


def test_storage_pressure_sweep_precedes_failed_provision_retry(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: None)
    source = tmp_path / "user" / ".codex"
    tokenpak_home = tmp_path / "tokenpak-home"
    orphan = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="pressure-orphan",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(orphan)
    old = time.time() - 300
    os.utime(orphan.home, (old, old))
    original = sh.provision
    calls = 0

    def no_space(paths, *, home_fd=None):
        nonlocal calls
        calls += 1
        if paths == orphan:
            return original(paths, home_fd=home_fd)
        raise OSError(errno.ENOSPC, "injected storage pressure")

    monkeypatch.setattr(sh, "provision", no_space)

    assert launcher.main(["--install-only"]) == 1
    assert calls == 2
    assert not orphan.home.exists()


def test_storage_pressure_during_home_creation_sweeps_and_retries_once(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: None)
    source = tmp_path / "user" / ".codex"
    tokenpak_home = tmp_path / "tokenpak-home"
    orphan = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="acquire-pressure-orphan",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(orphan)
    old = time.time() - 300
    os.utime(orphan.home, (old, old))
    original_acquire = sh.SessionLease.acquire
    calls = 0

    def acquire(paths):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(errno.EDQUOT, "injected selected-home quota pressure")
        return original_acquire(paths)

    monkeypatch.setattr(sh.SessionLease, "acquire", staticmethod(acquire))

    assert launcher.main(["--install-only"]) == 0
    assert calls == 2
    assert not orphan.home.exists()


def test_shared_mode_pre_sweep_recovers_receipted_quarantine(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    source = tmp_path / "user" / ".codex"
    tokenpak_home = tmp_path / "tokenpak-home"
    orphan = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="quarantine-before-shared",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(orphan)
    old = time.time() - 300
    os.utime(orphan.home, (old, old))
    original_remove = sh._remove_tree_contents_at

    def fail_once(*_args, **_kwargs):
        raise sh.HomeInUseError("injected resumable quarantine")

    monkeypatch.setattr(sh, "_remove_tree_contents_at", fail_once)
    failed = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)
    assert failed.errors and "injected resumable quarantine" in failed.errors[0]
    root = sh.sessions_root(tokenpak_home)
    assert any(path.name.startswith(sh._RETENTION_QUARANTINE_PREFIX) for path in root.iterdir())

    monkeypatch.setattr(sh, "_remove_tree_contents_at", original_remove)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: None)

    assert launcher.main(["--install-only"]) == 0
    assert not any(path.name.startswith(sh._RETENTION_QUARANTINE_PREFIX) for path in root.iterdir())
    assert '"action": "completed"' in (root / sh._RETENTION_RECEIPT_NAME).read_text(
        encoding="utf-8"
    )


def test_post_session_sweep_removes_orphan_created_during_last_child(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: None)
    source = tmp_path / "user" / ".codex"
    tokenpak_home = tmp_path / "tokenpak-home"
    created: list[sh.SessionPaths] = []

    def fake_run(_argv, _env, *, on_start=None):
        assert on_start is not None
        on_start(os.getpid())
        orphan = sh.select_paths(
            "isolated",
            workspace_dir=tmp_path,
            session_id="created-during-final-child",
            tokenpak_home=tokenpak_home,
            source_home=source,
        )
        sh.provision(orphan)
        old = time.time() - sh.RETENTION_MAX_AGE_S - 60
        os.utime(orphan.home, (old, old))
        created.append(orphan)
        return 0, launcher.empty_usage()

    monkeypatch.setattr(launcher, "_run_codex_process", fake_run)

    assert launcher.main([]) == 0
    assert len(created) == 1
    assert not created[0].home.exists()


def test_retention_failure_never_masks_child_exit_code(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: None)
    calls = 0

    def broken_cleanup(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise KeyError("malformed governed-cleanup metadata")

    def fake_run(_argv, _env, *, on_start=None):
        assert on_start is not None
        on_start(os.getpid())
        return 7, launcher.empty_usage()

    monkeypatch.setattr(sh, "cleanup_isolated_homes", broken_cleanup)
    monkeypatch.setattr(launcher, "_run_codex_process", fake_run)

    assert launcher.main([]) == 7
    assert calls == 3


def test_sentinel_release_failure_never_masks_child_exit_code(monkeypatch, tmp_path, capsys):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: None)

    def fake_run(_argv, _env, *, on_start=None):
        assert on_start is not None
        on_start(os.getpid())
        return 7, launcher.empty_usage()

    def preserve_sentinel(_self):
        _self._released = True
        if _self.home_fd is not None:
            os.close(_self.home_fd)
            _self.home_fd = None
        raise RuntimeError("injected exact-owner cleanup failure")

    monkeypatch.setattr(launcher, "_run_codex_process", fake_run)
    monkeypatch.setattr(sh.SessionLease, "release", preserve_sentinel)

    assert launcher.main([]) == 7
    assert "PID sentinel cleanup preserved for inspection" in capsys.readouterr().err


def test_launcher_live_child_transfer_temp_is_preserved_during_concurrent_retention(
    monkeypatch, tmp_path
):
    user_home = tmp_path / "user"
    source = user_home / ".codex"
    source.mkdir(parents=True)
    tokenpak_home = tmp_path / "tokenpak-home"
    monkeypatch.setenv("HOME", str(user_home))
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="concurrent-live-handoff",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    lease = sh.SessionLease.acquire(paths, session_id="concurrent-live-handoff")
    lease.begin_transfer()
    dead_parent = sh.PidSentinel(
        schema="tokenpak.codex.pid.v1",
        pid=2_000_000_000,
        start_time_ticks=1,
        session_id=lease.sentinel.session_id,
        mode="isolated",
        home=str(paths.home.resolve()),
    )
    paths.pid_sentinel.write_text(json.dumps(dead_parent.__dict__) + "\n", encoding="utf-8")
    paths.pid_sentinel.chmod(0o600)
    lease.sentinel = dead_parent

    transfer_temp_ready = threading.Event()
    allow_transfer = threading.Event()
    cleanup_done = threading.Event()
    release_child = tmp_path / "release-child"
    original_replace = sh.os.replace
    child_temp: list[tuple[str, sh.PidSentinel]] = []
    launch_results: list[tuple[int, dict[str, int | None]]] = []
    cleanup_results: list[sh._CleanupResult] = []
    errors: list[BaseException] = []

    def paused_replace(src, dst, *args, **kwargs):
        if isinstance(src, str) and sh._SENTINEL_TEMP_RE.fullmatch(src):
            candidate = sh.read_pid_sentinel(Path(src), dir_fd=lease.home_fd)
            assert candidate is not None
            child_temp.append((src, candidate))
            transfer_temp_ready.set()
            if not allow_transfer.wait(5):
                raise RuntimeError("timed out waiting for concurrent retention")
        return original_replace(src, dst, *args, **kwargs)

    def run_launcher_child() -> None:
        code = (
            "import pathlib,time\n"
            f"flag=pathlib.Path({str(release_child)!r})\n"
            "deadline=time.time()+10\n"
            "while not flag.exists() and time.time()<deadline: time.sleep(0.01)\n"
        )
        try:
            launch_results.append(
                launcher._run_codex_process(
                    [sys.executable, "-c", code],
                    os.environ.copy(),
                    on_start=lease.transfer_to,
                )
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def cleanup() -> None:
        try:
            cleanup_results.append(
                sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)
            )
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            cleanup_done.set()

    monkeypatch.setattr(sh.os, "replace", paused_replace)
    launch_thread = threading.Thread(target=run_launcher_child)
    cleanup_thread = threading.Thread(target=cleanup)
    try:
        launch_thread.start()
        assert transfer_temp_ready.wait(5)
        assert sh.read_pid_sentinel(paths.pid_sentinel) == dead_parent
        assert child_temp and sh.sentinel_is_live(child_temp[0][1], expected_home=paths.home)
        assert not list(paths.home.glob("*.sqlite*"))

        cleanup_thread.start()
        assert not cleanup_done.wait(0.1)
        allow_transfer.set()
        cleanup_thread.join(10)
        assert not cleanup_thread.is_alive()
        assert cleanup_results and cleanup_results[0].removed == ()
        assert paths.home.is_dir()
        current = sh.read_pid_sentinel(paths.pid_sentinel)
        assert current is not None and current.pid == child_temp[0][1].pid
    finally:
        allow_transfer.set()
        release_child.touch()
        launch_thread.join(15)
        if cleanup_thread.is_alive():
            cleanup_thread.join(15)
        lease.release()

    assert errors == []
    assert launch_results and launch_results[0][0] == 0


def test_main_applies_launcher_default_before_launch(monkeypatch, tmp_path, capsys):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: None)
    monkeypatch.setattr(
        launcher,
        "_launcher_mode_state",
        lambda: ("sandbox-bypass", None),
    )
    captured = {}

    def _fake_run(argv, env, *, on_start=None):
        captured["argv"] = list(argv)
        captured["env"] = dict(env)
        assert on_start is not None
        on_start(os.getpid())
        return 0, launcher.empty_usage()

    monkeypatch.setattr(launcher, "_run_codex_process", _fake_run)
    assert launcher.main(["--foo"]) == 0
    assert captured["argv"] == [
        "codex",
        "--sandbox",
        "danger-full-access",
        "--foo",
    ]
    stderr = capsys.readouterr().err
    assert "codex launcher mode sandbox-bypass active" in stderr
    assert "--sandbox danger-full-access" in stderr


# ── explicit no-body accounting receipt mode ───────────────────────────


def test_codex_parser_accepts_receipt_and_run_id_before_forwarded_args():
    parser = _cli_core.build_parser()
    ns = parser.parse_args(
        [
            "codex",
            "--receipt-out",
            "/tmp/receipt.json",
            "--run-id",
            "run_codex_smoke",
            "exec",
            "--version",
        ]
    )
    assert ns.receipt_out == "/tmp/receipt.json"
    assert ns.run_id == "run_codex_smoke"
    assert ns.args == ["exec", "--version"]


def test_codex_parser_accepts_receipt_only_before_forwarded_args():
    parser = _cli_core.build_parser()
    ns = parser.parse_args(
        [
            "codex",
            "--receipt-only",
            "--receipt-out",
            "/tmp/receipt.json",
            "--run-id",
            "run_codex_smoke",
            "exec",
            "--version",
        ]
    )
    assert ns.receipt_only is True
    assert ns.receipt_out == "/tmp/receipt.json"
    assert ns.run_id == "run_codex_smoke"
    assert ns.args == ["exec", "--version"]


def test_codex_accounting_flags_are_stripped_from_forwarded_args():
    forwarded, receipt_out, run_id = _cli_core._extract_codex_accounting_flags(
        [
            "exec",
            "--model",
            "gpt-5.5",
            "--receipt-out=/tmp/receipt.json",
            "--run-id",
            "run_after_subcommand",
            "prompt body",
        ]
    )
    assert receipt_out == "/tmp/receipt.json"
    assert run_id == "run_after_subcommand"
    assert forwarded == ["exec", "--model", "gpt-5.5", "prompt body"]


def test_codex_receipt_only_flag_is_stripped_from_forwarded_args():
    forwarded, receipt_only = _cli_core._extract_codex_receipt_only_flag(
        ["exec", "--receipt-only", "--version"]
    )
    assert receipt_only is True
    assert forwarded == ["exec", "--version"]


def test_codex_manual_namespace_preserves_native_flag_first_invocation():
    ns = _cli_core._codex_namespace_from_tail(
        [
            "--receipt-out",
            "/tmp/receipt.json",
            "--run-id",
            "run_flag_first",
            "--version",
        ]
    )
    assert ns.receipt_out == "/tmp/receipt.json"
    assert ns.run_id == "run_flag_first"
    assert ns.args == ["--version"]


def test_codex_manual_namespace_preserves_receipt_only_flag():
    ns = _cli_core._codex_namespace_from_tail(
        [
            "--receipt-out",
            "/tmp/receipt.json",
            "--run-id",
            "run_flag_first",
            "--receipt-only",
            "--version",
        ]
    )
    assert ns.receipt_only is True
    assert ns.receipt_out == "/tmp/receipt.json"
    assert ns.run_id == "run_flag_first"
    assert ns.args == ["--version"]


def test_main_receipt_mode_runs_child_and_writes_no_body_receipt(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: None)
    monkeypatch.setattr(launcher, "_fleet_state_enabled", lambda: False)
    captured = {}

    def _fake_run(argv, env, *, on_start=None):
        captured["argv"] = list(argv)
        captured["env"] = dict(env or {})
        assert on_start is not None
        on_start(os.getpid())
        return 0, launcher.empty_usage()

    monkeypatch.setattr(launcher, "_run_codex_process", _fake_run)

    receipt_path = tmp_path / "receipt.json"
    prompt_body = "BODY_SENTINEL_PROMPT_MUST_NOT_BE_STORED"
    rc = launcher.main(
        ["exec", "-m", "gpt-5.5", prompt_body],
        receipt_out=str(receipt_path),
        run_id="run_receipt_test",
    )

    assert rc == 0
    assert captured["argv"] == ["codex", "exec", "-m", "gpt-5.5", prompt_body]
    assert captured["env"]["TOKENPAK_CODEX_RUN_ID"] == "run_receipt_test"
    assert captured["env"]["TOKENPAK_CODEX_RECEIPT_OUT"] == str(receipt_path)

    raw = receipt_path.read_text(encoding="utf-8")
    assert prompt_body not in raw
    receipt = json.loads(raw)
    assert receipt["schema"] == "tokenpak.codex.accounting_receipt.v1"
    assert receipt["run_id"] == "run_receipt_test"
    assert receipt["command"]["model"] == "gpt-5.5"
    assert receipt["command"]["argv_redacted"] == [
        "codex",
        "exec",
        "-m",
        "gpt-5.5",
        "<redacted-positional>",
    ]
    assert receipt["privacy"]["prompt_body_stored"] is False
    assert receipt["privacy"]["completion_body_stored"] is False
    assert receipt["privacy"]["stdout_stored"] is False
    assert receipt["privacy"]["stderr_stored"] is False
    assert receipt["attribution"]["tokenpak_mechanism_active"] is True


def test_receipt_only_mode_skips_companion_setup_and_writes_no_body_receipt(monkeypatch, tmp_path):
    from tokenpak.companion.codex import (
        agents_md,
        mcp_config,
        rates_snapshot,
        skills_installer,
    )

    def _setup_must_not_run(*_a, **_k):
        raise AssertionError("receipt-only mode must not run companion setup")

    _stub_session_env(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher.CompanionConfig, "from_env", _setup_must_not_run)
    monkeypatch.setattr(rates_snapshot, "refresh", _setup_must_not_run)
    monkeypatch.setattr(mcp_config, "_register", _setup_must_not_run)
    monkeypatch.setattr(agents_md, "_install_agents_md", _setup_must_not_run)
    monkeypatch.setattr(skills_installer, "install_skills", _setup_must_not_run)
    monkeypatch.setattr(launcher, "_launcher_mode_state", _setup_must_not_run)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: None)
    monkeypatch.setenv("TOKENPAK_COMPANION_PROFILE", "aggressive")
    monkeypatch.setenv("TOKENPAK_MANAGED", "1")

    captured = {}

    def _fake_run(argv, env, *, on_start=None):
        captured["argv"] = list(argv)
        captured["env"] = dict(env or {})
        assert on_start is not None
        on_start(os.getpid())
        return 0, launcher.empty_usage()

    monkeypatch.setattr(launcher, "_run_codex_process", _fake_run)

    receipt_path = tmp_path / "receipt-only.json"
    prompt_body = "BODY_SENTINEL_PROMPT_MUST_NOT_BE_STORED"
    rc = launcher.main(
        ["--receipt-only", "exec", "-m", "gpt-5.5", prompt_body],
        receipt_out=str(receipt_path),
        run_id="run_receipt_only_test",
    )

    assert rc == 0
    assert captured["argv"] == ["codex", "exec", "-m", "gpt-5.5", prompt_body]
    assert captured["env"]["TOKENPAK_CODEX_RUN_ID"] == "run_receipt_only_test"
    assert captured["env"]["TOKENPAK_CODEX_RECEIPT_OUT"] == str(receipt_path)
    assert "TOKENPAK_COMPANION_PROFILE" not in captured["env"]
    assert "TOKENPAK_MANAGED" not in captured["env"]
    assert captured["env"]["TOKENPAK_CODEX_SESSION_MODE"] == "isolated"
    assert captured["env"]["CODEX_HOME"].startswith(
        str(tmp_path / "tokenpak-home" / "companion" / "codex" / "sessions")
    )

    raw = receipt_path.read_text(encoding="utf-8")
    assert prompt_body not in raw
    receipt = json.loads(raw)
    assert receipt["schema"] == "tokenpak.codex.accounting_receipt.v1"
    assert receipt["run_id"] == "run_receipt_only_test"
    assert receipt["tokenpak_setup"]["mode"] == "receipt_only"
    assert receipt["tokenpak_setup"]["setup_completed"] is False
    assert receipt["tokenpak_setup"]["mcp_registered"] is False
    assert receipt["tokenpak_setup"]["hooks_installed"] is False
    assert receipt["tokenpak_setup"]["agents_md_installed"] is False
    assert receipt["tokenpak_setup"]["skills_installed_count"] == 0
    assert receipt["privacy"]["prompt_body_stored"] is False
    assert receipt["privacy"]["completion_body_stored"] is False
    assert receipt["privacy"]["stdout_stored"] is False
    assert receipt["privacy"]["stderr_stored"] is False
    assert receipt["attribution"]["receipt_wrapper_active"] is True
    assert receipt["attribution"]["tokenpak_mechanism_active"] is False
    assert receipt["attribution"]["tokenpak_value_mechanism_active"] is False


def test_json_receipt_mode_extracts_usage_without_storing_body(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda **_kwargs: None)
    monkeypatch.setattr(launcher, "_fleet_state_enabled", lambda: False)

    class _FakeStdout:
        def __init__(self):
            self._lines = iter(
                [
                    '{"type":"message","text":"BODY_SENTINEL_COMPLETION"}\n',
                    (
                        '{"type":"usage","usage":{"input_tokens":10,'
                        '"cached_input_tokens":4,"output_tokens":6}}\n'
                    ),
                ]
            )

        def readline(self):
            return next(self._lines, "")

    class _FakePopen:
        stdout = _FakeStdout()
        pid = os.getpid()

        def __init__(self, *_a, **_k):
            return None

        def wait(self):
            return 0

        def poll(self):
            return None

    monkeypatch.setattr(launcher.subprocess, "Popen", _FakePopen)

    receipt_path = tmp_path / "json-receipt.json"
    rc = launcher.main(
        ["exec", "--json", "-m", "gpt-5.5", "BODY_SENTINEL_PROMPT"],
        receipt_out=str(receipt_path),
        run_id="run_json_usage",
    )

    assert rc == 0
    raw = receipt_path.read_text(encoding="utf-8")
    assert "BODY_SENTINEL_PROMPT" not in raw
    assert "BODY_SENTINEL_COMPLETION" not in raw
    receipt = json.loads(raw)
    assert receipt["metrics"]["input_tokens"] == 10
    assert receipt["metrics"]["cached_input_tokens"] == 4
    assert receipt["metrics"]["output_tokens"] == 6
    assert receipt["metrics"]["total_tokens"] == 16
    assert receipt["attribution"]["provider_native_caching_involved"] is True


def test_process_supervision_uses_fast_child_result_when_transfer_is_too_late(monkeypatch):
    class FastProcess:
        pid = 12345
        stdout = None
        waited = False

        def __init__(self, *_args, **_kwargs):
            pass

        def poll(self):
            return 0

        def wait(self):
            self.waited = True
            return 0

    process = FastProcess()
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *_a, **_k: process)

    result, _usage = launcher._run_codex_process(
        ["codex", "--version"],
        {},
        on_start=lambda _pid: (_ for _ in ()).throw(RuntimeError("already exited")),
    )

    assert result == 0
    assert process.waited


def test_json_supervision_continues_after_interrupts_and_reaps(monkeypatch, capsys):
    class InterruptingStdout:
        def __init__(self):
            self.calls = 0

        def readline(self):
            self.calls += 1
            if self.calls == 1:
                raise KeyboardInterrupt
            if self.calls == 2:
                return '{"type":"usage","usage":{"input_tokens":2,"output_tokens":3}}\n'
            return ""

    class InterruptingProcess:
        pid = 12346
        stdout = InterruptingStdout()

        def __init__(self, *_args, **_kwargs):
            self.wait_calls = 0

        def wait(self):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise KeyboardInterrupt
            return 0

    process = InterruptingProcess()
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *_a, **_k: process)

    result, usage = launcher._run_codex_process(["codex", "exec", "--json"], {})

    assert result == 0
    assert usage["input_tokens"] == 2
    assert usage["output_tokens"] == 3
    assert process.wait_calls == 2
    assert '"type":"usage"' in capsys.readouterr().out


@pytest.mark.parametrize(
    "write_error",
    [
        BrokenPipeError(),
        UnicodeEncodeError("ascii", "é", 0, 1, "ordinal not in range"),
    ],
    ids=["broken-pipe", "encoding-error"],
)
def test_json_output_failure_is_drained_and_child_reaped(monkeypatch, write_error):
    class Output:
        def __init__(self):
            self.lines = iter(["one\n", "two\n", ""])
            self.read_count = 0

        def readline(self):
            self.read_count += 1
            return next(self.lines)

    class Process:
        pid = 12347
        stdout = Output()

        def __init__(self, *_args, **_kwargs):
            self.waited = False

        def wait(self):
            self.waited = True
            return 0

    class BrokenOutput:
        def write(self, _line):
            raise write_error

        def flush(self):
            raise AssertionError("flush must not follow failed write")

    process = Process()
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *_a, **_k: process)
    monkeypatch.setattr(launcher.sys, "stdout", BrokenOutput())

    result, _usage = launcher._run_codex_process(["codex", "exec", "--json"], {})

    assert result == 0
    assert process.waited
    assert process.stdout.read_count == 3
