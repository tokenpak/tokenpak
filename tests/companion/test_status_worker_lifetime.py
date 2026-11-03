# SPDX-License-Identifier: Apache-2.0
"""Session lifetime fences with fake parents and clocks; no real signals."""

import shutil
import time
from pathlib import Path

import pytest

from tokenpak.status import binding, worker


def drive(monkeypatch, tmp_path, *, event=None, fail=None, end=10):
    launch = tmp_path / "run"
    launch.mkdir()
    monkeypatch.setenv(binding.ENV, str(launch))
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.chdir(tmp_path)
    binding.write_session("session-one")
    watch = worker.InputWatch(launch)
    transcript = watch.transcripts / "session-one.jsonl"
    transcript.parent.mkdir(parents=True)
    transcript.touch()
    clock = [0.0]
    calls = []
    real_time = time.time()
    handlers = {}
    monkeypatch.setattr(worker.signal, "signal", lambda sig, cb: handlers.__setitem__(sig, cb))
    monkeypatch.setattr(worker.os, "getppid", lambda: 20 if clock[0] < end else 1)
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(worker.time, "time", lambda: real_time + clock[0])

    def sleep(delay):
        clock[0] += delay
        if event:
            event(clock[0], launch, transcript, handlers)

    monkeypatch.setattr(worker.time, "sleep", sleep)

    def refresh(*args):
        calls.append(clock[0])
        if fail:
            raise fail("fixture unavailable")

    monkeypatch.setattr(worker, "refresh", refresh)
    assert worker.main() == 0
    return clock[0], calls


def test_deleted_run_directory_ends_writer(monkeypatch, tmp_path):
    def event(now, launch, transcript, handlers):
        if now >= 1 and launch.exists():
            shutil.rmtree(launch)

    elapsed, calls = drive(monkeypatch, tmp_path, event=event)
    assert elapsed <= 2.01
    assert len(calls) == 1


def test_deleted_bound_transcript_ends_writer(monkeypatch, tmp_path):
    def event(now, launch, transcript, handlers):
        if now >= 1:
            transcript.unlink(missing_ok=True)

    elapsed, calls = drive(monkeypatch, tmp_path, event=event)
    assert elapsed <= 2.01
    assert len(calls) == 1


def test_idle_session_expires_even_if_parent_is_alive(monkeypatch, tmp_path):
    elapsed, calls = drive(monkeypatch, tmp_path, end=3700)
    assert 3599.99 <= elapsed <= 3602.01


def test_writer_outputs_and_shared_ledger_do_not_extend_lifetime(monkeypatch, tmp_path):
    monkeypatch.setattr(worker, "SESSION_IDLE_SECONDS", 5, raising=False)

    def event(now, launch, transcript, handlers):
        output = launch / "status"
        output.mkdir(exist_ok=True)
        (output / "session-one.line").touch()
        (tmp_path / "state").mkdir(exist_ok=True)
        (tmp_path / "state/monitor.db-wal").touch()

    elapsed, calls = drive(monkeypatch, tmp_path, end=20, event=event)
    assert elapsed <= 8.01


def test_clear_switches_lifetime_binding_before_old_transcript_disappears(monkeypatch, tmp_path):
    changed = [False]

    def event(now, launch, transcript, handlers):
        if now >= 1 and not changed[0]:
            changed[0] = True
            binding.write_session("session-two")
            transcript.unlink()

    elapsed, calls = drive(monkeypatch, tmp_path, event=event)
    assert elapsed >= 10
    assert len(calls) == 2


def test_new_transcript_gets_creation_grace(monkeypatch, tmp_path):
    monkeypatch.setenv(binding.ENV, str(tmp_path))
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "state"))
    binding.write_session("new-session")
    watch = worker.InputWatch(tmp_path)
    lifetime = worker.SessionLifetime(tmp_path, watch.transcripts)
    assert lifetime.alive()


def test_transcript_activity_keeps_session_alive(monkeypatch, tmp_path):
    # Use a shortened idle window only to reduce fixture writes.
    monkeypatch.setattr(worker, "SESSION_IDLE_SECONDS", 5, raising=False)
    touched = [False]

    def event(now, launch, transcript, handlers):
        if now >= 4 and not touched[0]:
            touched[0] = True
            transcript.write_text("activity")

    elapsed, calls = drive(monkeypatch, tmp_path, event=event, end=20)
    assert 9 <= elapsed <= 10.1


def test_unexpected_refresh_error_waits_before_retry(monkeypatch, tmp_path):
    elapsed, calls = drive(monkeypatch, tmp_path, fail=RuntimeError, end=65)
    assert len(calls) == 3
    assert all(b - a >= 30 for a, b in zip(calls, calls[1:]))


def test_signal_stops_writer_within_sleep_slice(monkeypatch, tmp_path):
    def event(now, launch, transcript, handlers):
        if now >= 1:
            handlers[worker.signal.SIGTERM](None, None)

    elapsed, calls = drive(monkeypatch, tmp_path, event=event)
    assert elapsed <= 1.11
    assert len(calls) == 1


def test_deleted_binding_marker_ends_writer(monkeypatch, tmp_path):
    def event(now, launch, transcript, handlers):
        if now >= 1:
            (launch / "current-session").unlink(missing_ok=True)

    elapsed, calls = drive(monkeypatch, tmp_path, event=event)
    assert elapsed <= 2.01


def test_stale_launch_exits_before_first_refresh(monkeypatch, tmp_path):
    import os

    monkeypatch.setenv(binding.ENV, str(tmp_path))
    binding.write_session("old-session")
    old = time.time() - 7200
    os.utime(tmp_path / "current-session", (old, old))
    os.utime(tmp_path, (old, old))
    lifetime = worker.SessionLifetime(tmp_path, tmp_path / "absent-transcripts")
    assert not lifetime.alive()


def test_parent_change_still_ends_writer(monkeypatch, tmp_path):
    elapsed, calls = drive(monkeypatch, tmp_path, end=1)
    assert elapsed <= 1.11
    assert len(calls) == 1
