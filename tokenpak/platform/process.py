# SPDX-License-Identifier: Apache-2.0
"""Cross-platform process lifecycle adapters.

Replaces unguarded POSIX-only assumptions (``os.kill(pid, 0)`` as a liveness
probe, ``pkill``/``ss`` shellouts, ``start_new_session`` detach, ``Path.home() /
"tokenpak"`` cwd fallbacks) with helpers that behave correctly — or report an
honest unsupported result — on Linux, macOS, and native Windows.

Dispatch is on :func:`current_platform`, which reads ``sys.platform`` at call
time so behavior can be exercised under simulated platforms in tests.

Nothing here is public API: ``__all__`` is empty so the module contributes no
symbols to the public-API snapshot. Consumers import this module and call
through the module qualifier (e.g. ``process.pid_alive(pid)``).
"""

from __future__ import annotations

import os
import shutil
import signal
import socket
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

__all__: list[str] = []


# Windows process-creation flags. Referenced via getattr so this module imports
# on POSIX (where the subprocess constants are absent); the literal fallbacks
# match the Win32 values so the dispatched value is correct even when the code
# path is exercised under a simulated platform on a POSIX host.
_DETACHED_PROCESS = getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
_CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

_WINDOWS_STILL_ACTIVE = 259
_WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


def current_platform() -> str:
    """Return a normalized platform label: ``linux`` / ``macos`` / ``windows`` / ``other``.

    Reads ``sys.platform`` live so tests can simulate platforms via monkeypatch.
    """
    plat = sys.platform
    if plat.startswith("linux"):
        return "linux"
    if plat == "darwin":
        return "macos"
    if plat in ("win32", "cygwin") or plat.startswith("win"):
        return "windows"
    return "other"


def is_windows() -> bool:
    return current_platform() == "windows"


def is_macos() -> bool:
    return current_platform() == "macos"


def is_linux() -> bool:
    return current_platform() == "linux"


def is_posix() -> bool:
    """True on Linux/macOS (and other POSIX), False on native Windows."""
    return not is_windows()


def pid_alive(pid: int) -> bool:
    """Return True iff a process with ``pid`` currently exists.

    POSIX uses ``os.kill(pid, 0)`` (a no-op signal that only probes existence).
    On native Windows ``os.kill(pid, 0)`` is NOT a liveness probe — Python maps
    arbitrary signals to ``TerminateProcess``, so it would try to *kill* the
    process. Windows therefore uses an ``OpenProcess`` / ``GetExitCodeProcess``
    probe via ctypes instead.
    """
    if pid is None or pid <= 0:
        return False
    if is_windows():
        return _pid_alive_windows(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Process exists but is owned by another user / not signalable.
        return True
    except OSError:
        return False
    return True


def _pid_alive_windows(pid: int) -> bool:
    """Windows liveness probe via OpenProcess + GetExitCodeProcess.

    Returns False (rather than raising) if the Win32 APIs are unavailable, so a
    degraded host never crashes a caller's stale-PID handling.
    """
    try:
        import ctypes  # noqa: PLC0415 — Windows-only, imported lazily

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(
            _WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            if not ok:
                return False
            return exit_code.value == _WINDOWS_STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return False


def terminate(pid: int, *, force: bool = False) -> bool:
    """Terminate ``pid`` in a platform-safe way.

    Returns True if the signal was delivered or the process was already gone
    (idempotent stop), False on any other failure. POSIX sends ``SIGTERM``
    (or ``SIGKILL`` when ``force``); native Windows routes through ``os.kill``,
    which maps to ``TerminateProcess`` there.
    """
    if pid is None or pid <= 0:
        return False
    try:
        if is_windows():
            # On Windows os.kill with a non-CTRL signal calls TerminateProcess.
            os.kill(int(pid), signal.SIGTERM)
        else:
            os.kill(int(pid), signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        return True  # already gone — treat as a successful stop
    except OSError:
        return False
    return True


def start_background(
    cmd: Sequence[str],
    *,
    cwd: Optional[str] = None,
    env: Optional[dict] = None,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
) -> subprocess.Popen:
    """Launch a detached background process with platform-appropriate semantics.

    POSIX detaches via ``start_new_session=True`` (setsid). Native Windows uses
    ``DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP`` creation flags instead, since
    ``start_new_session`` is a POSIX-only concept.

    ``cwd`` defaults to None (inherit) — callers should NOT pass a guessed
    ``~/tokenpak`` directory; module execution (``-m tokenpak.proxy``) resolves
    the installed package regardless of the working directory.
    """
    popen_kwargs: dict = {
        "cwd": cwd,
        "env": env,
        "stdout": stdout,
        "stderr": stderr,
    }
    if is_windows():
        popen_kwargs["creationflags"] = _DETACHED_PROCESS | _CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    return subprocess.Popen(list(cmd), **popen_kwargs)


def port_in_use(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    """Return True iff a TCP connection to ``host:port`` succeeds.

    Pure-socket replacement for ``ss -tlnp`` — works identically on every
    platform and needs no external binary.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, int(port))) == 0
    except OSError:
        return False


def kill_by_pattern(patterns: Sequence[str]) -> tuple[bool, str]:
    """Best-effort kill of processes whose command line matches any pattern.

    POSIX (with ``pkill`` available) uses ``pkill -f`` to preserve existing Linux
    behavior. Native Windows — and any host lacking ``pkill`` — returns
    ``(False, <reason>)`` so the caller can fall back to PID-file-based
    termination rather than shelling out to a missing binary.
    """
    if is_windows():
        return (False, "kill-by-pattern unsupported on windows; use PID-file termination")
    if not shutil.which("pkill"):
        return (False, "pkill not available on this host")
    killed_any = False
    for pat in patterns:
        try:
            result = subprocess.run(["pkill", "-f", pat], timeout=5)
            if result.returncode in (0, 1):  # 0=killed, 1=no match
                killed_any = killed_any or result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            continue
    return (True, "pkill")


def read_pid_file(pid_path: Path) -> Optional[int]:
    """Read an integer PID from ``pid_path``; return None if absent/invalid."""
    try:
        if not pid_path.exists():
            return None
        return int(pid_path.read_text().strip())
    except (OSError, ValueError):
        return None
