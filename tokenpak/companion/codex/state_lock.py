# SPDX-License-Identifier: Apache-2.0
"""Read-only Codex SQLite holder diagnostics for ``CODEX_HOME``.

The diagnostic path must not join, copy, checkpoint, or otherwise touch a
Codex runtime database.  On Linux, the kernel already exposes the evidence we
need: database file identities, open file descriptors, and advisory byte-range
locks.  This module correlates those sources without importing ``sqlite3`` or
opening a database file.

SQLite's rollback-mode locks live in the main database's pending/reserved/
shared byte range.  WAL coordination locks live in bytes 120 through 127 of
the ``-shm`` file, with byte 128 used for dead-man-switch coordination.  An
open descriptor to a Codex database or sidecar is also treated as an unsafe
shared-home attachment, even if the process happens to be between lock
operations when sampled.
"""

from __future__ import annotations

import errno
import hashlib
import os
import stat as stat_module
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

# Companion-internal launcher helper (probe/remediation_hint are called by
# launcher.py, not by end users): export nothing as released public API.
__all__: list[str] = []

# Retained for callers and older installations.  Discovery itself is dynamic.
STATE_DB_NAME = "state_5.sqlite"
_LOG_DB_NAME = "logs_2.sqlite"

_PROC_ROOT = Path("/proc")

# SQLite locking bytes from sqlite3.h/os_unix.c.
_SQLITE_PENDING_BYTE = 0x40000000
_SQLITE_SHARED_FIRST = _SQLITE_PENDING_BYTE + 2
_SQLITE_SHARED_SIZE = 510
_SQLITE_MAIN_LOCK_LAST = _SQLITE_SHARED_FIRST + _SQLITE_SHARED_SIZE - 1
_SQLITE_SHM_LOCK_FIRST = 120
_SQLITE_SHM_LOCK_LAST = 127
_SQLITE_SHM_DMS = 128

_DEAD_STATES = frozenset({"X", "x", "Z"})
_STOPPED_STATES = frozenset({"T", "t"})
_BENIGN_UNREADABLE_PROCESSES = frozenset({"sd-pam"})

# Every probe has structural and wall-clock limits.  The values are generous
# for a developer workstation but finite so a hostile or damaged procfs/home
# cannot turn the launcher's 30-second wait into unbounded work.
_MAX_DATABASES = 128
_MAX_HOME_ENTRIES = 4096
_MAX_PROCESSES = 65536
_MAX_FDS_PER_PROCESS = 65536
_MAX_TOTAL_FDS = 262144
_MAX_PROC_LOCK_ROWS = 262144
_DEFAULT_PROBE_TIMEOUT_S = 5.0
_ERROR_NO_MORE_FILES = 18

_REASON_LABELS = {
    "database_discovery_incomplete": "Codex database discovery",
    "database_limit": "Codex database discovery limit",
    "database_target_incomplete": "Codex database target inspection",
    "database_set_changed": "Codex database set changed during inspection",
    "target_inode_replaced": "Codex database target inode changed during inspection",
    "proc_inspection_incomplete": "/proc holder inspection",
    "process_limit": "/proc process inspection limit",
    "fd_limit": "/proc file-descriptor inspection limit",
    "pid_reuse": "PID reuse detected during holder inspection",
    "process_changed": "process changed during holder inspection",
    "proc_lock_limit": "/proc lock inspection limit",
    "probe_timeout": "holder inspection wall-time limit",
    "portable_inspection_incomplete": "portable SQLite lock inspection",
}


@dataclass
class LockStatus:
    """Result of a read-only state-lock preflight on one ``CODEX_HOME``."""

    home: Path
    db_path: Path
    exists: bool
    locked: bool
    holder_pids: list[int] = field(default_factory=list)
    stopped_pids: list[int] = field(default_factory=list)
    detail: str = ""
    running_pids: list[int] = field(default_factory=list)
    diagnostics_complete: bool = True
    incomplete_reasons: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class _FileIdentity:
    path: Path
    device: int
    inode: int
    owner_uid: int
    role: str
    db_path: Path

    @property
    def proc_device(self) -> tuple[int, int]:
        return os.major(self.device), os.minor(self.device)


@dataclass(frozen=True)
class _ProcLock:
    pid: int
    device_major: int
    device_minor: int
    inode: int
    start: int
    end: int | None


@dataclass(frozen=True)
class _ProcessInfo:
    pid: int
    uid: int
    state: str
    start_time: int

    @property
    def stopped(self) -> bool:
        return self.state in _STOPPED_STATES


@dataclass
class _ProbeBudget:
    deadline: float
    clock: Callable[[], float]
    reasons: list[str] = field(default_factory=list)

    def add(self, reason: str) -> None:
        if reason not in self.reasons:
            self.reasons.append(reason)

    def expired(self) -> bool:
        if self.clock() < self.deadline:
            return False
        self.add("probe_timeout")
        return True


