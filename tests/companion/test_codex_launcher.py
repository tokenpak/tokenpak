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

import json
from types import SimpleNamespace

import pytest

from tokenpak import _cli_core
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

    with pytest.raises(_Exec):
        launcher.main([])
    assert captured["file"] == "codex"
    assert captured["argv"][0] == "codex"


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


def test_main_receipt_mode_runs_child_and_writes_no_body_receipt(
    monkeypatch, tmp_path
):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda: None)
    monkeypatch.setattr(launcher, "_fleet_state_enabled", lambda: False)

    captured = {}

    def _fake_run(argv, env=None):
        captured["argv"] = list(argv)
        captured["env"] = dict(env or {})
        return SimpleNamespace(returncode=0)

    def _no_exec(*_a, **_k):
        raise AssertionError("receipt mode must not exec-replace")

    monkeypatch.setattr(launcher.subprocess, "run", _fake_run)
    monkeypatch.setattr(launcher.os, "execvpe", _no_exec)

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


def test_receipt_only_mode_skips_companion_setup_and_writes_no_body_receipt(
    monkeypatch, tmp_path
):
    from tokenpak.companion.codex import (
        agents_md,
        mcp_config,
        rates_snapshot,
        skills_installer,
    )

    def _setup_must_not_run(*_a, **_k):
        raise AssertionError("receipt-only mode must not run companion setup")

    monkeypatch.setattr(launcher.CompanionConfig, "from_env", _setup_must_not_run)
    monkeypatch.setattr(rates_snapshot, "refresh", _setup_must_not_run)
    monkeypatch.setattr(mcp_config, "register", _setup_must_not_run)
    monkeypatch.setattr(agents_md, "install_agents_md", _setup_must_not_run)
    monkeypatch.setattr(skills_installer, "install_skills", _setup_must_not_run)
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda: None)
    monkeypatch.setenv("TOKENPAK_COMPANION_PROFILE", "aggressive")
    monkeypatch.setenv("TOKENPAK_MANAGED", "1")

    captured = {}

    def _fake_run(argv, env=None):
        captured["argv"] = list(argv)
        captured["env"] = dict(env or {})
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(launcher.subprocess, "run", _fake_run)
    monkeypatch.setattr(
        launcher.os,
        "execvpe",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("receipt-only mode must not exec-replace")
        ),
    )

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
    monkeypatch.setattr(launcher, "_preflight_state_lock", lambda: None)
    monkeypatch.setattr(launcher, "_fleet_state_enabled", lambda: False)

    class _FakeStdout:
        def __iter__(self):
            return iter(
                [
                    '{"type":"message","text":"BODY_SENTINEL_COMPLETION"}\n',
                    (
                        '{"type":"usage","usage":{"input_tokens":10,'
                        '"cached_input_tokens":4,"output_tokens":6}}\n'
                    ),
                ]
            )

    class _FakePopen:
        stdout = _FakeStdout()

        def __init__(self, *_a, **_k):
            return None

        def wait(self):
            return 0

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
