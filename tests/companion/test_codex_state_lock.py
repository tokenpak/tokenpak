# SPDX-License-Identifier: Apache-2.0
"""Focused tests for read-only Codex SQLite holder diagnostics."""

from __future__ import annotations

import errno
import os
import select
import signal
import sqlite3
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

from tokenpak.companion.codex import session_home as sh
from tokenpak.companion.codex import state_lock as sl


@pytest.fixture
def codex_home(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    home = tmp_path / ".codex"
    home.mkdir(parents=True)
    return home


def _make_db(home: Path, name: str = sl.STATE_DB_NAME) -> Path:
    db = home / name
    connection = sqlite3.connect(db)
    connection.execute("CREATE TABLE IF NOT EXISTS records (id INTEGER)")
    connection.commit()
    connection.close()
    return db


_HOLDER_SCRIPT = r"""
import sqlite3
import sys

database, mode = sys.argv[1:]
connection = sqlite3.connect(database, isolation_level=None)
if mode == "wal":
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("BEGIN IMMEDIATE")
else:
    connection.execute("BEGIN EXCLUSIVE")
print("ready", flush=True)
sys.stdin.readline()
connection.execute("ROLLBACK")
connection.close()
"""


@contextmanager
def _sqlite_holder(db: Path, mode: str = "exclusive"):
    """Start and fully reap a test-owned SQLite holder process."""
    process = subprocess.Popen(
        [sys.executable, "-c", _HOLDER_SCRIPT, str(db), mode],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert process.stdout is not None
        readable, _, _ = select.select([process.stdout], [], [], 10)
        if not readable:
            stderr = process.stderr.read() if process.poll() is not None and process.stderr else ""
            pytest.fail(f"SQLite holder did not become ready: {stderr}")
        assert process.stdout.readline().strip() == "ready"
        yield process
    finally:
        if process.poll() is None and sl._pid_stopped(process.pid):
            os.kill(process.pid, signal.SIGCONT)
        if process.poll() is None and process.stdin is not None:
            try:
                process.stdin.write("\n")
                process.stdin.flush()
            except BrokenPipeError:
                pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.terminate()
            process.wait(timeout=10)
        if process.stdin is not None:
            process.stdin.close()
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()


def _wait_for_state(pid: int, *, stopped: bool) -> None:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if sl._pid_stopped(pid) is stopped:
            return
        time.sleep(0.01)
    pytest.fail(f"test holder PID {pid} did not reach expected stopped={stopped} state")


def _write_synthetic_process(
    proc_root: Path,
    pid: int,
    *,
    uid: int,
    state: str = "S",
    name: str = "codex",
    with_fd_dir: bool = True,
) -> Path:
    process = proc_root / str(pid)
    process.mkdir(parents=True)
    if with_fd_dir:
        (process / "fd").mkdir()
        (process / "fdinfo").mkdir()
    (process / "status").write_text(
        f"Name:\t{name}\nTgid:\t{pid}\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\n"
    )
    fields = [state, *(["0"] * 18), "9001"]
    (process / "stat").write_text(f"{pid} (fixture holder) {' '.join(fields)}\n")
    (process / "comm").write_text(name + "\n")
    (process / "cmdline").write_bytes(name.encode() + b"\0")
    return process


def _complete_empty_proc(tmp_path: Path) -> Path:
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    (proc_root / "locks").write_text("")
    return proc_root


def test_probe_absent_db_is_unlocked(codex_home):
    status = sl.probe(codex_home)

    assert status.exists is False
    assert status.locked is False
    assert status.diagnostics_complete is True
    assert "uncontended" in status.detail


def test_probe_free_db_is_unlocked_without_sqlite_client(codex_home, tmp_path):
    _make_db(codex_home)

    status = sl.probe(codex_home, proc_root=_complete_empty_proc(tmp_path))

    assert status.exists is True
    assert status.locked is False
    assert status.diagnostics_complete is True
    assert "sqlite3" not in sl.__dict__


def test_missing_proc_fails_closed_when_database_exists(codex_home, tmp_path):
    db = _make_db(codex_home)

    status = sl.probe(codex_home, proc_root=tmp_path / "missing-proc")

    assert status.exists is True
    assert status.db_path == db
    assert status.locked is True
    assert status.diagnostics_complete is False
    assert status.holder_pids == []
    assert "/proc holder inspection is incomplete" in status.detail
    assert "refusing unsafe access" in status.detail


def test_unreadable_database_discovery_fails_closed(codex_home, tmp_path, monkeypatch):
    _make_db(codex_home)
    proc_root = _complete_empty_proc(tmp_path)
    original_iterdir = Path.iterdir

    def guarded_iterdir(path):
        if path == codex_home:
            raise PermissionError("fixture denies database discovery")
        return original_iterdir(path)

    monkeypatch.setattr(Path, "iterdir", guarded_iterdir)

    status = sl.probe(codex_home, proc_root=proc_root)

    assert status.exists is True
    assert status.locked is True
    assert status.diagnostics_complete is False
    assert "database discovery is incomplete" in status.detail
    assert "refusing unsafe access" in status.detail


def test_unreadable_database_sidecar_target_fails_closed(codex_home, tmp_path, monkeypatch):
    db = _make_db(codex_home)
    shm = Path(f"{db}-shm")
    shm.write_bytes(b"\0" * 256)
    proc_root = _complete_empty_proc(tmp_path)
    original_stat = Path.stat

    def guarded_stat(path, *args, **kwargs):
        if path == shm:
            raise PermissionError("fixture denies sidecar inspection")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", guarded_stat)

    status = sl.probe(codex_home, proc_root=proc_root)

    assert status.exists is True
    assert status.locked is True
    assert status.diagnostics_complete is False
    assert "database target inspection is incomplete" in status.detail
    assert "refusing unsafe access" in status.detail


def test_same_owner_unreadable_fd_scan_fails_closed(codex_home, tmp_path):
    db = _make_db(codex_home)
    proc_root = _complete_empty_proc(tmp_path)
    _write_synthetic_process(
        proc_root,
        4243,
        uid=db.stat().st_uid,
        with_fd_dir=False,
    )

    status = sl.probe(codex_home, proc_root=proc_root)

    assert status.locked is True
    assert status.diagnostics_complete is False
    assert status.holder_pids == []
    assert "refusing unsafe access" in status.detail


def test_foreign_owner_unreadable_fd_scan_does_not_fail_closed(codex_home, tmp_path):
    db = _make_db(codex_home)
    proc_root = _complete_empty_proc(tmp_path)
    _write_synthetic_process(
        proc_root,
        4244,
        uid=db.stat().st_uid + 1,
        with_fd_dir=False,
    )

    status = sl.probe(codex_home, proc_root=proc_root)

    assert status.locked is False
    assert status.diagnostics_complete is True


def test_same_owner_known_non_codex_unreadable_fd_is_ignored(codex_home, tmp_path):
    db = _make_db(codex_home)
    proc_root = _complete_empty_proc(tmp_path)
    process = _write_synthetic_process(
        proc_root,
        4245,
        uid=db.stat().st_uid,
        name="(sd-pam)",
    )
    fd_dir = process / "fd"
    fd_dir.chmod(0)
    try:
        status = sl.probe(codex_home, proc_root=proc_root)
    finally:
        fd_dir.chmod(0o700)

    assert status.locked is False
    assert status.diagnostics_complete is True


def test_same_owner_benign_process_nonpermission_fd_error_fails_closed(
    codex_home, tmp_path, monkeypatch
):
    db = _make_db(codex_home)
    proc_root = _complete_empty_proc(tmp_path)
    process = _write_synthetic_process(
        proc_root,
        4248,
        uid=db.stat().st_uid,
        name="(sd-pam)",
    )
    original = sl._bounded_directory_entries

    def fail_fd(path, **kwargs):
        if path == process / "fd":
            return [], OSError(5, "fixture I/O failure")
        return original(path, **kwargs)

    monkeypatch.setattr(sl, "_bounded_directory_entries", fail_fd)
    status = sl.probe(codex_home, proc_root=proc_root)

    assert status.locked is True
    assert status.diagnostics_complete is False
    assert "proc_inspection_incomplete" in status.incomplete_reasons


@pytest.mark.parametrize("name", ["node", "python", "sh", "env", "npx", "arbitrary-helper"])
def test_same_owner_unreadable_wrapper_process_fails_closed(codex_home, tmp_path, name):
    db = _make_db(codex_home)
    proc_root = _complete_empty_proc(tmp_path)
    _write_synthetic_process(
        proc_root,
        4247,
        uid=db.stat().st_uid,
        name=name,
        with_fd_dir=False,
    )

    status = sl.probe(codex_home, proc_root=proc_root)

    assert status.locked is True
    assert status.diagnostics_complete is False


@pytest.mark.parametrize("state", ["Z", "X", "x"])
def test_known_dead_codex_process_does_not_make_scan_incomplete(codex_home, tmp_path, state):
    db = _make_db(codex_home)
    proc_root = _complete_empty_proc(tmp_path)
    _write_synthetic_process(
        proc_root,
        4246,
        uid=db.stat().st_uid,
        state=state,
        with_fd_dir=False,
    )

    status = sl.probe(codex_home, proc_root=proc_root)

    assert status.locked is False
    assert status.diagnostics_complete is True


def test_probe_defaults_to_codex_home_env(codex_home, monkeypatch, tmp_path):
    monkeypatch.setenv("CODEX_HOME", str(codex_home))
    _make_db(codex_home)

    status = sl.probe(proc_root=_complete_empty_proc(tmp_path))

    assert status.home == codex_home
    assert status.locked is False


@pytest.mark.skipif(not Path("/proc/locks").exists(), reason="requires Linux /proc")
def test_dynamic_state_database_reports_running_holder_pid(codex_home):
    db = _make_db(codex_home, "state_99.sqlite")

    with _sqlite_holder(db) as holder:
        status = sl.probe(codex_home)

        assert status.locked is True
        assert status.db_path == db
        assert status.holder_pids == [holder.pid]
        assert status.running_pids == [holder.pid]
        assert status.stopped_pids == []
        assert f"PID {holder.pid} (running)" in status.detail
        assert "holder PID unavailable" not in status.detail


@pytest.mark.skipif(not Path("/proc/locks").exists(), reason="requires Linux /proc")
def test_dynamic_log_database_reports_running_holder_pid(codex_home):
    _make_db(codex_home, "state_12.sqlite")
    db = _make_db(codex_home, "logs_17.sqlite")

    with _sqlite_holder(db) as holder:
        status = sl.probe(codex_home)

        assert status.locked is True
        assert status.db_path == db
        assert status.holder_pids == [holder.pid]
        assert f"PID {holder.pid} (running)" in status.detail
        assert str(db) in sl.remediation_hint(status)


@pytest.mark.skipif(not Path("/proc/locks").exists(), reason="requires Linux /proc")
def test_stopped_test_holder_is_named_and_classified(codex_home):
    db = _make_db(codex_home)

    with _sqlite_holder(db) as holder:
        os.kill(holder.pid, signal.SIGSTOP)
        _wait_for_state(holder.pid, stopped=True)
        status = sl.probe(codex_home)

        assert status.locked is True
        assert status.holder_pids == [holder.pid]
        assert status.running_pids == []
        assert status.stopped_pids == [holder.pid]
        assert f"PID {holder.pid} (stopped)" in status.detail
        hint = sl.remediation_hint(status)
        assert "resumed and exited normally" in hint
        assert "kill" not in hint.lower()

        os.kill(holder.pid, signal.SIGCONT)
        _wait_for_state(holder.pid, stopped=False)


@pytest.mark.skipif(not Path("/proc/locks").exists(), reason="requires Linux /proc")
def test_wal_shm_attachment_is_detected(codex_home):
    db = _make_db(codex_home)

    with _sqlite_holder(db, mode="wal") as holder:
        assert Path(f"{db}-shm").exists()
        status = sl.probe(codex_home)

        assert status.locked is True
        assert status.holder_pids == [holder.pid]
        assert f"PID {holder.pid} (running)" in status.detail


def test_sqlite_main_and_shm_lock_byte_ranges(codex_home):
    db = _make_db(codex_home)
    shm = Path(f"{db}-shm")
    shm.touch()
    targets = {target.role: target for target in sl._target_files(db)[0]}

    def lock_for(role: str, start: int, end: int):
        target = targets[role]
        major, minor = target.proc_device
        return sl._ProcLock(123, major, minor, target.inode, start, end)

    assert sl._lock_targets_file(
        lock_for("main", sl._SQLITE_PENDING_BYTE, sl._SQLITE_PENDING_BYTE),
        targets["main"],
    )
    assert sl._lock_targets_file(lock_for("shm", 120, 127), targets["shm"])
    assert sl._lock_targets_file(lock_for("shm", 128, 128), targets["shm"])
    assert not sl._lock_targets_file(lock_for("main", 0, 100), targets["main"])
    assert not sl._lock_targets_file(lock_for("shm", 0, 100), targets["shm"])


@pytest.mark.parametrize(
    ("state", "expected_running", "expected_stopped"),
    [("S", [4242], []), ("T", [], [4242])],
)
def test_synthetic_proc_shm_only_lock_names_holder_state(
    codex_home, tmp_path, state, expected_running, expected_stopped
):
    """Prove SHM-byte attribution without a main-database descriptor."""
    db = _make_db(codex_home, "logs_88.sqlite")
    shm = Path(f"{db}-shm")
    shm.write_bytes(b"\0" * 256)
    target = next(item for item in sl._target_files(db)[0] if item.role == "shm")
    major, minor = target.proc_device

    proc_root = tmp_path / "proc"
    process = _write_synthetic_process(
        proc_root,
        4242,
        uid=db.stat().st_uid,
        state=state,
    )
    (process / "fd" / "7").symlink_to(shm)
    lock_row = f"9: POSIX ADVISORY WRITE 4242 {major:02x}:{minor:02x}:{target.inode} 120 127"
    (process / "fdinfo" / "7").write_text(f"lock:\t{lock_row}\n")
    (proc_root / "locks").write_text(lock_row + "\n")

    status = sl.probe(codex_home, proc_root=proc_root)

    assert status.locked is True
    assert status.db_path == db
    assert status.holder_pids == [4242]
    assert status.diagnostics_complete is True
    assert status.running_pids == expected_running
    assert status.stopped_pids == expected_stopped
    assert f"PID 4242 ({'stopped' if state == 'T' else 'running'})" in status.detail


def test_proc_lock_parser_accepts_global_and_fdinfo_rows():
    global_row = "7: POSIX ADVISORY READ 4321 08:02:99 120 127"
    fdinfo_row = "lock:\t7: POSIX ADVISORY WRITE 4321 08:02:99 128 128"

    global_lock = sl._parse_proc_lock_line(global_row)
    fdinfo_lock = sl._parse_proc_lock_line(fdinfo_row)

    assert global_lock == sl._ProcLock(4321, 8, 2, 99, 120, 127)
    assert fdinfo_lock == sl._ProcLock(4321, 8, 2, 99, 128, 128)


def test_codex_pid_file_is_not_trusted_as_lock_attribution(codex_home, tmp_path):
    _make_db(codex_home)
    (codex_home / "codex.pid").write_text(f"{os.getpid()}\n")

    status = sl.probe(codex_home, proc_root=_complete_empty_proc(tmp_path))

    assert status.locked is False
    assert status.holder_pids == []


def test_malformed_state_file_without_attachment_is_not_locked(codex_home, tmp_path):
    (codex_home / "state_42.sqlite").write_text("not a SQLite database")

    status = sl.probe(codex_home, proc_root=_complete_empty_proc(tmp_path))

    assert status.exists is True
    assert status.locked is False


@pytest.mark.parametrize("platform_id", ["darwin", "win32"])
def test_portable_shared_existing_database_is_functional_when_no_codex_process(
    codex_home, monkeypatch, platform_id
):
    _make_db(codex_home)
    monkeypatch.setattr(sl, "_portable_codex_processes", lambda *_args: ([], True))

    status = sl.probe(codex_home, platform_id=platform_id)

    assert status.exists is True
    assert status.locked is False
    assert status.diagnostics_complete is True


@pytest.mark.parametrize("platform_id", ["darwin", "win32"])
@pytest.mark.parametrize("state", ["S", "T"])
def test_portable_shared_conservatively_names_codex_process(
    codex_home, monkeypatch, platform_id, state
):
    _make_db(codex_home)
    candidate = sl._ProcessInfo(pid=8123, uid=-1, state=state, start_time=99)
    monkeypatch.setattr(
        sl,
        "_portable_codex_processes",
        lambda *_args: ([candidate], True),
    )

    status = sl.probe(codex_home, platform_id=platform_id)

    assert status.locked is True
    assert status.holder_pids == [8123]
    expected = "stopped" if state == "T" else "running"
    assert f"PID 8123 ({expected})" in status.detail


@pytest.mark.parametrize("platform_id", ["darwin", "win32"])
def test_portable_shared_inspection_failure_is_explicit_fail_closed(
    codex_home, monkeypatch, platform_id
):
    _make_db(codex_home)

    def incomplete(_platform, budget):
        budget.add("portable_inspection_incomplete")
        return [], False

    monkeypatch.setattr(sl, "_portable_codex_processes", incomplete)
    status = sl.probe(codex_home, platform_id=platform_id)

    assert status.locked is True
    assert status.diagnostics_complete is False
    assert status.incomplete_reasons == ["portable_inspection_incomplete"]


@pytest.mark.parametrize("platform_id", ["darwin", "win32"])
def test_portable_shared_unknown_sentinel_identity_is_explicit_fail_closed(
    codex_home, monkeypatch, platform_id
):
    _make_db(codex_home)
    sentinel = sh.PidSentinel(
        schema="tokenpak.codex.pid.v1",
        pid=8124,
        start_time_ticks=99,
        session_id="portable-unknown",
        mode="shared",
        home=str(codex_home.resolve()),
    )
    path = codex_home / "codex.pid"
    path.write_bytes(sh._sentinel_payload(sentinel))
    path.chmod(0o600)
    monkeypatch.setattr(
        sh,
        "_sentinel_liveness",
        lambda *_a, **_k: sh._PROCESS_UNKNOWN,
    )
    monkeypatch.setattr(sl, "_portable_codex_processes", lambda *_args: ([], True))

    status = sl.probe(codex_home, platform_id=platform_id)

    assert status.locked is True
    assert status.diagnostics_complete is False
    assert status.incomplete_reasons == ["portable_inspection_incomplete"]


def test_multiple_databases_scan_processes_once(codex_home, tmp_path, monkeypatch):
    _make_db(codex_home, "state_5.sqlite")
    _make_db(codex_home, "logs_2.sqlite")
    proc_root = _complete_empty_proc(tmp_path)
    calls = 0
    original = sl._scan_fd_holders

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(sl, "_scan_fd_holders", counted)
    status = sl.probe(codex_home, proc_root=proc_root)

    assert status.locked is False
    assert calls == 1


def test_target_inode_replacement_is_explicit_fail_closed(codex_home, tmp_path, monkeypatch):
    db = _make_db(codex_home)
    replacement = codex_home / "replacement.tmp"
    replacement.write_bytes(b"replacement")
    proc_root = _complete_empty_proc(tmp_path)
    original = sl._scan_fd_holders

    def replace_target(*args, **kwargs):
        result = original(*args, **kwargs)
        os.replace(replacement, db)
        return result

    monkeypatch.setattr(sl, "_scan_fd_holders", replace_target)
    status = sl.probe(codex_home, proc_root=proc_root)

    assert status.locked is True
    assert status.diagnostics_complete is False
    assert "target_inode_replaced" in status.incomplete_reasons


def test_new_database_during_probe_is_explicit_fail_closed(codex_home, tmp_path, monkeypatch):
    _make_db(codex_home)
    proc_root = _complete_empty_proc(tmp_path)
    original = sl._scan_fd_holders

    def add_database(*args, **kwargs):
        result = original(*args, **kwargs)
        (codex_home / "logs_99.sqlite").write_bytes(b"new")
        return result

    monkeypatch.setattr(sl, "_scan_fd_holders", add_database)
    status = sl.probe(codex_home, proc_root=proc_root)

    assert status.locked is True
    assert "database_set_changed" in status.incomplete_reasons


def test_pid_reuse_during_holder_revalidation_is_explicit(monkeypatch, tmp_path):
    observed = sl._ProcessInfo(pid=9000, uid=os.getuid(), state="S", start_time=10)
    scan = sl._FdScan(attachments={9000: (observed, {(1, 2)})})
    budget = sl._ProbeBudget(deadline=10, clock=lambda: 0)
    monkeypatch.setattr(
        sl,
        "_read_process_info",
        lambda *_args, **_kwargs: sl._ProcessInfo(9000, os.getuid(), "S", 11),
    )

    holders = sl._revalidate_attachments(
        scan,
        proc_root=tmp_path,
        budget=budget,
    )

    assert holders == {}
    assert budget.reasons == ["pid_reuse"]


def test_probe_deadline_and_database_limit_fail_closed(codex_home, tmp_path, monkeypatch):
    _make_db(codex_home, "state_1.sqlite")
    status = sl.probe(
        codex_home,
        proc_root=_complete_empty_proc(tmp_path),
        deadline=0,
        clock=lambda: 0,
    )
    assert status.locked is True
    assert status.incomplete_reasons == ["probe_timeout"]

    monkeypatch.setattr(sl, "_MAX_DATABASES", 1)
    _make_db(codex_home, "logs_1.sqlite")
    second = tmp_path / "second"
    second.mkdir()
    status = sl.probe(codex_home, proc_root=_complete_empty_proc(second))
    assert status.locked is True
    assert "database_limit" in status.incomplete_reasons


def test_process_and_fd_limits_are_explicit_fail_closed(codex_home, tmp_path, monkeypatch):
    db = _make_db(codex_home)
    proc_root = _complete_empty_proc(tmp_path)
    process = _write_synthetic_process(
        proc_root,
        9300,
        uid=db.stat().st_uid,
        name="codex",
    )
    first = process / "fd" / "1"
    second = process / "fd" / "2"
    first.symlink_to(db)
    second.symlink_to(db)

    monkeypatch.setattr(sl, "_MAX_FDS_PER_PROCESS", 1)
    status = sl.probe(codex_home, proc_root=proc_root)
    assert status.locked is True
    assert "fd_limit" in status.incomplete_reasons

    monkeypatch.setattr(sl, "_MAX_FDS_PER_PROCESS", 10)
    monkeypatch.setattr(sl, "_MAX_PROCESSES", 1)
    status = sl.probe(codex_home, proc_root=proc_root)
    assert status.locked is True
    assert "process_limit" in status.incomplete_reasons


def test_deadline_expiring_after_snapshot_fails_closed_during_classification(
    codex_home, tmp_path, monkeypatch
):
    db = _make_db(codex_home)
    proc_root = _complete_empty_proc(tmp_path)
    process = _write_synthetic_process(
        proc_root,
        9400,
        uid=db.stat().st_uid,
    )
    (process / "fd" / "7").symlink_to(db)
    now = [0.0]
    original = sl._revalidate_database_snapshot

    def expire_after_snapshot(*args, **kwargs):
        original(*args, **kwargs)
        now[0] = 2.0

    monkeypatch.setattr(sl, "_revalidate_database_snapshot", expire_after_snapshot)
    status = sl.probe(
        codex_home,
        proc_root=proc_root,
        deadline=1.0,
        clock=lambda: now[0],
    )

    assert status.locked is True
    assert status.diagnostics_complete is False
    assert "probe_timeout" in status.incomplete_reasons


def test_pid_reuse_during_unreadable_fd_scan_is_explicit_fail_closed(
    codex_home, tmp_path, monkeypatch
):
    db = _make_db(codex_home)
    proc_root = _complete_empty_proc(tmp_path)
    process = _write_synthetic_process(
        proc_root,
        9401,
        uid=db.stat().st_uid,
    )
    original_entries = sl._bounded_directory_entries
    original_info = sl._read_process_info
    reads = 0

    def unreadable_fd(path, **kwargs):
        if path == process / "fd":
            return [], PermissionError(errno.EACCES, "fixture permission denied")
        return original_entries(path, **kwargs)

    def reused_pid(pid, root=sl._PROC_ROOT):
        nonlocal reads
        info = original_info(pid, root)
        if pid == 9401 and info is not None:
            reads += 1
            if reads > 1:
                return sl._ProcessInfo(info.pid, info.uid, info.state, info.start_time + 1)
        return info

    monkeypatch.setattr(sl, "_bounded_directory_entries", unreadable_fd)
    monkeypatch.setattr(sl, "_read_process_info", reused_pid)
    status = sl.probe(codex_home, proc_root=proc_root)

    assert status.locked is True
    assert status.diagnostics_complete is False
    assert "pid_reuse" in status.incomplete_reasons


def test_proc_lock_pid_is_attributed_without_fd_row(codex_home, tmp_path):
    db = _make_db(codex_home)
    proc_root = _complete_empty_proc(tmp_path)
    _write_synthetic_process(proc_root, 9402, uid=db.stat().st_uid)
    target = next(item for item in sl._target_files(db)[0] if item.role == "main")
    major, minor = target.proc_device
    (proc_root / "locks").write_text(
        f"7: POSIX ADVISORY WRITE 9402 {major:02x}:{minor:02x}:"
        f"{target.inode} {sl._SQLITE_PENDING_BYTE} {sl._SQLITE_PENDING_BYTE}\n"
    )

    status = sl.probe(codex_home, proc_root=proc_root)

    assert status.locked is True
    assert status.diagnostics_complete is True
    assert status.holder_pids == [9402]
    assert "PID 9402 (running)" in status.detail


def test_proc_lock_pid_reuse_is_explicit_fail_closed(codex_home, tmp_path, monkeypatch):
    db = _make_db(codex_home)
    proc_root = _complete_empty_proc(tmp_path)
    _write_synthetic_process(proc_root, 9403, uid=db.stat().st_uid)
    target = next(item for item in sl._target_files(db)[0] if item.role == "main")
    major, minor = target.proc_device
    (proc_root / "locks").write_text(
        f"7: POSIX ADVISORY WRITE 9403 {major:02x}:{minor:02x}:"
        f"{target.inode} {sl._SQLITE_PENDING_BYTE} {sl._SQLITE_PENDING_BYTE}\n"
    )
    original = sl._read_process_info
    reads = 0

    def reused(pid, root=sl._PROC_ROOT):
        nonlocal reads
        info = original(pid, root)
        if pid == 9403 and info is not None:
            reads += 1
            if reads > 2:
                return sl._ProcessInfo(info.pid, info.uid, info.state, info.start_time + 1)
        return info

    monkeypatch.setattr(sl, "_read_process_info", reused)
    status = sl.probe(codex_home, proc_root=proc_root)

    assert status.locked is True
    assert status.diagnostics_complete is False
    assert status.holder_pids == []
    assert "pid_reuse" in status.incomplete_reasons


def test_proc_lock_pid_reuse_during_fd_scan_is_explicit_fail_closed(
    codex_home, tmp_path, monkeypatch
):
    db = _make_db(codex_home)
    proc_root = _complete_empty_proc(tmp_path)
    _write_synthetic_process(proc_root, 9500, uid=db.stat().st_uid)
    target = next(item for item in sl._target_files(db)[0] if item.role == "main")
    major, minor = target.proc_device
    (proc_root / "locks").write_text(
        f"7: POSIX ADVISORY WRITE 9500 {major:02x}:{minor:02x}:"
        f"{target.inode} {sl._SQLITE_PENDING_BYTE} {sl._SQLITE_PENDING_BYTE}\n"
    )
    original_read = sl._read_process_info
    original_scan = sl._scan_fd_holders
    scan_finished = False

    def reused_after_scan(pid, root=sl._PROC_ROOT):
        info = original_read(pid, root)
        if pid == 9500 and info is not None and scan_finished:
            return sl._ProcessInfo(info.pid, info.uid, info.state, info.start_time + 1)
        return info

    def finish_scan(*args, **kwargs):
        nonlocal scan_finished
        result = original_scan(*args, **kwargs)
        scan_finished = True
        return result

    monkeypatch.setattr(sl, "_read_process_info", reused_after_scan)
    monkeypatch.setattr(sl, "_scan_fd_holders", finish_scan)
    status = sl.probe(codex_home, proc_root=proc_root)

    assert status.locked is True
    assert status.diagnostics_complete is False
    assert status.holder_pids == []
    assert "pid_reuse" in status.incomplete_reasons


def test_toolhelp_eof_is_distinct_from_enumeration_failure():
    assert sl._toolhelp_has_entry(True, 0) is True
    assert sl._toolhelp_has_entry(False, sl._ERROR_NO_MORE_FILES) is False
    with pytest.raises(OSError, match="Toolhelp process enumeration failed"):
        sl._toolhelp_has_entry(False, errno.EIO)


@pytest.mark.parametrize("platform_id", ["darwin", "win32"])
@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ("inode", "target_inode_replaced"),
        ("database", "database_set_changed"),
        ("shm", "target_inode_replaced"),
    ],
)
def test_portable_probe_revalidates_database_targets(
    codex_home, monkeypatch, platform_id, change, reason
):
    db = _make_db(codex_home)

    def mutate_during_snapshot(*_args):
        if change == "inode":
            replacement = codex_home / "replacement.tmp"
            replacement.write_bytes(b"replacement")
            os.replace(replacement, db)
        elif change == "database":
            (codex_home / "logs_99.sqlite").write_bytes(b"new")
        else:
            Path(f"{db}-shm").write_bytes(b"new")
        return [], True

    monkeypatch.setattr(sl, "_portable_codex_processes", mutate_during_snapshot)
    status = sl.probe(codex_home, platform_id=platform_id)

    assert status.locked is True
    assert status.diagnostics_complete is False
    assert reason in status.incomplete_reasons
