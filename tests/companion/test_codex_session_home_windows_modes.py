# SPDX-License-Identifier: Apache-2.0
"""Codex shared-home leases on a platform without POSIX permission bits.

Windows Python has no ``os.fchmod`` before 3.13, and where it exists it only
toggles the read-only flag, so ``st_mode`` reports 0o666 whoever may open the
file. The shared-home lease path called ``os.fchmod`` unguarded and compared
permission bits, so it failed there. These tests emulate that platform on any
host and check that POSIX hosts keep enforcing the bits.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path

import pytest

from tokenpak.companion.codex import session_home as sh


@pytest.fixture
def shared_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> sh.SessionPaths:
    user_home = tmp_path / "user"
    source = user_home / ".codex"
    source.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(user_home))
    monkeypatch.delenv(sh.ENV_CODEX_HOME, raising=False)
    monkeypatch.delenv(sh.ENV_SESSION_MODE, raising=False)
    return sh.select_paths(
        "shared",
        workspace_dir=tmp_path,
        source_home=source,
        selected_home=source,
    )


@pytest.fixture
def windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Emulate Windows: no directory descriptors, no fchmod, no POSIX modes."""
    monkeypatch.setattr(sh, "_WINDOWS", True, raising=False)
    monkeypatch.delattr(os, "fchmod", raising=False)


def _write_world_readable(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o644)


def test_shared_lease_works_without_fchmod(shared_paths, windows):
    lease = sh.SessionLease.acquire(shared_paths, session_id="windows")
    try:
        assert lease.owns_sentinel is True
        assert sh.read_pid_sentinel(shared_paths.pid_sentinel) == lease.sentinel
    finally:
        lease.release()
    assert not shared_paths.pid_sentinel.exists()


def test_existing_lease_guard_without_posix_bits_is_accepted(shared_paths, windows):
    shared_paths.home.mkdir(parents=True, exist_ok=True)
    _write_world_readable(shared_paths.home / sh._LEASE_GUARD_NAME, b"\0")
    lease = sh.SessionLease.acquire(shared_paths, session_id="windows-guard")
    lease.release()


def test_posix_still_refuses_a_lease_guard_open_to_others(shared_paths):
    shared_paths.home.mkdir(parents=True, exist_ok=True)
    _write_world_readable(shared_paths.home / sh._LEASE_GUARD_NAME, b"\0")
    with pytest.raises(sh.HomeInUseError, match="lease guard"):
        sh.SessionLease.acquire(shared_paths, session_id="posix-guard")


def _sentinel_bytes(shared_paths: sh.SessionPaths, monkeypatch) -> tuple[bytes, object]:
    """A valid sentinel payload, produced by a real lease on the emulated platform."""
    monkeypatch.setattr(sh, "_WINDOWS", True, raising=False)
    lease = sh.SessionLease.acquire(shared_paths, session_id="payload")
    try:
        sentinel = lease.sentinel
    finally:
        lease.release()
    monkeypatch.setattr(sh, "_WINDOWS", False, raising=False)
    return (json.dumps(asdict(sentinel), sort_keys=True) + "\n").encode(), sentinel


def test_sentinel_without_posix_bits_is_read_on_windows(shared_paths, monkeypatch):
    payload, sentinel = _sentinel_bytes(shared_paths, monkeypatch)
    _write_world_readable(shared_paths.pid_sentinel, payload)
    monkeypatch.setattr(sh, "_WINDOWS", True)
    assert sh.read_pid_sentinel(shared_paths.pid_sentinel) == sentinel


def test_posix_still_ignores_a_sentinel_open_to_others(shared_paths, monkeypatch):
    payload, _ = _sentinel_bytes(shared_paths, monkeypatch)
    _write_world_readable(shared_paths.pid_sentinel, payload)
    assert sh.read_pid_sentinel(shared_paths.pid_sentinel) is None


def test_restrict_mode_applies_bits_only_where_they_exist(tmp_path, monkeypatch):
    target = tmp_path / "private"
    target.write_bytes(b"x")
    target.chmod(0o644)
    fd = os.open(target, os.O_RDONLY)
    try:
        monkeypatch.setattr(sh, "_WINDOWS", False, raising=False)
        sh._restrict_mode(fd, 0o600)
        assert target.stat().st_mode & 0o777 == 0o600
        target.chmod(0o644)
        monkeypatch.setattr(sh, "_WINDOWS", True)
        monkeypatch.delattr(os, "fchmod", raising=False)
        sh._restrict_mode(fd, 0o600)  # no fchmod on this platform: a no-op
        assert target.stat().st_mode & 0o777 == 0o644
    finally:
        os.close(fd)
