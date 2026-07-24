# SPDX-License-Identifier: Apache-2.0
"""Deterministic tests for Codex session-home selection and lifecycle."""

from __future__ import annotations

import errno
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict
from pathlib import Path

import pytest

from tokenpak.companion.codex import session_home as sh


@pytest.fixture
def homes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    user_home = tmp_path / "user"
    source = user_home / ".codex"
    source.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.delenv(sh.ENV_CODEX_HOME, raising=False)
    monkeypatch.delenv(sh.ENV_SESSION_MODE, raising=False)
    (source / "config.toml").write_text('model = "gpt-test"\n', encoding="utf-8")
    (source / "auth.json").write_text('{"test": true}\n', encoding="utf-8")
    (source / "auth.json").chmod(0o600)
    return source, tmp_path / "tokenpak"


@pytest.mark.parametrize("mode", sh.VALID_MODES)
def test_resolve_mode_accepts_only_advertised_values(mode: str) -> None:
    assert sh.resolve_mode(mode) == mode


@pytest.mark.parametrize(
    "mode",
    ["", "auto", "attach", "per-project", "bogus", " Shared", "WORKSPACE", "isolated "],
)
def test_invalid_mode_fails_closed(mode: str) -> None:
    with pytest.raises(sh.InvalidSessionMode, match="expected shared\\|workspace\\|isolated"):
        sh.resolve_mode(mode)


