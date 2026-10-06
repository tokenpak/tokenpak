"""License write lock: backend selection, safe failure, release."""

import sys
import types

import pytest

import tokenpak.licensing as mod
from tokenpak.licensing import LicenseLockError, _license_write_lock


def _fake_msvcrt(held, calls):
    m = types.ModuleType("msvcrt")
    m.LK_NBLCK, m.LK_UNLCK = 2, 0

    def locking(fd, mode, n):
        calls.append((mode, n))
        if mode == m.LK_NBLCK and held:
            raise OSError("locked")

    m.locking = locking
    return m


def test_no_lock_backend_fails_before_any_write(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.setitem(sys.modules, "msvcrt", None)
    p = tmp_path / "license.json"
    ran = []
    with pytest.raises(LicenseLockError):
        with _license_write_lock(p):
            ran.append(1)
    assert not ran and not p.exists()


def test_windows_backend_locks_byte_zero_and_releases_on_error(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.setitem(sys.modules, "msvcrt", _fake_msvcrt(False, calls))
    with pytest.raises(RuntimeError):
        with _license_write_lock(tmp_path / "license.json"):
            raise RuntimeError("boom")
    assert calls == [(2, 1), (0, 1)]


def test_windows_contention_times_out_without_running_body(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "fcntl", None)
    monkeypatch.setitem(sys.modules, "msvcrt", _fake_msvcrt(True, calls))
    monkeypatch.setattr(mod, "_LICENSE_LOCK_TIMEOUT", 0.2)
    monkeypatch.setattr(mod, "_LICENSE_LOCK_POLL", 0.01)
    ran = []
    with pytest.raises(LicenseLockError):
        with _license_write_lock(tmp_path / "license.json"):
            ran.append(1)
    assert not ran and len(calls) > 2 and all(c[0] == 2 for c in calls)


def test_unopenable_lock_path_fails_safely(tmp_path):
    p = tmp_path / "license.json"
    (tmp_path / "license.json.lock").mkdir()
    ran = []
    with pytest.raises(OSError):
        with _license_write_lock(p):
            ran.append(1)
    assert not ran and not p.exists()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX flock regression")
def test_posix_flock_excludes_other_open_file(tmp_path):
    import fcntl

    p = tmp_path / "license.json"
    with _license_write_lock(p):
        with open(tmp_path / "license.json.lock", "a") as other:
            with pytest.raises(OSError):
                fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with open(tmp_path / "license.json.lock", "a") as other:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
