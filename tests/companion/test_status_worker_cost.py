# SPDX-License-Identifier: Apache-2.0
"""Work-count regressions for the launch-local status writer."""

from pathlib import Path

import pytest

from tokenpak.status import binding, worker
from tokenpak.status.snapshot import StatusSnapshot


@pytest.fixture
def bound(tmp_path, monkeypatch):
    monkeypatch.setenv(binding.ENV, str(tmp_path))
    binding.write_session("session-one")
    monkeypatch.setattr(worker, "pending_version", lambda: "")
    monkeypatch.setattr(worker, "fetch_snapshot", lambda sid, *a, **kw: StatusSnapshot(sid, 100))
    return tmp_path


@pytest.mark.parametrize("entries", [0, 1000])
def test_refresh_does_not_enumerate_run_or_cache(bound, monkeypatch, entries):
    """Regression: each tick scanned every disposable-cache directory entry."""
    cache = bound / "status"
    cache.mkdir()
    for number in range(entries):
        (cache / f"unrelated-{number}.txt").touch()
    calls = []
    original = Path.iterdir

    def counted(path):
        calls.append(path)
        return original(path)

    monkeypatch.setattr(Path, "iterdir", counted)
    worker.refresh(bound, "http://127.0.0.1:1", "unknown")
    worker.refresh(bound, "http://127.0.0.1:1", "unknown")
    assert calls == []
    assert (cache / "session-one.json").is_file()


def test_refresh_removes_only_its_previous_session_pair(bound):
    worker.refresh(bound, "http://127.0.0.1:1", "unknown")
    cache = bound / "status"
    (cache / "other.json").write_text("retained")
    binding.write_session("session-two")
    worker.refresh(bound, "http://127.0.0.1:1", "unknown")
    assert not (cache / "session-one.json").exists()
    assert not (cache / "session-one.line").exists()
    assert (cache / "other.json").read_text() == "retained"
    assert (cache / "session-two.line").is_file()