@dataclass
class _FdScan:
    attachments: dict[int, tuple[_ProcessInfo, set[tuple[int, int]]]] = field(default_factory=dict)
    locks: list[_ProcLock] = field(default_factory=list)
    complete: bool = True


def _bounded_directory_entries(
    path: Path,
    *,
    limit: int,
    budget: _ProbeBudget | None,
    limit_reason: str,
) -> tuple[list[Path], OSError | None]:
    """Materialize at most ``limit`` entries and surface iteration errors."""
    entries: list[Path] = []
    try:
        with os.scandir(path) as iterator:
            for entry in iterator:
                if budget is not None and budget.expired():
                    return entries, TimeoutError("probe deadline reached")
                if len(entries) >= limit:
                    if budget is not None:
                        budget.add(limit_reason)
                    return entries, OSError(f"{limit_reason} exceeded")
                entries.append(path / entry.name)
    except OSError as exc:
        return entries, exc
    return entries, None


def _db_path(home: Path) -> Path:
    """Compatibility fallback used when a home has no database yet."""
    return home / STATE_DB_NAME


def _db_paths(home: Path, *, budget: _ProbeBudget | None = None) -> tuple[list[Path], bool]:
    """Discover Codex databases without assuming a schema generation."""
    try:
        entries: list[Path] = []
        for entry_count, path in enumerate(home.iterdir(), start=1):
            if budget is not None and budget.expired():
                return [], False
            if entry_count > _MAX_HOME_ENTRIES:
                if budget is not None:
                    budget.add("database_limit")
                return [], False
            entries.append(path)
    except FileNotFoundError:
        return [], True
    except OSError:
        if budget is not None:
            budget.add("database_discovery_incomplete")
        return [], False
    candidates = [
        path
        for path in entries
        if path.name.endswith(".sqlite") and path.name.startswith(("state_", "logs_"))
    ]
    result: list[Path] = []
    complete = True
    for path in sorted(
        candidates, key=lambda item: (not item.name.startswith("state_"), item.name)
    ):
        if budget is not None and budget.expired():
            return result, False
        if len(result) >= _MAX_DATABASES:
            if budget is not None:
                budget.add("database_limit")
            return result, False
        try:
            mode = path.stat().st_mode
        except FileNotFoundError:
            continue
        except OSError:
            complete = False
            if budget is not None:
                budget.add("database_discovery_incomplete")
            continue
        if stat_module.S_ISREG(mode):
            result.append(path)
    return result, complete


def _target_files(
    db_path: Path, *, budget: _ProbeBudget | None = None
) -> tuple[list[_FileIdentity], bool]:
    targets: list[_FileIdentity] = []
    complete = True
    for path, role in (
        (db_path, "main"),
        (Path(f"{db_path}-wal"), "wal"),
        (Path(f"{db_path}-shm"), "shm"),
    ):
        if budget is not None and budget.expired():
            return targets, False
        try:
            st = path.stat()
        except FileNotFoundError:
            continue
        except OSError:
            complete = False
            if budget is not None:
                budget.add("database_target_incomplete")
            continue
        if stat_module.S_ISREG(st.st_mode):
            targets.append(
                _FileIdentity(
                    path=path,
                    device=st.st_dev,
                    inode=st.st_ino,
                    owner_uid=st.st_uid,
                    role=role,
                    db_path=db_path,
                )
            )
    return targets, complete


def _parse_proc_lock_line(line: str) -> _ProcLock | None:
    """Parse one ``/proc/locks`` or ``fdinfo`` lock row."""
    line = line.strip()
    if line.startswith("lock:"):
        line = line[5:].strip()
    fields = line.split()
    if len(fields) < 8:
        return None
    if len(fields) > 1 and fields[1] == "->":
        fields.pop(1)
    if len(fields) < 8:
        return None
    try:
        pid = int(fields[4])
        major_raw, minor_raw, inode_raw = fields[5].split(":", 2)
        device_major = int(major_raw, 16)
        device_minor = int(minor_raw, 16)
        inode = int(inode_raw)
        start = int(fields[6])
        end = None if fields[7] == "EOF" else int(fields[7])
    except (TypeError, ValueError):
        return None
    return _ProcLock(
        pid=pid,
        device_major=device_major,
        device_minor=device_minor,
        inode=inode,
        start=start,
        end=end,
    )


def _read_proc_locks(
    proc_root: Path = _PROC_ROOT, *, budget: _ProbeBudget | None = None
) -> tuple[list[_ProcLock], bool]:
    try:
        stream = (proc_root / "locks").open(errors="replace")
    except OSError:
        if budget is not None:
            budget.add("proc_inspection_incomplete")
        return [], False
    locks: list[_ProcLock] = []
    complete = True
    try:
        for row_count, row in enumerate(stream, start=1):
            if budget is not None and budget.expired():
                return locks, False
            if row_count > _MAX_PROC_LOCK_ROWS:
                complete = False
                if budget is not None:
                    budget.add("proc_lock_limit")
                break
            lock = _parse_proc_lock_line(row)
            if lock is None:
                if row.strip():
                    complete = False
                    if budget is not None:
                        budget.add("proc_inspection_incomplete")
                continue
            locks.append(lock)
    except OSError:
        complete = False
        if budget is not None:
            budget.add("proc_inspection_incomplete")
    finally:
        stream.close()
    return locks, complete


