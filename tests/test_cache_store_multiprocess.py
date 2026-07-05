"""CacheStore cross-process write-safety tests.

The pre-fix ``CacheStore._save()`` had two independent defects when two
processes shared one store path:

1. A FIXED shared tmp name (``path.with_suffix(".tmp")``): writer A could
   ``os.replace`` the tmp file out from under writer B, so B's replace hit
   FileNotFoundError — swallowed as an error log, silently dropping B's
   whole update — or shipped A's half-written payload.
2. Whole-file read-modify-write with no inter-process lock: last writer
   wins, losing every key the other process had written since its load.

The fix uses per-writer unique tmp names plus an fcntl.flock-guarded
read-merge-write cycle. These tests prove no key is lost across two
concurrently writing processes, and that failed saves are surfaced
(counted) instead of being silently swallowed.
"""

from __future__ import annotations

import json
import multiprocessing
import sys
from pathlib import Path

import pytest

from tokenpak.cache.cache_store import CacheStore

KEYS_PER_PROC = 25


def _writer_proc(path: str, prefix: str, barrier) -> None:
    """Write KEYS_PER_PROC distinct keys through a fresh CacheStore."""
    store = CacheStore(path=path)
    barrier.wait(timeout=20)  # maximize overlap between the two writers
    for i in range(KEYS_PER_PROC):
        store.set(f"{prefix}_{i}", {"n": i, "who": prefix})
    if store.save_errors:
        # Any swallowed save means the assertion below may pass vacuously.
        sys.exit(3)


@pytest.mark.skipif(sys.platform == "win32", reason="fcntl lock is POSIX-only")
def test_two_process_concurrent_writes_no_key_loss(tmp_path: Path) -> None:
    path = tmp_path / "store.json"
    ctx = multiprocessing.get_context()
    barrier = ctx.Barrier(2)

    procs = [
        ctx.Process(target=_writer_proc, args=(str(path), prefix, barrier))
        for prefix in ("alpha", "beta")
    ]
    for p in procs:
        p.start()
    for p in procs:
        p.join(timeout=25)
        assert p.exitcode == 0, f"writer process failed (exitcode={p.exitcode})"

    # The final file must be complete, parseable JSON …
    on_disk = json.loads(path.read_text(encoding="utf-8"))

    # … containing EVERY key from BOTH processes (pre-fix: last-writer-wins
    # dropped an entire process's key set, and tmp-name collisions silently
    # dropped whole updates).
    expected = {f"{prefix}_{i}" for prefix in ("alpha", "beta") for i in range(KEYS_PER_PROC)}
    missing = expected - set(on_disk)
    assert not missing, f"lost {len(missing)} keys across processes: {sorted(missing)[:5]}…"

    # A fresh reader sees the merged state too.
    reader = CacheStore(path=path)
    assert reader.get("alpha_0") == {"n": 0, "who": "alpha"}
    assert reader.get(f"beta_{KEYS_PER_PROC - 1}") == {
        "n": KEYS_PER_PROC - 1,
        "who": "beta",
    }

    # No stale tmp files may linger after clean shutdown.
    leftovers = [p.name for p in tmp_path.iterdir() if ".tmp." in p.name]
    assert not leftovers, f"tmp files left behind: {leftovers}"


def test_delete_survives_cross_process_merge(tmp_path: Path) -> None:
    """A local delete must not be resurrected by the save merge."""
    path = tmp_path / "store.json"
    store = CacheStore(path=path)
    store.set("keep", 1)
    store.set("gone", 2)
    store.delete("gone")

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert "keep" in on_disk
    assert "gone" not in on_disk


def test_clear_resets_disk_state(tmp_path: Path) -> None:
    path = tmp_path / "store.json"
    store = CacheStore(path=path)
    store.set("a", 1)
    store.set("b", 2)
    store.clear()

    assert json.loads(path.read_text(encoding="utf-8")) == {}
    assert store.get("a") is None


def test_failed_save_is_counted_not_swallowed(tmp_path: Path) -> None:
    """A failed save must be surfaced via save_errors, not silently dropped."""
    # Pointing the store at an existing DIRECTORY makes os.replace fail.
    target = tmp_path / "adir"
    target.mkdir()
    store = CacheStore(path=target)

    store.set("k", "v")

    assert store.save_errors >= 1
    assert store.last_save_error
    # The in-memory value survives even though persistence failed.
    assert store.get("k") == "v"
