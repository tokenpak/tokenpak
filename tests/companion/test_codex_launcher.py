# SPDX-License-Identifier: Apache-2.0
"""Launcher session selection, provisioning, retention, and supervision.

Concurrent Codex sessions against one home are supported, exactly as they
are for native ``codex``: Codex's local databases run in write-ahead-logging
mode, which coordinates readers and a serialized writer across processes.
The launcher never opens those databases, so it does not gate startup on
their lock state — see ``test_parallel_sessions_are_not_gated`` below.

Scope note: this does not exercise the ``codex mcp`` / ``codex features``
behavior — that lives in ``hooks.py`` / ``mcp_config.py``.
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

# ── main() session selection, provisioning, and supervision ───────────


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
    """Stub the launcher's setup steps so main() reaches the launch."""
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


@pytest.mark.parametrize("platform_id", ["linux", "darwin", "win32"])
def test_shared_mode_existing_database_launches_on_every_platform(
    monkeypatch, tmp_path, platform_id
):
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
    database = tmp_path / "user" / ".codex" / "state_77.sqlite"
    connection = sqlite3.connect(database)
    connection.execute("CREATE TABLE records (id INTEGER)")
    connection.close()
    monkeypatch.setattr(sys, "platform", platform_id)

    assert launcher.main(["--install-only"]) == 0


def test_parallel_sessions_are_not_gated(monkeypatch, tmp_path):
    """A second session starts while the first still holds the databases.

    Native ``codex`` supports this — its local state is write-ahead-logging
    SQLite, which coordinates concurrent processes.  The launcher must not
    re-impose an exclusivity constraint that Codex itself does not have, so
    it holds an open write transaction here and still expects a clean start.
    """
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
    database = tmp_path / "user" / ".codex" / "state_77.sqlite"
    holder = sqlite3.connect(database)
    holder.execute("PRAGMA journal_mode=WAL")
    holder.execute("CREATE TABLE records (id INTEGER)")
    holder.commit()
    # Keep a live writer attached for the whole launch, the exact condition
    # the removed preflight used to refuse on.
    holder.execute("BEGIN IMMEDIATE")
    holder.execute("INSERT INTO records VALUES (1)")
    try:
        assert launcher.main(["--install-only"]) == 0
    finally:
        holder.rollback()
        holder.close()


def test_parallel_sessions_are_not_gated_by_the_lifecycle_lease(monkeypatch, tmp_path):
    """End-to-end guard for the second gate on the same path.

    Removing the database preflight is not sufficient on its own: the
    ``codex.pid`` lifecycle lease is a single slot, and claiming it
    exclusively refused a concurrent shared session with "already claimed by
    PID N" well after the preflight had passed. Hold a live lease on the
    shared home and require ``main()`` to launch anyway.
    """
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
    paths = sh.select_paths(mode="shared", workspace_dir=tmp_path)
    held = sh.SessionLease.acquire(paths, session_id="already-running")
    try:
        assert launcher.main(["--install-only"]) == 0
        # The concurrent launch must leave the running session's lease intact.
        assert sh.read_pid_sentinel(paths.pid_sentinel) == held.sentinel
    finally:
        held.release()


def test_launcher_never_opens_codex_databases(monkeypatch, tmp_path):
    """Contention is SQLite's to resolve because the launcher is not a reader.

    This is the invariant that makes skipping the gate correct: if the
    launcher ever starts opening Codex's databases it acquires a stake in
    their lock state and this whole design has to be revisited.
    """
    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
    (tmp_path / "user" / ".codex" / "state_77.sqlite").write_bytes(b"")
    opened: list[object] = []
    real_connect = sqlite3.connect

    def _tracking_connect(target, *args, **kwargs):
        opened.append(target)
        return real_connect(target, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", _tracking_connect)

    assert launcher.main(["--install-only"]) == 0
    assert not [target for target in opened if "state_" in str(target)]


def test_retention_uncertainty_output_is_bounded(capsys, tmp_path):
    """Every uncertain home is preserved; only the report is capped.

    Salvaged from the superseded competing PR, whose diagnosis of the retention
    output was correct and independent of the launch-gate disagreement: one line
    per preserved home buries an ordinary launch once several are uncertain.
    """

    class Cleanup:
        removed = ()
        errors = tuple(f"home-{index}: uncertain" for index in range(5))

    class FakeSessionHome:
        @staticmethod
        def _generated_tokenpak_root(_home):
            return tmp_path

        @staticmethod
        def cleanup_isolated_homes(*_args, **_kwargs):
            return Cleanup()

    class Paths:
        home = tmp_path / "selected"

    launcher._run_isolated_retention(
        FakeSessionHome,
        Paths(),
        phase="pre-launch",
        preserve_home=None,
    )

    error = capsys.readouterr().err
    assert "preserved 5 uncertain home(s)" in error
    assert "home-0" in error and "home-1" in error and "home-2" in error
    assert "home-3" not in error and "home-4" not in error
    assert "... 2 more" in error


def test_invalid_mode_fails_before_any_filesystem_write(monkeypatch, tmp_path):
    _stub_session_env(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "automatic")

    assert launcher.main([]) == 2
    assert not (tmp_path / "tokenpak-home").exists()


def test_main_install_only_releases_lease(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
    rc = launcher.main(["--install-only"])
    assert rc == 0
    assert not list((tmp_path / "tokenpak-home").rglob("codex.pid"))


def test_shared_install_preserves_existing_config_and_skips_skill_refs(monkeypatch, tmp_path):
    from tokenpak.companion.codex import skills_installer

    _stub_setup(monkeypatch, tmp_path)
    monkeypatch.setenv("TOKENPAK_CODEX_SESSION_MODE", "shared")
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

    assert launcher.main(["--install-only"]) == 0
    assert not orphan.home.exists()


def test_storage_pressure_sweep_precedes_failed_provision_retry(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
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

    assert launcher.main(["--install-only"]) == 0
    assert not any(path.name.startswith(sh._RETENTION_QUARANTINE_PREFIX) for path in root.iterdir())
    assert '"action": "completed"' in (root / sh._RETENTION_RECEIPT_NAME).read_text(
        encoding="utf-8"
    )


def test_post_session_sweep_removes_orphan_created_during_last_child(monkeypatch, tmp_path):
    _stub_setup(monkeypatch, tmp_path)
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