def _read_process_info(pid: int, proc_root: Path = _PROC_ROOT) -> _ProcessInfo | None:
    """Return a TGID-normalized process identity, resistant to PID reuse."""
    if pid <= 0:
        return None
    process_dir = proc_root / str(pid)
    try:
        status = (process_dir / "status").read_text(errors="replace")
        raw_stat = (process_dir / "stat").read_text(errors="replace")
    except OSError:
        return None

    tgid: int | None = None
    uid: int | None = None
    for line in status.splitlines():
        if line.startswith("Tgid:"):
            try:
                tgid = int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
        elif line.startswith("Uid:"):
            try:
                uid = int(line.split(":", 1)[1].split()[0])
            except (IndexError, ValueError):
                return None
    if tgid is None or uid is None:
        return None
    if tgid != pid:
        return _read_process_info(tgid, proc_root)

    # /proc/<pid>/stat is ``pid (comm) state ...``.  comm may contain spaces
    # and parentheses, so only the final right parenthesis is structural.
    rparen = raw_stat.rfind(")")
    if rparen < 0:
        return None
    tail = raw_stat[rparen + 1 :].split()
    if len(tail) <= 19:
        return None
    state = tail[0]
    try:
        start_time = int(tail[19])  # field 22 in proc_pid_stat(5)
    except ValueError:
        return None
    return _ProcessInfo(pid=tgid, uid=uid, state=state, start_time=start_time)


def _revalidate_process(
    info: _ProcessInfo, proc_root: Path = _PROC_ROOT
) -> tuple[str, _ProcessInfo | None]:
    """Distinguish a clean exit from PID reuse or unreadable identity."""
    current = _read_process_info(info.pid, proc_root)
    if current is not None:
        if current.state in _DEAD_STATES:
            return "dead", current
        if current.start_time != info.start_time:
            return "reused", current
        return "same", current
    try:
        (proc_root / str(info.pid)).stat()
    except FileNotFoundError:
        return "exited", None
    except OSError:
        return "unreadable", None
    return "unreadable", None


def _identity_key(device: int, inode: int) -> tuple[int, int]:
    return device, inode


def _normalize_process_name(value: str) -> str:
    """Normalize procfs ``comm`` and argv names for conservative matching.

    Linux may expose kernel-style names such as ``(sd-pam)`` while cmdline
    exposes ``sd-pam``.  Treating those spellings differently turned one
    unreadable benign desktop process into an incomplete machine-wide scan.
    """
    normalized = value.strip()
    while len(normalized) >= 2 and normalized.startswith("(") and normalized.endswith(")"):
        normalized = normalized[1:-1].strip()
    return Path(normalized).name.casefold()


def _codex_process_kind(pid: int, proc_root: Path = _PROC_ROOT) -> bool | None:
    """Classify Codex, explicitly benign, or unknown process wrappers."""
    process_dir = proc_root / str(pid)
    executable_names: list[str] = []
    all_names: list[str] = []
    try:
        comm = (process_dir / "comm").read_text(errors="replace").strip()
    except OSError:
        comm = ""
    if comm:
        executable_names.append(comm)
        all_names.append(comm)
    try:
        command = (process_dir / "cmdline").read_bytes()
    except OSError:
        command = b""
    argv = [token.decode(errors="replace") for token in command.split(b"\0") if token]
    if argv:
        executable_names.append(argv[0])
        all_names.extend(argv)
    if not all_names:
        return None
    normalized_all = {name for value in all_names if (name := _normalize_process_name(value))}
    if any("codex" in name for name in normalized_all):
        return True
    # Generic interpreters and arbitrary readable process names are not proof
    # that the process cannot own a Codex database.  Only a deliberately tiny
    # benign allowlist avoids the known non-dumpable desktop-session false
    # blocker; every other readable name remains unknown and fails closed.
    normalized_executables = {
        name for value in executable_names if (name := _normalize_process_name(value))
    }
    if normalized_executables and normalized_executables <= _BENIGN_UNREADABLE_PROCESSES:
        return False
    return None


def _inspection_failure_is_unsafe(
    info: _ProcessInfo,
    owner_uids: set[int],
    proc_root: Path,
    error: OSError,
) -> tuple[bool, str | None]:
    """Fail closed unless a permission error belongs to a known benign process."""
    if info.uid not in owner_uids:
        return False, None
    outcome, current = _revalidate_process(info, proc_root)
    if outcome in {"exited", "dead"}:
        return False, None
    if outcome == "reused":
        return True, "pid_reuse"
    if outcome == "unreadable" or current is None:
        return True, "process_changed"
    if current.uid not in owner_uids:
        return True, "pid_reuse"
    if error.errno not in {errno.EACCES, errno.EPERM}:
        return True, "proc_inspection_incomplete"
    unsafe = _codex_process_kind(current.pid, proc_root) is not False
    return unsafe, "proc_inspection_incomplete" if unsafe else None