def test_shared_retains_existing_codex_home(
    homes: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    source, tokenpak_home = homes
    custom = source.parent / "custom-codex"
    monkeypatch.setenv(sh.ENV_CODEX_HOME, str(custom))
    paths = sh.select_paths("shared", tokenpak_home=tokenpak_home)
    assert paths.home == custom
    assert sh.provision(paths).created is False
    assert not custom.exists()


def test_workspace_homes_are_stable_per_project_and_database_separate(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    (project_a / ".git").mkdir()
    nested_a = project_a / "packages" / "component"
    nested_a.mkdir(parents=True)

    a1 = sh.select_paths(
        "workspace",
        workspace_dir=project_a,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    a2 = sh.select_paths(
        "workspace",
        workspace_dir=nested_a,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    b = sh.select_paths(
        "workspace",
        workspace_dir=project_b,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )

    assert a1.home == a2.home
    assert a1.workspace == project_a.resolve()
    assert a1.home != b.home
    assert a1.home.parent == tokenpak_home / "companion" / "codex" / "workspaces"
    assert a1.home / "state_5.sqlite" != b.home / "state_5.sqlite"
    assert a1.home / "logs_2.sqlite" != b.home / "logs_2.sqlite"

    for paths, marker in ((a1, "project-a"), (b, "project-b")):
        sh.provision(paths)
        with sqlite3.connect(paths.home / "state_5.sqlite") as connection:
            connection.execute("CREATE TABLE marker (value TEXT NOT NULL)")
            connection.execute("INSERT INTO marker VALUES (?)", (marker,))
    with sqlite3.connect(a1.home / "state_5.sqlite") as connection:
        assert connection.execute("SELECT value FROM marker").fetchone() == ("project-a",)
    with sqlite3.connect(b.home / "state_5.sqlite") as connection:
        assert connection.execute("SELECT value FROM marker").fetchone() == ("project-b",)


def test_workspace_inspection_ignores_inherited_codex_home(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, tokenpak_home = homes
    monkeypatch.setenv(sh.ENV_CODEX_HOME, str(tmp_path / "unrelated-home"))
    paths = sh.current_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    assert paths.home == sh.workspaces_root(tokenpak_home) / sh.workspace_hash(
        sh.project_root(tmp_path)
    )


def test_isolated_sessions_always_receive_unique_homes(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, tokenpak_home = homes
    ids = iter(["fixed-session-one", "fixed-session-two"])
    monkeypatch.setattr(sh.uuid, "uuid4", lambda: type("UUID", (), {"hex": next(ids)})())
    first = sh.select_paths(
        "isolated", workspace_dir=tmp_path, tokenpak_home=tokenpak_home, source_home=source
    )
    second = sh.select_paths(
        "isolated", workspace_dir=tmp_path, tokenpak_home=tokenpak_home, source_home=source
    )
    assert first.home != second.home
    assert first.home.parent == tokenpak_home / "companion" / "codex" / "sessions"


def test_provision_uses_closed_allowlist_and_never_copies_runtime_state(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    forbidden = (
        "state_5.sqlite",
        "state_5.sqlite-wal",
        "state_5.sqlite-shm",
        "logs_2.sqlite",
        "logs_2.sqlite-wal",
        "logs_2.sqlite-shm",
        "history.jsonl",
        "session_index.jsonl",
    )
    for name in forbidden:
        (source / name).write_bytes(b"must-not-copy")
    (source / "sessions").mkdir()
    (source / "sessions" / "live.jsonl").write_text("must-not-copy", encoding="utf-8")

    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="allowlist-test",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    result = sh.provision(paths)

    assert result.seeded == ("config.toml",)
    assert result.linked_credentials == ("auth.json",)
    assert paths.config.read_text(encoding="utf-8") == 'model = "gpt-test"\n'
    assert paths.config.stat().st_mode & 0o777 == 0o600
    assert paths.auth.is_symlink()
    assert paths.auth.resolve() == (source / "auth.json").resolve()
    assert not any((paths.home / name).exists() for name in forbidden)
    assert not (paths.home / "sessions").exists()
    assert {entry.name for entry in paths.home.iterdir()} == {"config.toml", "auth.json"}


def test_config_symlink_cannot_smuggle_database_bytes(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="symlink-source",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    source_config = source / "config.toml"
    source_config.unlink()
    database = source / "state_9.sqlite"
    database.write_bytes(b"SQLite format 3\x00runtime")
    source_config.symlink_to(database)

    result = sh.provision(paths)
    assert result.seeded == ()
    assert not paths.config.exists()
    assert not (paths.home / database.name).exists()


def test_config_seed_removes_storage_overrides_that_break_isolation(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    (source / "config.toml").write_text(
        'model = "gpt-test"\n'
        f'sqlite_home = "{source}"\n'
        f'log_dir = "{source / "log"}"\n'
        "\n[tui]\n"
        "animations = false\n",
        encoding="utf-8",
    )
    paths = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )

    result = sh.provision(paths)
    seeded = paths.config.read_text(encoding="utf-8")
    assert result.seeded == ("config.toml",)
    assert 'model = "gpt-test"' in seeded
    assert "[tui]" in seeded
    assert "animations = false" in seeded
    assert "sqlite_home" not in seeded
    assert "log_dir" not in seeded


def test_existing_config_is_revalidated_and_storage_redirects_are_removed(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    paths.config.write_text(
        f'model = "gpt-test"\nsqlite_home = "{source}"\nlog_dir = "{source}"\n',
        encoding="utf-8",
    )

    sh.provision(paths)

    selected = paths.config.read_text(encoding="utf-8")
    assert "sqlite_home" not in selected
    assert "log_dir" not in selected
    assert paths.config.stat().st_mode & 0o777 == 0o600


def test_preexisting_selected_config_symlink_fails_closed(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    paths.config.unlink()
    paths.config.symlink_to(source / "config.toml")

    with pytest.raises(RuntimeError, match="not a regular file"):
        sh.provision(paths)
    assert paths.config.is_symlink()


def test_auth_source_and_existing_target_links_must_be_exact_and_safe(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    database = source / "state_8.sqlite"
    database.write_bytes(b"SQLite format 3\x00runtime")
    source_auth = source / "auth.json"
    source_auth.unlink()
    source_auth.symlink_to(database)
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="unsafe-auth-source",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    result = sh.provision(paths)
    assert result.linked_credentials == ()
    assert not paths.auth.exists()

    source_auth.unlink()
    source_auth.write_text('{"safe": true}\n', encoding="utf-8")
    source_auth.chmod(0o600)
    paths.auth.symlink_to(database)
    with pytest.raises(RuntimeError, match="unsafe target"):
        sh.provision(paths)
    assert paths.auth.resolve() == database


def test_live_0700_tokenpak_and_0775_companion_create_private_codex_boundary(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    tokenpak_home.mkdir(mode=0o700)
    companion = tokenpak_home / "companion"
    companion.mkdir(mode=0o775)
    tokenpak_home.chmod(0o700)
    companion.chmod(0o775)
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="private-chain",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    old_umask = os.umask(0o022)
    try:
        sh.provision(paths)
    finally:
        os.umask(old_umask)

    assert tokenpak_home.stat().st_mode & 0o777 == 0o700
    assert companion.stat().st_mode & 0o777 == 0o775
    for directory in (
        tokenpak_home / "companion" / "codex",
        tokenpak_home / "companion" / "codex" / "sessions",
        paths.home,
    ):
        assert directory.stat().st_mode & 0o777 == 0o700

    paths.home.chmod(0o755)
    with pytest.raises(sh.HomeInUseError, match="owned 0700 directory"):
        sh.provision(paths)


def test_existing_nonprivate_codex_boundary_fails_closed(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    (tokenpak_home / "companion" / "codex").mkdir(parents=True)
    tokenpak_home.chmod(0o700)
    (tokenpak_home / "companion").chmod(0o775)
    (tokenpak_home / "companion" / "codex").chmod(0o775)
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="broad-codex-boundary",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )

    with pytest.raises(sh.HomeInUseError, match="owned 0700 directory"):
        sh.provision(paths)


@pytest.mark.parametrize("name", ["hooks.json", "AGENTS.md"])
def test_selected_managed_file_symlink_fails_closed(
    homes: tuple[Path, Path], tmp_path: Path, name: str
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    outside = tmp_path / f"outside-{name}"
    outside.write_text("must not change", encoding="utf-8")
    (paths.home / name).symlink_to(outside)

    with pytest.raises(RuntimeError, match="managed file is unsafe"):
        sh.provision(paths)
    assert outside.read_text(encoding="utf-8") == "must not change"


@pytest.mark.parametrize("alias_kind", ["symlink", "hardlink"])
@pytest.mark.parametrize(
    "database_name",
    ["state_99.sqlite", "state_99.sqlite-wal", "state_99.sqlite-shm", "state_99.sqlite-journal"],
)
def test_selected_runtime_database_alias_fails_closed(
    homes: tuple[Path, Path], tmp_path: Path, alias_kind: str, database_name: str
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    shared_database = source / database_name
    shared_database.write_bytes(b"SQLite format 3\x00shared")
    selected_database = paths.home / database_name
    if alias_kind == "symlink":
        selected_database.symlink_to(shared_database)
    else:
        os.link(shared_database, selected_database)

    with pytest.raises(RuntimeError, match="runtime database is aliased or unsafe"):
        sh.provision(paths)
    assert shared_database.read_bytes() == b"SQLite format 3\x00shared"


def test_selected_home_symlink_fails_closed(homes: tuple[Path, Path], tmp_path: Path) -> None:
    source, tokenpak_home = homes
    target = tmp_path / "redirect-target"
    target.mkdir()
    selected = tmp_path / "selected-link"
    selected.symlink_to(target, target_is_directory=True)
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        selected_home=selected,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    with pytest.raises(RuntimeError, match="unsafe"):
        sh.provision(paths)
    assert list(target.iterdir()) == []


def test_selected_environment_and_report_cover_every_managed_path(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    env = paths.environment({"PATH": "/test/bin"})
    report = dict(paths.report_rows())

    assert env[sh.ENV_CODEX_HOME] == str(paths.home)
    assert env[sh.ENV_SESSION_MODE] == "workspace"
    assert env["PATH"] == "/test/bin"
    for expected in (
        paths.home,
        paths.config,
        paths.auth,
        paths.mcp_config,
        paths.hooks,
        paths.agents,
        paths.skills_root,
        paths.pid_sentinel,
    ):
        assert str(expected) in report.values()


def test_pid_sentinel_is_validated_transferred_and_cleaned(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="lease-test",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)

    lease = sh.SessionLease.acquire(paths, session_id="lease-test")
    launcher_record = sh.read_pid_sentinel(paths.pid_sentinel)
    assert launcher_record is not None
    assert launcher_record.pid == os.getpid()
    assert sh.sentinel_is_live(launcher_record, expected_home=paths.home)

    child = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys; print('ready', flush=True); sys.stdin.readline()",
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        lease.transfer_to(child.pid)
        child_record = sh.read_pid_sentinel(paths.pid_sentinel)
        assert child_record is not None
        assert child_record.pid == child.pid
        assert child_record.session_id == launcher_record.session_id
        assert sh.sentinel_is_live(child_record, expected_home=paths.home)
        assert child.stdin is not None
        child.stdin.write("\n")
        child.stdin.close()
        assert child.wait(timeout=5) == 0
    finally:
        if child.poll() is None and child.stdin is not None and not child.stdin.closed:
            child.stdin.close()
        child.wait(timeout=5)
        if child.stdout is not None:
            child.stdout.close()
        lease.release()

    assert not paths.pid_sentinel.exists()


def test_live_home_lease_refuses_parallel_owner(homes: tuple[Path, Path], tmp_path: Path) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    first = sh.SessionLease.acquire(paths, session_id="first")
    try:
        with pytest.raises(sh.HomeInUseError, match="already claimed"):
            sh.SessionLease.acquire(paths, session_id="second")
    finally:
        first.release()


def test_invalid_pid_sentinel_is_never_replaced(homes: tuple[Path, Path], tmp_path: Path) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    paths.pid_sentinel.write_text("not validated json\n", encoding="utf-8")
    with pytest.raises(sh.HomeInUseError, match="refusing unsafe replacement"):
        sh.SessionLease.acquire(paths)
    assert paths.pid_sentinel.read_text(encoding="utf-8") == "not validated json\n"


@pytest.mark.parametrize("kind", ["symlink", "fifo", "oversized"])
def test_nonregular_or_oversized_pid_sentinel_fails_closed_without_blocking(
    homes: tuple[Path, Path], tmp_path: Path, kind: str
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    if kind == "symlink":
        outside = tmp_path / "outside-sentinel"
        outside.write_text("do not read", encoding="utf-8")
        paths.pid_sentinel.symlink_to(outside)
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO sentinel test requires POSIX")
        os.mkfifo(paths.pid_sentinel, 0o600)
    else:
        paths.pid_sentinel.write_bytes(b"x" * (sh._MAX_SENTINEL_BYTES + 1))
        paths.pid_sentinel.chmod(0o600)

    with pytest.raises(sh.HomeInUseError, match="refusing unsafe replacement"):
        sh.SessionLease.acquire(paths)


def test_stale_valid_sentinel_is_reclaimed_without_signalling(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="stale-test",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    stale = sh.PidSentinel(
        schema="tokenpak.codex.pid.v1",
        pid=2_000_000_000,
        start_time_ticks=1,
        session_id="stale",
        mode="isolated",
        home=str(paths.home.resolve()),
    )
    paths.pid_sentinel.write_text(json.dumps(asdict(stale)) + "\n", encoding="utf-8")
    paths.pid_sentinel.chmod(0o600)

    lease = sh.SessionLease.acquire(paths, session_id="replacement")
    try:
        current = sh.read_pid_sentinel(paths.pid_sentinel)
        assert current is not None
        assert current.pid == os.getpid()
        assert current.session_id == "replacement"
    finally:
        lease.release()


def test_initial_sentinel_short_write_never_publishes_partial_canonical(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    guard = paths.home / sh._LEASE_GUARD_NAME
    guard.write_bytes(b"\0")
    guard.chmod(0o600)
    original = sh._write_all

    def fail_sentinel(fd: int, data: bytes) -> None:
        if data.startswith(b'{"home"'):
            os.write(fd, data[:7])
            raise OSError("injected partial sentinel write")
        original(fd, data)

    monkeypatch.setattr(sh, "_write_all", fail_sentinel)
    with pytest.raises(OSError, match="injected partial"):
        sh.SessionLease.acquire(paths)
    assert not paths.pid_sentinel.exists()
    assert not any(sh._SENTINEL_TEMP_RE.fullmatch(path.name) for path in paths.home.iterdir())

    monkeypatch.setattr(sh, "_write_all", original)
    lease = sh.SessionLease.acquire(paths)
    lease.release()


def test_initial_sentinel_post_rename_enospc_rolls_back_exact_owner_for_retry(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="post-rename-enospc",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    original = sh._fsync_directory
    calls = 0

    def fail_canonical_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.ENOSPC, "injected post-rename fsync pressure")
        original(fd)

    monkeypatch.setattr(sh, "_fsync_directory", fail_canonical_fsync)
    with pytest.raises(OSError, match="post-rename fsync pressure"):
        sh.SessionLease.acquire(paths, session_id="post-rename-enospc")
    assert not paths.pid_sentinel.exists()

    lease = sh.SessionLease.acquire(paths, session_id="post-rename-enospc-retry")
    try:
        assert sh.read_pid_sentinel(paths.pid_sentinel) == lease.sentinel
    finally:
        lease.release()


def test_initial_sentinel_temp_fsync_enospc_leaves_no_untracked_artifact(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="temp-fsync-enospc",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    original = sh._fsync_directory

    def no_space(_fd: int) -> None:
        raise OSError(errno.ENOSPC, "injected temp directory fsync pressure")

    monkeypatch.setattr(sh, "_fsync_directory", no_space)
    with pytest.raises(OSError, match="temp directory fsync pressure"):
        sh.SessionLease.acquire(paths, session_id="temp-fsync-enospc")
    assert not paths.pid_sentinel.exists()
    assert not any(sh._is_sentinel_temp_candidate(path.name) for path in paths.home.iterdir())

    monkeypatch.setattr(sh, "_fsync_directory", original)
    lease = sh.SessionLease.acquire(paths, session_id="temp-fsync-enospc-retry")
    lease.release()


def test_transfer_post_rename_enospc_remains_exactly_owned_for_release(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="transfer-fsync-enospc",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    lease = sh.SessionLease.acquire(paths, session_id="transfer-fsync-enospc")
    lease.begin_transfer()
    original = sh._fsync_directory
    calls = 0

    def fail_canonical_fsync(fd: int) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError(errno.ENOSPC, "injected transfer canonical fsync pressure")
        original(fd)

    monkeypatch.setattr(sh, "_fsync_directory", fail_canonical_fsync)
    with pytest.raises(OSError, match="transfer canonical fsync pressure"):
        lease.transfer_to(os.getpid())
    current = sh.read_pid_sentinel(paths.pid_sentinel)
    assert current == lease.sentinel
    assert current is not None and current.pid == os.getpid()

    monkeypatch.setattr(sh, "_fsync_directory", original)
    assert lease.release() is True
    assert not paths.pid_sentinel.exists()
    assert not any(sh._is_sentinel_temp_candidate(path.name) for path in paths.home.iterdir())


def test_partial_acquire_temp_is_recovered_but_partial_transfer_fails_closed(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    acquire_temp = paths.home / f".codex.pid.acquire.{uuid.uuid4().hex}.tmp"
    acquire_temp.write_text("partial", encoding="utf-8")
    acquire_temp.chmod(0o600)

    lease = sh.SessionLease.acquire(paths)
    lease.release()
    assert not acquire_temp.exists()

    transfer_temp = paths.home / f".codex.pid.transfer.{uuid.uuid4().hex}.tmp"
    transfer_temp.write_text("partial", encoding="utf-8")
    transfer_temp.chmod(0o600)
    with pytest.raises(sh.HomeInUseError, match="partial transfer sentinel"):
        sh.SessionLease.acquire(paths)
    assert transfer_temp.exists()


def test_complete_stale_acquire_temp_is_reclaimed(homes: tuple[Path, Path], tmp_path: Path) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    stale = sh.PidSentinel(
        schema="tokenpak.codex.pid.v1",
        pid=2_000_000_000,
        start_time_ticks=1,
        session_id="crashed-acquire",
        mode="workspace",
        home=str(paths.home.resolve()),
    )
    temp = paths.home / f".codex.pid.acquire.{uuid.uuid4().hex}.tmp"
    temp.write_text(json.dumps(asdict(stale)) + "\n", encoding="utf-8")
    temp.chmod(0o600)

    lease = sh.SessionLease.acquire(paths)
    try:
        assert not temp.exists()
        assert sh.read_pid_sentinel(paths.pid_sentinel) == lease.sentinel
    finally:
        lease.release()


def _write_proc_stat(
    proc_root: Path, pid: int, *, state: str = "S", start_ticks: int = 100
) -> None:
    stat_path = proc_root / str(pid) / "stat"
    stat_path.parent.mkdir(parents=True)
    fields = [state, *(["0"] * 18), str(start_ticks)]
    stat_path.write_text(f"{pid} (fixture process) {' '.join(fields)}\n", encoding="utf-8")


def test_portable_posix_identity_uses_locale_stable_start_time(monkeypatch) -> None:
    if os.name != "posix":
        pytest.skip("POSIX ps fallback test")
    captured: dict[str, object] = {}

    class Result:
        returncode = 0
        stdout = "S+ Sat Jul 11 18:00:00 2026\n"

    def fake_run(*args, **kwargs):
        captured["args"] = args
        captured["env"] = kwargs["env"]
        return Result()

    monkeypatch.setattr(sh.subprocess, "run", fake_run)

    first = sh._portable_process_identity(123)
    second = sh._portable_process_identity(123)

    assert first == second
    assert first is not None and first[0] == "S" and first[1] > 0
    assert captured["env"]["LC_ALL"] == "C"


def test_proc_stat_read_failure_is_unknown_without_portable_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proc_root = tmp_path / "proc"
    _write_proc_stat(proc_root, 123, start_ticks=77)
    target = proc_root / "123" / "stat"
    original = Path.read_text

    def unreadable(path: Path, *args, **kwargs):
        if path == target:
            raise PermissionError(errno.EACCES, "injected unreadable proc stat")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)

    evidence, identity = sh._process_identity_evidence(123, proc_root=proc_root)

    assert evidence == sh._PROCESS_UNKNOWN
    assert identity is None


def test_linux_proc_read_failure_never_publishes_portable_lease_identity(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="linux-proc-read-failure",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    target = Path("/proc") / str(os.getpid()) / "stat"
    original = Path.read_text

    def unreadable(path: Path, *args, **kwargs):
        if path == target:
            raise PermissionError(errno.EACCES, "injected Linux proc stat failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(Path, "read_text", unreadable)
    monkeypatch.setattr(
        sh,
        "_portable_process_identity",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("Linux lease publication must not cross identity domains")
        ),
    )

    with pytest.raises(RuntimeError, match="cannot validate launcher PID"):
        sh.SessionLease.acquire(paths)

    assert not paths.home.exists()
    assert not paths.pid_sentinel.exists()


def test_nonlinux_default_proc_absence_uses_portable_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = 2_000_000_000
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        sh,
        "_portable_process_identity",
        lambda candidate: ("S", 77) if candidate == pid else None,
    )

    assert sh._proc_identity(pid) == ("S", 77)


def test_shared_lease_path_backend_supports_platforms_without_directory_fds(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, _tokenpak_home = homes
    paths = sh.select_paths(
        "shared",
        workspace_dir=tmp_path,
        source_home=source,
        selected_home=source,
    )

    def path_backend(selected: sh.SessionPaths):
        selected.home.mkdir(mode=0o700, parents=True, exist_ok=True)
        return None

    monkeypatch.setattr(sh, "_open_selected_home", path_backend)
    lease = sh.SessionLease.acquire(paths, session_id="path-backend")
    try:
        assert sh.read_pid_sentinel(paths.pid_sentinel) == lease.sentinel
    finally:
        lease.release()
    assert not paths.pid_sentinel.exists()


def test_stopped_lease_owner_is_still_live_and_refused(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    proc_root = tmp_path / "proc"
    _write_proc_stat(proc_root, 101, state="T", start_ticks=11)
    _write_proc_stat(proc_root, 202, state="R", start_ticks=22)
    stopped = sh.SessionLease.acquire(paths, pid=101, session_id="stopped", proc_root=proc_root)
    try:
        with pytest.raises(sh.HomeInUseError, match="already claimed by PID 101"):
            sh.SessionLease.acquire(paths, pid=202, session_id="second", proc_root=proc_root)
    finally:
        stopped.release()


def test_dead_process_cannot_acquire_or_receive_lease(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="dead-owner",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    proc_root = tmp_path / "proc"
    _write_proc_stat(proc_root, 303, state="Z", start_ticks=33)
    _write_proc_stat(proc_root, 606, state="R", start_ticks=66)
    with pytest.raises(RuntimeError, match="is not running"):
        sh.SessionLease.acquire(paths, pid=303, proc_root=proc_root)
    lease = sh.SessionLease.acquire(paths, pid=606, proc_root=proc_root)
    try:
        with pytest.raises(RuntimeError, match="is not running"):
            lease.transfer_to(303)
    finally:
        lease.release()


def test_live_wrong_home_sentinel_is_preserved(homes: tuple[Path, Path], tmp_path: Path) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    proc_root = tmp_path / "proc"
    _write_proc_stat(proc_root, 404, state="S", start_ticks=44)
    _write_proc_stat(proc_root, 505, state="R", start_ticks=55)
    wrong = sh.PidSentinel(
        schema="tokenpak.codex.pid.v1",
        pid=404,
        start_time_ticks=44,
        session_id="wrong-home",
        mode="workspace",
        home=str(tmp_path / "different-home"),
    )
    original = json.dumps(asdict(wrong)) + "\n"
    paths.pid_sentinel.write_text(original, encoding="utf-8")
    paths.pid_sentinel.chmod(0o600)

    with pytest.raises(sh.HomeInUseError, match="bound to another home"):
        sh.SessionLease.acquire(paths, pid=505, proc_root=proc_root)
    assert paths.pid_sentinel.read_text(encoding="utf-8") == original


def test_release_removes_only_exact_sentinel(homes: tuple[Path, Path], tmp_path: Path) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="exact-release",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    lease = sh.SessionLease.acquire(paths, session_id="exact-release")
    changed = sh.PidSentinel(
        schema=lease.sentinel.schema,
        pid=lease.sentinel.pid,
        start_time_ticks=lease.sentinel.start_time_ticks + 1,
        session_id=lease.sentinel.session_id,
        mode=lease.sentinel.mode,
        home=lease.sentinel.home,
    )
    paths.pid_sentinel.write_text(json.dumps(asdict(changed)) + "\n", encoding="utf-8")
    paths.pid_sentinel.chmod(0o600)

    assert lease.release() is False
    assert sh.read_pid_sentinel(paths.pid_sentinel) == changed


def _retention_home(
    source: Path,
    tokenpak_home: Path,
    tmp_path: Path,
    name: str,
    *,
    age_s: float = 120.0,
    payload_bytes: int = 0,
) -> sh.SessionPaths:
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id=name,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    if payload_bytes:
        (paths.home / "payload.bin").write_bytes(b"x" * payload_bytes)
    timestamp = time.time() - age_s
    os.utime(paths.home, (timestamp, timestamp))
    return paths


def test_retention_enforces_five_homes_oldest_first(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    created = [
        _retention_home(source, tokenpak_home, tmp_path, f"session-{index}", age_s=600 - index)
        for index in range(6)
    ]

    result = sh.cleanup_isolated_homes(tokenpak_home)

    assert result.removed == (created[0].home,)
    assert len(result.after.homes) == sh.RETENTION_MAX_HOMES
    assert all(path.home.exists() for path in created[1:])


def test_retention_age_and_size_thresholds(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, tokenpak_home = homes
    exact = _retention_home(
        source,
        tokenpak_home,
        tmp_path,
        "exact-age",
        age_s=sh.RETENTION_MAX_AGE_S - 10,
    )
    old = _retention_home(
        source,
        tokenpak_home,
        tmp_path,
        "over-age",
        age_s=sh.RETENTION_MAX_AGE_S + 10,
    )
    result = sh.cleanup_isolated_homes(tokenpak_home)
    assert old.home in result.removed
    assert exact.home.exists()

    sized = _retention_home(source, tokenpak_home, tmp_path, "over-size", payload_bytes=4096)
    monkeypatch.setattr(sh, "RETENTION_MAX_TOTAL_BYTES", 1)
    result = sh.cleanup_isolated_homes(tokenpak_home)
    assert sized.home in result.removed


def test_retention_plan_exact_boundaries_do_not_evict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sh, "RETENTION_MAX_TOTAL_BYTES", 100)
    info = sh._IsolatedHomeInfo(
        path=tmp_path / "exact",
        device=1,
        inode=1,
        mtime=1.0,
        age_s=sh.RETENTION_MAX_AGE_S,
        size_bytes=100,
        size_complete=True,
        state="orphan-stale",
    )
    report = sh._RetentionReport(tmp_path, (info,), 100, True)
    assert sh._retention_plan(report, preserve_home=None, remove_all_orphans=False) == []

    over = sh._IsolatedHomeInfo(
        **{**info.__dict__, "path": tmp_path / "over", "age_s": info.age_s + 1}
    )
    report = sh._RetentionReport(tmp_path, (over,), 101, True)
    assert sh._retention_plan(report, preserve_home=None, remove_all_orphans=False) == [
        (over, "age")
    ]
    over_size = sh._IsolatedHomeInfo(
        **{
            **info.__dict__,
            "path": tmp_path / "over-size",
            "age_s": sh.RETENTION_MAX_AGE_S - 1,
            "size_bytes": 101,
        }
    )
    report = sh._RetentionReport(tmp_path, (over_size,), 101, True)
    assert sh._retention_plan(report, preserve_home=None, remove_all_orphans=False) == [
        (over_size, "size")
    ]


def test_retention_preserves_live_and_malformed_sentinel_homes(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    live = _retention_home(source, tokenpak_home, tmp_path, "live-retention")
    malformed = _retention_home(source, tokenpak_home, tmp_path, "malformed-retention")
    malformed.pid_sentinel.write_text("partial", encoding="utf-8")
    malformed.pid_sentinel.chmod(0o600)
    lease = sh.SessionLease.acquire(live, session_id="live-retention")
    try:
        result = sh.cleanup_isolated_homes(
            tokenpak_home,
            remove_all_orphans=True,
        )
        assert result.removed == ()
        assert live.home.exists()
        assert malformed.home.exists()
        states = {home.path.name: home.state for home in result.after.homes}
        assert states["live-retention"] == "active"
        assert states["malformed-retention"] == "unsafe"
    finally:
        lease.release()


def _write_private_sentinel(path: Path, sentinel: sh.PidSentinel) -> None:
    path.write_text(json.dumps(asdict(sentinel)) + "\n", encoding="utf-8")
    path.chmod(0o600)


@pytest.mark.parametrize("phase", ["acquire", "transfer"])
def test_retention_partial_temp_artifact_protects_home(
    homes: tuple[Path, Path], tmp_path: Path, phase: str
) -> None:
    source, tokenpak_home = homes
    candidate = _retention_home(source, tokenpak_home, tmp_path, f"partial-{phase}")
    artifact = candidate.home / f".codex.pid.{phase}.{uuid.uuid4().hex}.tmp"
    artifact.write_text("partial", encoding="utf-8")
    artifact.chmod(0o600)
    old = time.time() - 300
    os.utime(candidate.home, (old, old))

    result = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert result.removed == ()
    assert candidate.home.is_dir()
    assert artifact.is_file()
    state = {home.path: home.state for home in result.after.homes}
    assert state[candidate.home] == "unsafe"
    assert result.errors and "inventory incomplete" in result.errors[-1]


@pytest.mark.parametrize("suffix", ["not-a-publisher-uuid", "contains\nnewline"])
def test_retention_invalid_temp_pattern_is_recognized_and_protected(
    homes: tuple[Path, Path], tmp_path: Path, suffix: str
) -> None:
    source, tokenpak_home = homes
    candidate = _retention_home(source, tokenpak_home, tmp_path, "invalid-temp-pattern")
    artifact = candidate.home / f".codex.pid.acquire.{suffix}.tmp"
    artifact.write_text("partial", encoding="utf-8")
    artifact.chmod(0o600)
    old = time.time() - 300
    os.utime(candidate.home, (old, old))

    result = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert result.removed == ()
    assert candidate.home.is_dir()
    assert artifact.is_file()
    assert result.after.homes[0].state == "unsafe"


@pytest.mark.parametrize("phase", ["acquire", "transfer"])
def test_retention_live_temp_artifact_protects_before_sqlite_open(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str
) -> None:
    from tokenpak.companion.codex import state_lock

    source, tokenpak_home = homes
    candidate = _retention_home(source, tokenpak_home, tmp_path, f"live-{phase}")
    identity = sh._proc_identity(os.getpid())
    assert identity is not None
    live = sh.PidSentinel(
        schema="tokenpak.codex.pid.v1",
        pid=os.getpid(),
        start_time_ticks=identity[1],
        session_id=f"live-{phase}",
        mode="isolated",
        home=str(candidate.home.resolve()),
    )
    artifact = candidate.home / f".codex.pid.{phase}.{uuid.uuid4().hex}.tmp"
    _write_private_sentinel(artifact, live)
    old = time.time() - 300
    os.utime(candidate.home, (old, old))
    assert not list(candidate.home.glob("*.sqlite*"))
    monkeypatch.setattr(
        state_lock,
        "probe",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("temp-protected home must not reach SQLite holder probing")
        ),
    )

    result = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert result.removed == ()
    assert candidate.home.is_dir()
    info = next(home for home in result.after.homes if home.path == candidate.home)
    assert info.state == "active"
    assert info.pid == os.getpid()


def test_retention_dead_parent_and_live_child_transfer_temp_preserves_home(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokenpak.companion.codex import state_lock

    source, tokenpak_home = homes
    candidate = _retention_home(source, tokenpak_home, tmp_path, "dead-parent-live-child")
    dead_parent = sh.PidSentinel(
        schema="tokenpak.codex.pid.v1",
        pid=2_000_000_000,
        start_time_ticks=1,
        session_id="dead-parent-live-child",
        mode="isolated",
        home=str(candidate.home.resolve()),
    )
    _write_private_sentinel(candidate.pid_sentinel, dead_parent)
    identity = sh._proc_identity(os.getpid())
    assert identity is not None
    live_child = sh.PidSentinel(
        schema="tokenpak.codex.pid.v1",
        pid=os.getpid(),
        start_time_ticks=identity[1],
        session_id=dead_parent.session_id,
        mode="isolated",
        home=dead_parent.home,
    )
    transfer = candidate.home / f".codex.pid.transfer.{uuid.uuid4().hex}.tmp"
    _write_private_sentinel(transfer, live_child)
    old = time.time() - 300
    os.utime(candidate.home, (old, old))
    assert not list(candidate.home.glob("*.sqlite*"))
    monkeypatch.setattr(
        state_lock,
        "probe",
        lambda *_a, **_k: (_ for _ in ()).throw(
            AssertionError("live transfer temp must protect before SQLite probing")
        ),
    )

    result = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert result.removed == ()
    assert candidate.home.is_dir()
    assert sh.read_pid_sentinel(candidate.pid_sentinel) == dead_parent
    assert sh.read_pid_sentinel(transfer) == live_child
    info = next(home for home in result.after.homes if home.path == candidate.home)
    assert info.state == "active"
    assert info.pid == os.getpid()


def test_retention_unknown_temp_pid_evidence_fails_closed(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, tokenpak_home = homes
    candidate = _retention_home(source, tokenpak_home, tmp_path, "unknown-temp-pid")
    identity = sh._proc_identity(os.getpid())
    assert identity is not None
    live = sh.PidSentinel(
        schema="tokenpak.codex.pid.v1",
        pid=os.getpid(),
        start_time_ticks=identity[1],
        session_id="unknown-temp-pid",
        mode="isolated",
        home=str(candidate.home.resolve()),
    )
    artifact = candidate.home / f".codex.pid.acquire.{uuid.uuid4().hex}.tmp"
    _write_private_sentinel(artifact, live)
    old = time.time() - 300
    os.utime(candidate.home, (old, old))
    monkeypatch.setattr(
        sh,
        "_process_identity_evidence",
        lambda *_a, **_k: (sh._PROCESS_UNKNOWN, None),
    )

    result = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert result.removed == ()
    assert candidate.home.is_dir()
    assert result.after.homes[0].state == "unsafe"


def test_retention_unknown_canonical_pid_evidence_fails_closed(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, tokenpak_home = homes
    candidate = _retention_home(source, tokenpak_home, tmp_path, "unknown-canonical-pid")
    identity = sh._proc_identity(os.getpid())
    assert identity is not None
    canonical = sh.PidSentinel(
        schema="tokenpak.codex.pid.v1",
        pid=os.getpid(),
        start_time_ticks=identity[1],
        session_id="unknown-canonical-pid",
        mode="isolated",
        home=str(candidate.home.resolve()),
    )
    _write_private_sentinel(candidate.pid_sentinel, canonical)
    old = time.time() - 300
    os.utime(candidate.home, (old, old))
    monkeypatch.setattr(
        sh,
        "_process_identity_evidence",
        lambda *_a, **_k: (sh._PROCESS_UNKNOWN, None),
    )

    result = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert result.removed == ()
    assert candidate.home.is_dir()
    assert result.after.homes[0].state == "unsafe"


def test_retention_pid_reuse_is_proven_stale_and_reclaimable(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    candidate = _retention_home(source, tokenpak_home, tmp_path, "reused-canonical-pid")
    proc_root = tmp_path / "reuse-proc"
    _write_proc_stat(proc_root, 4242, start_ticks=22)
    reused = sh.PidSentinel(
        schema="tokenpak.codex.pid.v1",
        pid=4242,
        start_time_ticks=11,
        session_id="reused-canonical-pid",
        mode="isolated",
        home=str(candidate.home.resolve()),
    )
    _write_private_sentinel(candidate.pid_sentinel, reused)
    old = time.time() - 300
    os.utime(candidate.home, (old, old))

    result = sh.cleanup_isolated_homes(
        tokenpak_home,
        remove_all_orphans=True,
        proc_root=proc_root,
    )

    assert result.removed == (candidate.home,)
    assert not candidate.home.exists()


def test_missing_proc_root_is_unknown_and_never_reclaimable(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    candidate = _retention_home(source, tokenpak_home, tmp_path, "missing-proc-root")
    canonical = sh.PidSentinel(
        schema="tokenpak.codex.pid.v1",
        pid=4242,
        start_time_ticks=11,
        session_id="missing-proc-root",
        mode="isolated",
        home=str(candidate.home.resolve()),
    )
    _write_private_sentinel(candidate.pid_sentinel, canonical)
    old = time.time() - 300
    os.utime(candidate.home, (old, old))
    missing_proc = tmp_path / "proc-not-mounted"

    result = sh.cleanup_isolated_homes(
        tokenpak_home,
        remove_all_orphans=True,
        proc_root=missing_proc,
    )

    assert result.removed == ()
    assert candidate.home.is_dir()
    assert result.after.homes[0].state == "unsafe"


def test_retention_complete_dead_transfer_remains_protected_handoff(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    candidate = _retention_home(source, tokenpak_home, tmp_path, "dead-handoff")
    handoff = sh.PidSentinel(
        schema="tokenpak.codex.pid.v1",
        pid=2_000_000_000,
        start_time_ticks=1,
        session_id="dead-handoff",
        mode="isolated",
        home=str(candidate.home.resolve()),
    )
    artifact = candidate.home / f".codex.pid.transfer.{uuid.uuid4().hex}.tmp"
    _write_private_sentinel(artifact, handoff)
    old = time.time() - 300
    os.utime(candidate.home, (old, old))

    result = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert result.removed == ()
    assert candidate.home.is_dir()
    assert result.after.homes[0].state == "handoff"
    assert result.errors and "inventory incomplete" in result.errors[-1]


def test_retention_never_touches_workspace_homes(homes: tuple[Path, Path], tmp_path: Path) -> None:
    source, tokenpak_home = homes
    workspace = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(workspace)
    isolated = _retention_home(source, tokenpak_home, tmp_path, "isolated-only")

    result = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert result.removed == (isolated.home,)
    assert workspace.home.exists()


def test_isolated_leaf_creation_and_sentinel_publication_hold_retention_guard(
    homes: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "isolated",
        workspace_dir=tmp_path,
        session_id="coordinated-publication",
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    entered_leaf = threading.Event()
    allow_publication = threading.Event()
    cleanup_done = threading.Event()
    original = sh._open_isolated_leaf_at
    leases: list[sh.SessionLease] = []
    cleanups: list[sh._CleanupResult] = []
    errors: list[BaseException] = []
    monkeypatch.setattr(sh, "_GUARD_LOCK_TIMEOUT_S", 30.0)

    def paused_leaf(selected: sh.SessionPaths, root_fd: int) -> int:
        entered_leaf.set()
        if not allow_publication.wait(5):
            raise RuntimeError("timed out waiting to publish isolated leaf")
        return original(selected, root_fd)

    def acquire() -> None:
        try:
            leases.append(sh.SessionLease.acquire(paths, session_id="coordinated-publication"))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    def cleanup() -> None:
        try:
            cleanups.append(sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True))
        except BaseException as exc:  # pragma: no cover - asserted below
            errors.append(exc)
        finally:
            cleanup_done.set()

    monkeypatch.setattr(sh, "_open_isolated_leaf_at", paused_leaf)
    acquire_thread = threading.Thread(target=acquire)
    acquire_thread.start()
    assert entered_leaf.wait(5)

    cleanup_thread = threading.Thread(target=cleanup)
    cleanup_thread.start()
    assert not cleanup_done.wait(0.1)
    allow_publication.set()
    acquire_thread.join(30)
    cleanup_thread.join(30)

    assert not acquire_thread.is_alive()
    assert not cleanup_thread.is_alive()
    assert errors == []
    assert len(leases) == 1
    assert len(cleanups) == 1
    assert cleanups[0].removed == ()
    assert paths.home.is_dir()
    assert sh.read_pid_sentinel(paths.pid_sentinel) == leases[0].sentinel
    leases[0].release()


def test_lifecycle_thread_guard_acquisition_is_bounded(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, tokenpak_home = homes
    paths = sh.select_paths(
        "workspace",
        workspace_dir=tmp_path,
        tokenpak_home=tokenpak_home,
        source_home=source,
    )
    sh.provision(paths)
    guard_file = tmp_path / "bounded-thread-guard"
    guard_fd = os.open(guard_file, os.O_RDWR | os.O_CREAT, 0o600)
    held = threading.Event()
    release = threading.Event()

    def hold_thread_guard() -> None:
        with sh._THREAD_LEASE_LOCK:
            held.set()
            release.wait(5)

    holder = threading.Thread(target=hold_thread_guard)
    holder.start()
    assert held.wait(5)
    monkeypatch.setattr(sh, "_GUARD_LOCK_TIMEOUT_S", 0.05)
    started = time.monotonic()
    try:
        with pytest.raises(sh.HomeInUseError, match="thread guard"):
            with sh._bounded_guard_lock(guard_fd):
                raise AssertionError("bounded guard unexpectedly acquired")
        assert time.monotonic() - started < 0.25
    finally:
        os.close(guard_fd)
        release.set()
        holder.join(5)
    assert not holder.is_alive()


def test_cleanup_failure_leaves_quarantine_and_durable_receipt(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, tokenpak_home = homes
    isolated = _retention_home(source, tokenpak_home, tmp_path, "quarantine-crash")

    def fail_cleanup(*_args, **_kwargs):
        raise sh.HomeInUseError("injected cleanup crash")

    original = sh._remove_tree_contents_at
    monkeypatch.setattr(sh, "_remove_tree_contents_at", fail_cleanup)
    result = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert result.errors and "injected cleanup crash" in result.errors[0]
    assert not isolated.home.exists()
    root = sh.sessions_root(tokenpak_home)
    assert any(path.name.startswith(sh._RETENTION_QUARANTINE_PREFIX) for path in root.iterdir())
    receipt = root / sh._RETENTION_RECEIPT_NAME
    assert '"action": "planned"' in receipt.read_text(encoding="utf-8")

    monkeypatch.setattr(sh, "_remove_tree_contents_at", original)
    recovered = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert recovered.errors == ()
    assert recovered.removed == (isolated.home,)
    assert not any(path.name.startswith(sh._RETENTION_QUARANTINE_PREFIX) for path in root.iterdir())
    assert '"action": "completed"' in receipt.read_text(encoding="utf-8")


def test_unproven_quarantine_is_preserved_with_explicit_error(
    homes: tuple[Path, Path],
) -> None:
    _source, tokenpak_home = homes
    opened = sh._open_managed_sessions_root(tokenpak_home, create=True)
    assert opened is not None
    _root, root_fd = opened
    os.close(root_fd)
    root = sh.sessions_root(tokenpak_home)
    quarantine = root / f"{sh._RETENTION_QUARANTINE_PREFIX}{uuid.uuid4().hex}"
    quarantine.mkdir(mode=0o700)

    result = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert result.removed == ()
    assert quarantine.is_dir()
    assert result.errors and "no planned retention receipt" in result.errors[0]


def test_conflicting_quarantine_receipts_never_purge(
    homes: tuple[Path, Path],
) -> None:
    _source, tokenpak_home = homes
    opened = sh._open_managed_sessions_root(tokenpak_home, create=True)
    assert opened is not None
    root, root_fd = opened
    quarantine_name = f"{sh._RETENTION_QUARANTINE_PREFIX}{uuid.uuid4().hex}"
    quarantine = root / quarantine_name
    quarantine.mkdir(mode=0o700)
    (quarantine / "keep.txt").write_text("preserve", encoding="utf-8")
    info = quarantine.stat()
    base = {
        "schema": "tokenpak.codex.retention.v1",
        "action": "planned",
        "home": "receipt-victim",
        "device": info.st_dev,
        "inode": info.st_ino,
        "size_bytes": 1,
        "reason": "count",
        "quarantine": quarantine_name,
        "timestamp_ns": time.time_ns(),
    }
    try:
        sh._append_retention_receipt(root_fd, base)
        sh._append_retention_receipt(
            root_fd,
            {**base, "home": "different-home", "inode": info.st_ino + 1},
        )
    finally:
        os.close(root_fd)

    result = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert result.removed == ()
    assert quarantine.is_dir()
    assert (quarantine / "keep.txt").read_text(encoding="utf-8") == "preserve"
    assert any("conflicting planned retention receipts" in error for error in result.errors)


def test_malformed_quarantine_reason_is_preserved_as_invalid_receipt(
    homes: tuple[Path, Path],
) -> None:
    _source, tokenpak_home = homes
    opened = sh._open_managed_sessions_root(tokenpak_home, create=True)
    assert opened is not None
    root, root_fd = opened
    os.close(root_fd)
    quarantine_name = f"{sh._RETENTION_QUARANTINE_PREFIX}{uuid.uuid4().hex}"
    quarantine = root / quarantine_name
    quarantine.mkdir(mode=0o700)
    (quarantine / "keep.txt").write_text("preserve", encoding="utf-8")
    info = quarantine.stat()
    malformed = {
        "schema": "tokenpak.codex.retention.v1",
        "action": "planned",
        "home": "receipt-victim",
        "device": info.st_dev,
        "inode": info.st_ino,
        "size_bytes": 1,
        "quarantine": quarantine_name,
        "timestamp_ns": time.time_ns(),
    }
    receipt = root / sh._RETENTION_RECEIPT_NAME
    receipt.write_text(json.dumps(malformed) + "\n", encoding="utf-8")
    receipt.chmod(0o600)

    result = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert result.removed == ()
    assert quarantine.is_dir()
    assert (quarantine / "keep.txt").read_text(encoding="utf-8") == "preserve"
    assert any("invalid retention receipt" in error for error in result.errors)


def test_incomplete_retention_inventory_preserves_all_ordinary_homes(
    homes: tuple[Path, Path], tmp_path: Path
) -> None:
    source, tokenpak_home = homes
    candidate = _retention_home(source, tokenpak_home, tmp_path, "rechecked")
    outside = tmp_path / "outside-retention"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    escape = sh.sessions_root(tokenpak_home) / "escape"
    escape.symlink_to(outside, target_is_directory=True)

    result = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert result.removed == ()
    assert result.errors and "inventory incomplete" in result.errors[-1]
    assert candidate.home.exists()
    assert escape.is_symlink()
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_retention_rechecks_candidate_before_removal(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, tokenpak_home = homes
    candidate = _retention_home(source, tokenpak_home, tmp_path, "rechecked")
    original = sh._inspect_isolated_home_at
    calls = 0

    def becomes_active(*args, **kwargs):
        nonlocal calls
        info = original(*args, **kwargs)
        if info.path == candidate.home:
            calls += 1
            if calls == 2:
                return sh._IsolatedHomeInfo(
                    **{**info.__dict__, "state": "active", "pid": os.getpid()}
                )
        return info

    monkeypatch.setattr(sh, "_inspect_isolated_home_at", becomes_active)
    result = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert result.errors and "changed before cleanup" in result.errors[0]
    assert candidate.home.exists()


def test_retention_rechecks_sentinel_artifacts_after_holder_probe(
    homes: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tokenpak.companion.codex import state_lock

    source, tokenpak_home = homes
    candidate = _retention_home(source, tokenpak_home, tmp_path, "post-probe-race")
    identity = sh._proc_identity(os.getpid())
    assert identity is not None
    live = sh.PidSentinel(
        schema="tokenpak.codex.pid.v1",
        pid=os.getpid(),
        start_time_ticks=identity[1],
        session_id="post-probe-race",
        mode="isolated",
        home=str(candidate.home.resolve()),
    )
    transfer = candidate.home / f".codex.pid.transfer.{uuid.uuid4().hex}.tmp"

    def raced_probe(home, *, deadline=None):
        assert home == candidate.home
        _write_private_sentinel(transfer, live)
        return state_lock.LockStatus(
            home=home,
            db_path=home / "state.sqlite",
            exists=False,
            locked=False,
        )

    monkeypatch.setattr(state_lock, "probe", raced_probe)

    result = sh.cleanup_isolated_homes(tokenpak_home, remove_all_orphans=True)

    assert result.removed == ()
    assert candidate.home.is_dir()
    assert transfer.is_file()
    assert result.errors and "changed after holder inspection" in result.errors[0]
