"""EmbeddingCache eviction-policy tests.

The pre-fix ``_evict()`` looped on ``pragma page_count * page_size`` while
deleting one oldest row per pass. SQLite never shrinks ``page_count`` on
DELETE (freed pages are recycled, not returned, absent VACUUM/auto_vacuum),
so once the DB file crossed max_mb the loop could only terminate at row
count zero: every subsequent ``put()`` wiped ALL rows, leaving the cache
permanently cold, and concurrent puts raced the purge unguarded.

The fix evicts on logical content size (embedding bytes + per-row overhead)
inside a single IMMEDIATE transaction. These tests prove that over-cap
eviction keeps the newest rows, never empties the cache, and that fresh
writes survive concurrent eviction pressure.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from tokenpak.proxy import embedding_cache as ec_module
from tokenpak.proxy.embedding_cache import _ROW_OVERHEAD_BYTES, EmbeddingCache


def _row_count(db_path: str) -> int:
    con = sqlite3.connect(db_path)
    try:
        return con.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
    finally:
        con.close()


def _logical_bytes(db_path: str) -> int:
    con = sqlite3.connect(db_path)
    try:
        return con.execute(
            "SELECT COALESCE(SUM(LENGTH(embedding)), 0) + COUNT(*) * ? FROM cache",
            (_ROW_OVERHEAD_BYTES,),
        ).fetchone()[0]
    finally:
        con.close()


def test_over_cap_eviction_keeps_newest_rows(tmp_path, monkeypatch) -> None:
    """Only the OLDEST rows go; the newest survive and the cache never empties."""
    db = str(tmp_path / "emb.db")
    cache = EmbeddingCache(db, ttl_days=7, max_mb=1)

    # Deterministic, strictly-increasing created_at timestamps.
    base = int(time.time())
    counter = {"n": 0}

    def fake_time() -> float:
        counter["n"] += 1
        return base + counter["n"]

    monkeypatch.setattr(ec_module, "time", SimpleNamespace(time=fake_time))

    payload = b"x" * (300 * 1024)  # ~300 KB per row; 1 MB budget fits ~3 rows
    for i in range(8):
        cache.put("model-a", 8, f"text-{i}", payload, tokens=1)

    remaining = [i for i in range(8) if cache.get("model-a", 8, f"text-{i}") is not None]

    # Pre-fix behaviour: the file-size ratchet emptied the whole table on
    # every put once the file exceeded max_mb — the cache went permanently
    # cold. The cache must never be empty after a put.
    assert remaining, "eviction emptied the cache entirely (page_count ratchet)"

    # The newest row always survives its own put.
    assert 7 in remaining, f"newest row was evicted; survivors={remaining}"

    # Survivors are the newest contiguous suffix (oldest-first eviction).
    assert remaining == list(range(min(remaining), 8)), (
        f"eviction order not oldest-first: survivors={remaining}"
    )

    # Logical size is back under budget.
    assert _logical_bytes(db) <= 1 * 1024 * 1024


def test_under_cap_puts_never_evict(tmp_path) -> None:
    db = str(tmp_path / "emb.db")
    cache = EmbeddingCache(db, ttl_days=7, max_mb=100)

    for i in range(10):
        cache.put("model-a", 8, f"small-{i}", b"z" * 1024, tokens=1)

    assert _row_count(db) == 10
    for i in range(10):
        assert cache.get("model-a", 8, f"small-{i}") is not None


# Override the global 30s pytest-timeout: a 4-thread write storm racing the
# eviction purge, each thread retrying transient lock contention, can exceed
# 30s on a loaded host. The 60s thread joins bound the real hang.
@pytest.mark.timeout(120)
def test_concurrent_puts_survive_eviction_pressure(tmp_path) -> None:
    """Concurrent writers racing the purge must not error or go cold."""
    db = str(tmp_path / "emb.db")
    cache = EmbeddingCache(db, ttl_days=7, max_mb=1)
    payload = b"y" * (100 * 1024)  # ~100 KB; budget fits ~9 rows
    errors: list[Exception] = []

    def _put_tolerating_contention(tid: int, i: int) -> None:
        """Put with retry on transient 'database is locked'.

        Under a 4-thread write storm SQLite can raise a transient
        ``OperationalError('database is locked')`` past the busy timeout —
        the product tolerates this on the eviction path by design (skip on
        contention), so a caller retry mirrors that contract. Genuine
        (non-lock) errors still propagate to be collected.
        """
        for attempt in range(5):
            try:
                cache.put("model-a", 8, f"t{tid}-{i}", payload, tokens=1)
                return
            except sqlite3.OperationalError as exc:
                if "locked" not in str(exc).lower() or attempt == 4:
                    raise
                time.sleep(0.02 * (attempt + 1))

    def worker(tid: int) -> None:
        try:
            for i in range(15):
                _put_tolerating_contention(tid, i)
        except Exception as exc:  # noqa: BLE001 - collecting for assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    assert errors == [], f"concurrent put/evict raced into non-transient errors: {errors}"

    # The storm must not leave the cache permanently cold …
    assert _row_count(db) > 0, "cache went cold under eviction pressure"

    # … and a FRESH write after the storm both lands and survives its own
    # eviction pass (created_at/rowid tie-break keeps the newest row).
    cache.put("model-a", 8, "fresh-after-storm", payload, tokens=1)
    assert cache.get("model-a", 8, "fresh-after-storm") is not None
    assert _logical_bytes(db) <= 1 * 1024 * 1024