def _scan_fd_holders(
    targets: list[_FileIdentity],
    proc_root: Path = _PROC_ROOT,
    *,
    budget: _ProbeBudget | None = None,
) -> _FdScan:
    """Scan every process at most once and correlate all target descriptors."""
    identities = {_identity_key(target.device, target.inode) for target in targets}
    owner_uids = {target.owner_uid for target in targets if target.role == "main"}
    scan = _FdScan()
    process_dirs, process_error = _bounded_directory_entries(
        proc_root,
        limit=_MAX_PROCESSES,
        budget=budget,
        limit_reason="process_limit",
    )
    if process_error is not None:
        if budget is not None:
            budget.add("proc_inspection_incomplete")
        scan.complete = False
        return scan

    process_count = 0
    total_fd_count = 0
    for process_dir in process_dirs:
        if not process_dir.name.isdigit():
            continue
        process_count += 1
        if budget is not None and budget.expired():
            scan.complete = False
            break
        info = _read_process_info(int(process_dir.name), proc_root)
        if info is None:
            # A same-owner process that remains present but cannot be parsed
            # cannot safely be excluded from the holder scan.  Use the proc
            # directory owner only as a fallback because status is unavailable.
            try:
                process_uid = process_dir.stat().st_uid
            except FileNotFoundError:
                continue
            except OSError:
                scan.complete = False
                if budget is not None:
                    budget.add("proc_inspection_incomplete")
                continue
            if process_uid in owner_uids and process_dir.exists():
                scan.complete = False
                if budget is not None:
                    budget.add("proc_inspection_incomplete")
            continue
        if info.state in _DEAD_STATES:
            continue
        if info.pid in scan.attachments:
            continue
        fd_dir = proc_root / str(info.pid) / "fd"
        descriptors, descriptor_error = _bounded_directory_entries(
            fd_dir,
            limit=_MAX_FDS_PER_PROCESS,
            budget=budget,
            limit_reason="fd_limit",
        )
        if descriptor_error is not None:
            exc = descriptor_error
            unsafe, reason = _inspection_failure_is_unsafe(info, owner_uids, proc_root, exc)
            if unsafe:
                scan.complete = False
                if budget is not None:
                    budget.add(reason or "proc_inspection_incomplete")
            continue
        total_fd_count += len(descriptors)
        if total_fd_count > _MAX_TOTAL_FDS:
            scan.complete = False
            if budget is not None:
                budget.add("fd_limit")
            break
        matching_fds: list[tuple[str, tuple[int, int]]] = []
        fd_count = 0
        for descriptor in descriptors:
            fd_count += 1
            if budget is not None and budget.expired():
                scan.complete = False
                break
            try:
                st = descriptor.stat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                unsafe, reason = _inspection_failure_is_unsafe(info, owner_uids, proc_root, exc)
                if unsafe:
                    scan.complete = False
                    if budget is not None:
                        budget.add(reason or "proc_inspection_incomplete")
                continue
            identity = _identity_key(st.st_dev, st.st_ino)
            if identity in identities:
                matching_fds.append((descriptor.name, identity))
        if budget is not None and budget.expired():
            break
        if not matching_fds:
            continue

        # Re-read stat after descriptor matching so a recycled PID cannot be
        # attributed using observations from two different processes.
        outcome, current = _revalidate_process(info, proc_root)
        if outcome in {"exited", "dead"}:
            continue
        if outcome == "reused":
            scan.complete = False
            if budget is not None:
                budget.add("pid_reuse")
            continue
        if outcome != "same" or current is None:
            scan.complete = False
            if budget is not None:
                budget.add("process_changed")
            continue
        attached = {identity for _, identity in matching_fds}
        scan.attachments[current.pid] = (current, attached)

        for fd_name, _identity in matching_fds:
            if budget is not None and budget.expired():
                scan.complete = False
                break
            try:
                rows = (
                    (proc_root / str(current.pid) / "fdinfo" / fd_name)
                    .read_text(errors="replace")
                    .splitlines()
                )
            except FileNotFoundError:
                continue
            except OSError as exc:
                unsafe, reason = _inspection_failure_is_unsafe(current, owner_uids, proc_root, exc)
                if unsafe:
                    scan.complete = False
                    if budget is not None:
                        budget.add(reason or "proc_inspection_incomplete")
                continue
            for row in rows:
                if not row.lstrip().startswith("lock:"):
                    continue
                lock = _parse_proc_lock_line(row)
                if lock is not None:
                    scan.locks.append(lock)

    return scan


def _ranges_overlap(
    first_start: int, first_end: int | None, second_start: int, second_end: int
) -> bool:
    effective_end = (1 << 63) - 1 if first_end is None else first_end
    return first_start <= second_end and effective_end >= second_start


