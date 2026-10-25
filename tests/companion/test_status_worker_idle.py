# SPDX-License-Identifier: Apache-2.0
"""Virtual-clock coverage of idle refresh scheduling; no host timing oracle."""

from pathlib import Path

from tokenpak.status import binding, worker


def run_clock(monkeypatch, tmp_path, *, end=95, change_at=None, fail=False):
    clock = [0.0]
    calls = []
    monkeypatch.setenv(binding.ENV, str(tmp_path))
    binding.write_session("session-one")
    monkeypatch.setattr(worker.signal, "signal", lambda *a: None)
    monkeypatch.setattr(worker.os, "getppid", lambda: 20 if clock[0] < end else 1)
    monkeypatch.setattr(worker.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(worker.time, "time", lambda: 10000 + clock[0])
    monkeypatch.setattr(worker.time, "sleep", lambda delay: clock.__setitem__(0, clock[0] + delay))

    class Watch:
        def __init__(self, directory):
            pass

        def stamp(self):
            return ("session-one", int(change_at is not None and clock[0] >= change_at))

    monkeypatch.setattr(worker, "InputWatch", Watch, raising=False)

    def refresh(*args):
        calls.append(round(clock[0], 3))
        if fail:
            raise ValueError("fixture unavailable")

    monkeypatch.setattr(worker, "refresh", refresh)
    assert worker.main() == 0
    return calls


def test_unchanged_inputs_refresh_at_most_every_thirty_seconds(monkeypatch, tmp_path):
    calls = run_clock(monkeypatch, tmp_path)
    assert len(calls) == 4
    assert all(b - a >= 30 for a, b in zip(calls, calls[1:]))


def test_changed_inputs_resume_within_two_seconds(monkeypatch, tmp_path):
    calls = run_clock(monkeypatch, tmp_path, end=45, change_at=7)
    assert len(calls) == 3
    assert 7 <= calls[1] <= 9.01
    assert calls[2] - calls[1] >= 30


def test_refresh_errors_also_back_off(monkeypatch, tmp_path):
    calls = run_clock(monkeypatch, tmp_path, end=65, fail=True)
    assert len(calls) == 3
    assert all(b - a >= 30 for a, b in zip(calls, calls[1:]))


def test_watch_uses_metadata_without_reading_transcript_or_corpus(tmp_path, monkeypatch):
    monkeypatch.setenv(binding.ENV, str(tmp_path))
    monkeypatch.setenv("TOKENPAK_HOME", str(tmp_path / "state"))
    binding.write_session("session-one")
    watch = worker.InputWatch(tmp_path)
    first = watch.stamp()
    writes = tmp_path / "settings.json"
    writes.write_text("{}")
    assert watch.stamp() != first
    first = watch.stamp()
    assert watch.stamp() == first
    binding.write_session("session-two")
    assert watch.stamp() != first

    def reject(*args, **kwargs):
        raise AssertionError("input watch must not scan directories")

    monkeypatch.setattr(Path, "iterdir", reject)
    monkeypatch.setattr(Path, "glob", reject)
    monkeypatch.setattr(Path, "rglob", reject)
    watch.stamp()