def _lock_targets_file(lock: _ProcLock, target: _FileIdentity) -> bool:
    major, minor = target.proc_device
    if (lock.device_major, lock.device_minor, lock.inode) != (major, minor, target.inode):
        return False
    if target.role == "main":
        return _ranges_overlap(
            lock.start,
            lock.end,
            _SQLITE_PENDING_BYTE,
            _SQLITE_MAIN_LOCK_LAST,
        )
    if target.role == "shm":
        return _ranges_overlap(
            lock.start,
            lock.end,
            _SQLITE_SHM_LOCK_FIRST,
            _SQLITE_SHM_DMS,
        )
    return False


def _target_snapshot_key(target: _FileIdentity) -> tuple[str, int, int, int, str]:
    return (
        target.role,
        target.device,
        target.inode,
        target.owner_uid,
        str(target.path),
    )


def _targets_unchanged(
    db_path: Path,
    before: list[_FileIdentity],
    *,
    budget: _ProbeBudget,
) -> bool:
    """Re-stat main/WAL/SHM targets and reject replacement or appearance."""
    after, complete = _target_files(db_path, budget=budget)
    if not complete:
        budget.add("database_target_incomplete")
        return False
    if {_target_snapshot_key(item) for item in before} != {
        _target_snapshot_key(item) for item in after
    }:
        budget.add("target_inode_replaced")
        return False
    return True


def _revalidate_database_snapshot(
    home: Path,
    dbs: list[Path],
    targets_by_db: dict[Path, list[_FileIdentity]],
    *,
    budget: _ProbeBudget,
) -> None:
    """Fail closed if database names or target inodes changed during a probe."""
    after_dbs, after_complete = _db_paths(home, budget=budget)
    if not after_complete:
        budget.add("database_discovery_incomplete")
    elif [str(path) for path in after_dbs] != [str(path) for path in dbs]:
        budget.add("database_set_changed")
    for db_path, targets in targets_by_db.items():
        if budget.expired():
            return
        _targets_unchanged(db_path, targets, budget=budget)


def _revalidate_attachments(
    scan: _FdScan,
    *,
    proc_root: Path,
    budget: _ProbeBudget,
) -> dict[int, tuple[_ProcessInfo, set[tuple[int, int]]]]:
    """Revalidate every attached PID incarnation exactly once."""
    validated: dict[int, tuple[_ProcessInfo, set[tuple[int, int]]]] = {}
    for pid, (observed, attached) in scan.attachments.items():
        if budget.expired():
            break
        outcome, current = _revalidate_process(observed, proc_root)
        if outcome in {"exited", "dead"}:
            continue
        if outcome == "reused":
            budget.add("pid_reuse")
            continue
        if outcome != "same" or current is None:
            budget.add("process_changed")
            continue
        validated[pid] = (current, attached)
    return validated


def _darwin_codex_processes(budget: _ProbeBudget) -> tuple[list[_ProcessInfo], bool]:
    """Return a locale-stable, bounded macOS process snapshot."""
    remaining = budget.deadline - budget.clock()
    if remaining <= 0:
        budget.add("probe_timeout")
        return [], False
    try:
        result = subprocess.run(
            ["ps", "-axo", "pid=,state=,lstart=,comm="],
            capture_output=True,
            env={**os.environ, "LC_ALL": "C"},
            text=True,
            timeout=remaining,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        budget.add("portable_inspection_incomplete")
        return [], False
    if result.returncode != 0:
        budget.add("portable_inspection_incomplete")
        return [], False

    processes: list[_ProcessInfo] = []
    for row_count, row in enumerate(result.stdout.splitlines(), start=1):
        if budget.expired():
            return processes, False
        if row_count > _MAX_PROCESSES:
            budget.add("process_limit")
            return processes, False
        fields = row.split(maxsplit=7)
        if len(fields) != 8:
            if row.strip():
                budget.add("portable_inspection_incomplete")
                return processes, False
            continue
        try:
            pid = int(fields[0])
        except ValueError:
            budget.add("portable_inspection_incomplete")
            return processes, False
        executable = _normalize_process_name(fields[7])
        if "codex" not in executable:
            continue
        fingerprint = int.from_bytes(
            hashlib.sha256(" ".join(fields[2:7]).encode()).digest()[:8], "big"
        )
        processes.append(
            _ProcessInfo(
                pid=pid,
                uid=-1,
                state=fields[1][0],
                start_time=fingerprint or 1,
            )
        )
    return processes, True


def _toolhelp_has_entry(available: bool, error_code: int) -> bool:
    """Distinguish Toolhelp EOF from an enumeration failure."""
    if available:
        return True
    if error_code == _ERROR_NO_MORE_FILES:
        return False
    raise OSError(error_code, "Toolhelp process enumeration failed")


def _windows_codex_processes(budget: _ProbeBudget) -> tuple[list[_ProcessInfo], bool]:
    """Enumerate Windows executables with Toolhelp32, without dependencies."""
    try:  # pragma: no cover - exercised on Windows CI
        import ctypes
        from ctypes import wintypes

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = (
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_size_t),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", ctypes.c_long),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            )

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateToolhelp32Snapshot.argtypes = (wintypes.DWORD, wintypes.DWORD)
        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(PROCESSENTRY32W),
        )
        kernel32.Process32FirstW.restype = wintypes.BOOL
        kernel32.Process32NextW.argtypes = (
            wintypes.HANDLE,
            ctypes.POINTER(PROCESSENTRY32W),
        )
        kernel32.Process32NextW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        invalid_handle = ctypes.c_void_p(-1).value
        if snapshot == invalid_handle:
            raise OSError(ctypes.get_last_error(), "CreateToolhelp32Snapshot failed")
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        first = kernel32.Process32FirstW
        next_entry = kernel32.Process32NextW
        processes: list[_ProcessInfo] = []
        count = 0
        try:
            ctypes.set_last_error(0)
            available = _toolhelp_has_entry(
                bool(first(snapshot, ctypes.byref(entry))),
                ctypes.get_last_error(),
            )
            while available:
                if budget.expired():
                    return processes, False
                count += 1
                if count > _MAX_PROCESSES:
                    budget.add("process_limit")
                    return processes, False
                executable = _normalize_process_name(entry.szExeFile)
                if "codex" in executable:
                    from .session_home import _portable_process_identity

                    identity = _portable_process_identity(int(entry.th32ProcessID))
                    if identity is None:
                        budget.add("portable_inspection_incomplete")
                        return processes, False
                    state, start_time = identity
                    processes.append(
                        _ProcessInfo(
                            pid=int(entry.th32ProcessID),
                            uid=-1,
                            state=state,
                            start_time=start_time,
                        )
                    )
                ctypes.set_last_error(0)
                available = _toolhelp_has_entry(
                    bool(next_entry(snapshot, ctypes.byref(entry))),
                    ctypes.get_last_error(),
                )
        finally:
            kernel32.CloseHandle(snapshot)
        return processes, True
    except (ImportError, OSError, AttributeError):
        budget.add("portable_inspection_incomplete")
        return [], False


def _portable_codex_processes(
    platform_id: str, budget: _ProbeBudget
) -> tuple[list[_ProcessInfo], bool]:
    if platform_id == "darwin":
        return _darwin_codex_processes(budget)
    if platform_id.startswith("win"):
        return _windows_codex_processes(budget)
    budget.add("portable_inspection_incomplete")
    return [], False


def _pid_alive(pid: int) -> bool:
    """Compatibility helper implemented as a read-only ``/proc`` lookup."""
    info = _read_process_info(pid)
    return bool(info and info.state not in _DEAD_STATES)


def _pid_stopped(pid: int) -> bool:
    """Return whether a live process is stopped or being traced."""
    info = _read_process_info(pid)
    return bool(info and info.state not in _DEAD_STATES and info.stopped)


def _incomplete_component(reasons: list[str]) -> str:
    labels = [_REASON_LABELS.get(reason, reason.replace("_", " ")) for reason in reasons]
    return ", ".join(labels)


def _portable_probe(
    home: Path,
    dbs: list[Path],
    targets_by_db: dict[Path, list[_FileIdentity]],
    *,
    platform_id: str,
    budget: _ProbeBudget,
) -> LockStatus:
    """Conservatively gate non-Linux shared homes without relying on procfs."""
    from .session_home import (
        _PROCESS_LIVE,
        _PROCESS_UNKNOWN,
        _proc_identity,
        _sentinel_liveness,
        read_pid_sentinel,
    )

    db_path = dbs[0]
    holders: dict[int, _ProcessInfo] = {}
    sentinel_path = home / "codex.pid"
    try:
        sentinel_stat = sentinel_path.lstat()
    except FileNotFoundError:
        sentinel_stat = None
    except OSError:
        sentinel_stat = None
        budget.add("portable_inspection_incomplete")
    if sentinel_stat is not None:
        sentinel = read_pid_sentinel(sentinel_path)
        if sentinel is None:
            budget.add("portable_inspection_incomplete")
        elif sentinel.pid != os.getpid():
            liveness = _sentinel_liveness(sentinel, expected_home=home)
            if liveness == _PROCESS_UNKNOWN:
                budget.add("portable_inspection_incomplete")
            elif liveness == _PROCESS_LIVE:
                identity = _proc_identity(sentinel.pid)
                if identity is None:
                    budget.add("portable_inspection_incomplete")
                else:
                    state, start_time = identity
                    holders[sentinel.pid] = _ProcessInfo(
                        pid=sentinel.pid,
                        uid=-1,
                        state=state,
                        start_time=start_time,
                    )

    processes, complete = _portable_codex_processes(platform_id, budget)
    for info in processes:
        if info.state not in _DEAD_STATES:
            holders[info.pid] = info
    if not complete:
        budget.add("portable_inspection_incomplete")

    _revalidate_database_snapshot(home, dbs, targets_by_db, budget=budget)
    budget.expired()

    running = sorted(pid for pid, info in holders.items() if not info.stopped)
    stopped = sorted(pid for pid, info in holders.items() if info.stopped)
    reasons = list(budget.reasons)
    diagnostics_complete = not reasons
    if holders or reasons:
        if holders:
            states = [
                *(f"PID {pid} (running)" for pid in running),
                *(f"PID {pid} (stopped)" for pid in stopped),
            ]
            detail = (
                f"{db_path.name} has conservative {platform_id} Codex process "
                f"candidate(s): {', '.join(states)}"
            )
        else:
            detail = f"{db_path.name} portable contention inspection did not complete"
        if reasons:
            detail += f"; {_incomplete_component(reasons)} is incomplete, refusing unsafe access"
        return LockStatus(
            home=home,
            db_path=db_path,
            exists=True,
            locked=True,
            holder_pids=sorted(holders),
            stopped_pids=stopped,
            running_pids=running,
            detail=detail,
            diagnostics_complete=diagnostics_complete,
            incomplete_reasons=reasons,
        )
    return LockStatus(
        home=home,
        db_path=db_path,
        exists=True,
        locked=False,
        detail=(
            f"Codex local databases have no live portable Codex process candidates: "
            f"{', '.join(db.name for db in dbs)}"
        ),
    )


def probe(
    home: "Path | str | None" = None,
    *,
    proc_root: Path = _PROC_ROOT,
    deadline: float | None = None,
    timeout_s: float = _DEFAULT_PROBE_TIMEOUT_S,
    clock: Callable[[], float] | None = None,
    platform_id: str | None = None,
) -> LockStatus:
    """Inspect Codex database attachments once, within fixed work limits."""
    if home is None:
        home = os.environ.get("CODEX_HOME") or (Path.home() / ".codex")
    home = Path(home)
    clock = clock or time.monotonic
    budget = _ProbeBudget(
        deadline=deadline if deadline is not None else clock() + max(0.0, timeout_s),
        clock=clock,
    )
    dbs, discovery_complete = _db_paths(home, budget=budget)
    fallback = _db_path(home)
    if not dbs:
        if not discovery_complete:
            if not budget.reasons:
                budget.add("database_discovery_incomplete")
            reasons = list(budget.reasons)
            return LockStatus(
                home=home,
                db_path=fallback,
                exists=True,
                locked=True,
                detail=(f"{_incomplete_component(reasons)} is incomplete; refusing unsafe access"),
                diagnostics_complete=False,
                incomplete_reasons=reasons,
            )
        return LockStatus(
            home=home,
            db_path=fallback,
            exists=False,
            locked=False,
            detail="no Codex local database yet (uncontended)",
        )

    targets_by_db: dict[Path, list[_FileIdentity]] = {}
    for db_path in dbs:
        targets, complete = _target_files(db_path, budget=budget)
        targets_by_db[db_path] = targets
        if not complete:
            budget.add("database_target_incomplete")

    resolved_platform = platform_id or sys.platform
    if not resolved_platform.startswith("linux"):
        return _portable_probe(
            home,
            dbs,
            targets_by_db,
            platform_id=resolved_platform,
            budget=budget,
        )

    all_targets = [target for targets in targets_by_db.values() for target in targets]
    targets_by_identity: dict[tuple[int, int], list[_FileIdentity]] = {}
    targets_by_proc_identity: dict[tuple[int, int, int], list[_FileIdentity]] = {}
    for target in all_targets:
        targets_by_identity.setdefault(_identity_key(target.device, target.inode), []).append(
            target
        )
        major, minor = target.proc_device
        targets_by_proc_identity.setdefault((major, minor, target.inode), []).append(target)

    proc_locks, locks_complete = _read_proc_locks(proc_root, budget=budget)
    # Capture every relevant kernel lock-owner incarnation before the process
    # scan.  Revalidating this exact snapshot later closes the PID-reuse window
    # between reading /proc/locks and classifying its holder PID.
    lock_owner_observations: dict[int, _ProcessInfo | None] = {}
    for lock in proc_locks:
        if budget.expired():
            break
        if lock.pid <= 0 or lock.pid in lock_owner_observations:
            continue
        candidates = targets_by_proc_identity.get(
            (lock.device_major, lock.device_minor, lock.inode), ()
        )
        if not any(_lock_targets_file(lock, target) for target in candidates):
            continue
        observed = _read_process_info(lock.pid, proc_root)
        if observed is None or observed.state in _DEAD_STATES:
            budget.add("process_changed")
            observed = None
        lock_owner_observations[lock.pid] = observed

    scan = _scan_fd_holders(all_targets, proc_root, budget=budget)
    if not locks_complete or not scan.complete:
        budget.add("proc_inspection_incomplete")

    # Revalidate both the database set and every target inode after the single
    # process scan.  A concurrent replacement or new WAL/SHM sidecar is not a
    # clean result; it is an explicit fail-closed race outcome.
    _revalidate_database_snapshot(home, dbs, targets_by_db, budget=budget)

    validated = _revalidate_attachments(scan, proc_root=proc_root, budget=budget)

    holders_by_db: dict[Path, dict[int, _ProcessInfo]] = {db_path: {} for db_path in dbs}
    relevant_by_db: dict[Path, bool] = {db_path: False for db_path in dbs}
    lock_owner_cache: dict[int, _ProcessInfo | None] = {}
    for pid, (info, attached) in validated.items():
        if budget.expired():
            break
        for identity in attached:
            for target in targets_by_identity.get(identity, ()):
                holders_by_db[target.db_path][pid] = info

    for lock in (*proc_locks, *scan.locks):
        if budget.expired():
            break
        candidates = targets_by_proc_identity.get(
            (lock.device_major, lock.device_minor, lock.inode), ()
        )
        relevant_targets = [target for target in candidates if _lock_targets_file(lock, target)]
        if not relevant_targets:
            continue
        for target in relevant_targets:
            relevant_by_db[target.db_path] = True

        # POSIX lock rows identify an owning PID directly.  Validate each
        # unique incarnation once so a lock that races the FD snapshot still
        # names its discoverable holder instead of degrading to "unavailable".
        if lock.pid > 0:
            if lock.pid not in lock_owner_cache:
                observed = lock_owner_observations.get(lock.pid)
                if lock.pid not in lock_owner_observations:
                    attached = scan.attachments.get(lock.pid)
                    observed = attached[0] if attached is not None else None
                if observed is None:
                    budget.add("process_changed")
                    lock_owner_cache[lock.pid] = None
                else:
                    outcome, current = _revalidate_process(observed, proc_root)
                    if outcome == "reused":
                        budget.add("pid_reuse")
                        current = None
                    elif outcome != "same" or current is None:
                        budget.add("process_changed")
                        current = None
                    lock_owner_cache[lock.pid] = current
            current = lock_owner_cache[lock.pid]
            if current is not None:
                for target in relevant_targets:
                    holders_by_db[target.db_path][current.pid] = current

    budget.expired()
    unsafe_by_db = {
        db_path: (
            holders_by_db[db_path],
            bool(holders_by_db[db_path] or relevant_by_db[db_path]),
        )
        for db_path in dbs
    }

    reasons = list(budget.reasons)
    diagnostics_complete = discovery_complete and not reasons
    for db_path in dbs:
        holders, unsafe = unsafe_by_db[db_path]
        if not unsafe:
            continue
        running = sorted(pid for pid, info in holders.items() if not info.stopped)
        stopped = sorted(pid for pid, info in holders.items() if info.stopped)
        all_holders = sorted(holders)
        return LockStatus(
            home=home,
            db_path=db_path,
            exists=True,
            locked=True,
            holder_pids=all_holders,
            stopped_pids=stopped,
            running_pids=running,
            detail=_format_lock_detail(
                running,
                stopped,
                db_path.name,
                diagnostics_complete=diagnostics_complete,
                incomplete_component=_incomplete_component(reasons),
            ),
            diagnostics_complete=diagnostics_complete,
            incomplete_reasons=reasons,
        )

    if not diagnostics_complete:
        return LockStatus(
            home=home,
            db_path=dbs[0],
            exists=True,
            locked=True,
            detail=_format_lock_detail(
                [],
                [],
                dbs[0].name,
                diagnostics_complete=False,
                incomplete_component=_incomplete_component(reasons),
            ),
            diagnostics_complete=False,
            incomplete_reasons=reasons,
        )

    names = ", ".join(db.name for db in dbs)
    return LockStatus(
        home=home,
        db_path=dbs[0],
        exists=True,
        locked=False,
        detail=f"Codex local databases have no live attachments: {names}",
    )


def _format_lock_detail(
    running: list[int],
    stopped: list[int],
    db_name: str,
    *,
    diagnostics_complete: bool = True,
    incomplete_component: str = "/proc holder inspection",
) -> str:
    """Describe every validated holder and its current process state."""
    if not running and not stopped:
        detail = f"{db_name} has SQLite lock evidence, but no holder passed /proc validation"
    else:
        states = [
            *(f"PID {pid} (running)" for pid in running),
            *(f"PID {pid} (stopped)" for pid in stopped),
        ]
        detail = f"{db_name} has live Codex database holder(s): {', '.join(states)}"
    if not diagnostics_complete:
        detail += f"; {incomplete_component} is incomplete, refusing unsafe access"
    return detail


def remediation_hint(status: LockStatus) -> str:
    """Return mode-aware guidance for a contended shared home."""
    lines = [
        f"tokenpak: Codex local database is locked: {status.db_path}",
        f"          {status.detail}",
    ]
    if status.stopped_pids:
        lines.append(
            "          a stopped holder must be resumed and exited normally "
            "before this shared home is safe"
        )
    elif status.running_pids:
        lines.append("          finish or close the running session normally before retrying")
    lines.append(
        "          to run a parallel session without contention, set "
        "TOKENPAK_CODEX_SESSION_MODE=workspace (per-project home) or "
        "=isolated (fresh per-session home)."
    )
    return "\n".join(lines)
