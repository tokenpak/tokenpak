from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any

import pytest

from tokenpak.proxy import vault_bridge


def _write_index(root: Path, documents: dict[str, tuple[str, str]]) -> Path:
    blocks_dir = root / "blocks"
    blocks_dir.mkdir(exist_ok=True)
    metadata = {}
    for block_id, (source_path, content) in documents.items():
        (blocks_dir / f"{block_id}.txt").write_text(content, encoding="utf-8")
        metadata[block_id] = {
            "source_path": source_path,
            "content_hash": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "raw_tokens": len(content.split()),
        }

    index_path = root / "index.json"
    index_path.write_text(json.dumps({"blocks": metadata}), encoding="utf-8")
    return index_path


def _load_index(root: Path) -> vault_bridge.VaultIndex:
    index_path = root / "index.json"
    index = vault_bridge.VaultIndex(str(root))
    index._load(index_path, index_path.stat().st_mtime)
    return index


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("condition was not reached before timeout")


def test_compact_index_preserves_query_expansion_search_and_injection(tmp_path, monkeypatch):
    """Regression: compact postings preserve proxy query expansion and result bytes."""
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    _write_index(
        tmp_path,
        {
            "b1": ("docs/auth.md", "authentication authorization token auth"),
            "b2": ("docs/config.md", "configuration database settings config"),
            "b3": ("docs/request.md", "authentication authentication request message"),
            "b4": ("docs/misc.md", "garden flowers unrelated"),
        },
    )
    index = _load_index(tmp_path)

    assert vault_bridge._bm25_tokenize_query("auth") == [
        "auth",
        "authentication",
        "authentic",
        "authorization",
        "authoriz",
        "authenticate",
    ]
    assert vault_bridge._bm25_tokenize_query("authentication request") == [
        "authentication",
        "authentic",
        "auth",
        "request",
        "req",
    ]

    auth_results = index.search("auth", top_k=4, min_score=0)
    assert [block["block_id"] for block, _ in auth_results] == ["b1"]
    assert [score for _, score in auth_results] == pytest.approx([3.010769698264], rel=1e-12)
    assert auth_results[0][0] == {
        "block_id": "b1",
        "source_path": "docs/auth.md",
        "risk_class": "narrative",
        "must_keep": False,
        "raw_tokens": 4,
        "source_type": "filesystem",
        "claude_transcript": None,
        "content": "authentication authorization token auth",
    }

    phrase_results = index.search("authentication request", top_k=4, min_score=0)
    assert [block["block_id"] for block, _ in phrase_results] == ["b3", "b1"]
    assert [score for _, score in phrase_results] == pytest.approx(
        [2.138342251436, 1.841864062996], rel=1e-12
    )

    injection_text, tokens_used, source_refs = index.compile_injection(
        "authentication request", budget=1000, top_k=4, min_score=0
    )
    assert injection_text == (
        "\n\n## Retrieved Context\n"
        "--- [docs/request.md] (relevance: 2.1) ---\n"
        "authentication authentication request message\n\n"
        "--- [docs/auth.md] (relevance: 1.8) ---\n"
        "authentication authorization token auth"
    )
    assert tokens_used == vault_bridge.count_tokens(injection_text)
    assert source_refs == ["docs/request.md", "docs/auth.md"]
    assert all("content" not in block for block in index.blocks.values())


def test_content_hydration_lru_is_strictly_byte_bounded(tmp_path, monkeypatch):
    """Regression: selected block bytes never grow beyond the configured cache cap."""
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_MAX_BYTES", 96)
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    documents = {
        "b1": ("docs/one.md", "needle_one " + "x" * 60),
        "b2": ("docs/two.md", "needle_two " + "y" * 60),
        "b3": ("docs/huge.md", "needle_huge " + "z" * 120),
    }
    _write_index(tmp_path, documents)
    index = _load_index(tmp_path)

    assert index._cache_bytes == 0
    first_result = index.search("needle_one", top_k=1, min_score=0)
    assert first_result[0][0]["content"] == documents["b1"][1]
    assert index._cache_bytes <= 96
    second_result = index.search("needle_two", top_k=1, min_score=0)
    assert second_result[0][0]["content"] == documents["b2"][1]
    assert index._cache_bytes <= 96
    assert len(index._content_cache) == 1
    assert index.cache_stats["vault_cache_evictions"] == 1

    # Rehydrate the evicted block to prove disk fallback stays correct.
    assert index.search("needle_one", top_k=1, min_score=0)[0][0]["content"] == documents["b1"][1]
    assert index._cache_bytes <= 96
    assert index.cache_stats["vault_cache_evictions"] == 2
    assert index._max_cache_bytes == 96

    # A single block larger than the cap is served only as a bounded prefix.
    huge_result = index.search("needle_huge", top_k=1, min_score=0)
    huge_block = huge_result[0][0]
    assert huge_block["content"] == documents["b3"][1][:96]
    assert huge_block["content_truncated"] is True
    assert huge_block["content_bytes_total"] == len(documents["b3"][1])
    assert huge_block["content_bytes_loaded"] == 96
    assert index._cache_bytes == 96


def test_reload_replaces_postings_and_invalidates_cached_content(tmp_path, monkeypatch):
    """Regression: a new index generation cannot serve stale postings or content."""
    monkeypatch.setattr(vault_bridge, "VAULT_INDEX_RELOAD_INTERVAL", 0)
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 1)
    index_path = _write_index(tmp_path, {"b1": ("docs/item.md", "alpha old_marker")})

    index = vault_bridge.VaultIndex(str(tmp_path))
    index.maybe_reload()
    assert index.search("alpha", top_k=1, min_score=0)[0][0]["content"] == "alpha old_marker"
    first_postings = index._postings
    first_cache = index._content_cache
    first_mtime = index._last_mtime

    _write_index(tmp_path, {"b1": ("docs/item.md", "beta new_marker")})
    next_mtime = max(time.time() + 2, first_mtime + 2)
    os.utime(index_path, (next_mtime, next_mtime))
    os.utime(tmp_path / "blocks" / "b1.txt", (next_mtime, next_mtime))
    index.maybe_reload()

    assert index._last_mtime == next_mtime
    assert index._postings is not first_postings
    assert index._content_cache is not first_cache
    assert index.search("alpha", top_k=1, min_score=0) == []
    beta_results = index.search("beta", top_k=1, min_score=0)
    assert beta_results[0][0]["content"] == "beta new_marker"


def test_stale_metadata_hash_indexes_and_pins_stable_block_bytes(tmp_path, monkeypatch):
    """One stale index hash cannot take the whole retrieval generation offline."""
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    index_path = _write_index(tmp_path, {"b1": ("docs/item.md", "needle stable")})
    metadata = json.loads(index_path.read_text(encoding="utf-8"))
    metadata["blocks"]["b1"]["content_hash"] = "0" * 64
    index_path.write_text(json.dumps(metadata), encoding="utf-8")

    index = _load_index(tmp_path)

    results = index.search("needle", top_k=1, min_score=0)
    assert index.available
    assert results[0][0]["content"] == "needle stable"
    assert index.cache_stats["vault_index_stale_content_hashes"] == 1
    assert (
        index._snapshot_generation().content_records["b1"].content_hash
        == hashlib.sha256(b"needle stable").hexdigest()
    )


def test_missing_block_is_quarantined_without_hiding_healthy_siblings(tmp_path, monkeypatch):
    """One unreadable index entry cannot take the complete generation offline."""
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    _write_index(
        tmp_path,
        {
            "healthy": ("docs/healthy.md", "needle healthy content"),
            "missing": ("docs/missing.md", "needle missing content"),
        },
    )
    (tmp_path / "blocks" / "missing.txt").unlink()

    index = _load_index(tmp_path)

    assert index.available
    assert index._doc_count == 1
    assert index.cache_stats["vault_index_skipped_blocks"] == 1
    results = index.search("healthy", top_k=5, min_score=0)
    assert [block["block_id"] for block, _ in results] == ["healthy"]
    assert results[0][0]["content"] == "needle healthy content"


def test_oversized_block_is_quarantined_without_hiding_healthy_siblings(tmp_path, monkeypatch):
    """Loader allocation is bounded by the canonical vault walker file limit."""
    monkeypatch.setattr(vault_bridge, "_VAULT_BLOCK_MAX_BYTES", 32)
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    _write_index(
        tmp_path,
        {
            "healthy": ("docs/healthy.md", "needle healthy"),
            "oversized": ("docs/oversized.md", "needle " + "x" * 64),
        },
    )

    index = _load_index(tmp_path)

    assert index.available
    assert index._doc_count == 1
    assert index.cache_stats["vault_index_skipped_blocks"] == 1
    assert index.cache_stats["vault_index_oversized_blocks"] == 1
    results = index.search("healthy", top_k=5, min_score=0)
    assert [block["block_id"] for block, _ in results] == ["healthy"]


def test_stale_raw_tokens_use_actual_content_for_injection_budget(tmp_path, monkeypatch):
    """Stale metadata cannot admit a full oversized block under a small budget."""
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_MAX_BYTES", 64 * 1024)
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    content = "needle " + "x " * 1000
    index_path = _write_index(tmp_path, {"b1": ("docs/stale.md", content)})
    metadata = json.loads(index_path.read_text(encoding="utf-8"))
    metadata["blocks"]["b1"]["content_hash"] = "0" * 64
    metadata["blocks"]["b1"]["raw_tokens"] = 1
    index_path.write_text(json.dumps(metadata), encoding="utf-8")
    index = _load_index(tmp_path)

    result = index.search("needle", top_k=1, min_score=0)[0][0]
    injection, tokens_used, refs = index.compile_injection(
        "needle", budget=150, top_k=1, min_score=0
    )

    assert result["content"] == content
    assert not any(key.startswith("_content_") for key in result)
    assert index._snapshot_generation().content_records[
        "b1"
    ].actual_tokens == vault_bridge.count_tokens(content)
    assert len(injection) < len(content)
    assert tokens_used <= 150
    assert tokens_used < vault_bridge.count_tokens(content)
    assert refs == ["docs/stale.md"]


def test_coherent_token_dense_content_obeys_hard_injection_budget(tmp_path, monkeypatch):
    """Accurate metadata still cannot omit rendered header tokens from the budget."""
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    content = "needle " + "x " * 1000
    _write_index(tmp_path, {"b1": ("docs/coherent.md", content)})
    index = _load_index(tmp_path)

    injection, tokens_used, refs = index.compile_injection(
        "needle", budget=150, top_k=1, min_score=0
    )

    assert injection
    assert tokens_used == vault_bridge.count_tokens(injection)
    assert tokens_used <= 150
    assert refs == ["docs/coherent.md"]


def test_complete_stale_result_does_not_suppress_later_in_budget_result(tmp_path, monkeypatch):
    """A fully fitting stale block continues through the ranked result list."""
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    index_path = _write_index(
        tmp_path,
        {
            "b1": ("docs/a.md", "needle first context"),
            "b2": ("docs/b.md", "needle second context"),
        },
    )
    metadata = json.loads(index_path.read_text(encoding="utf-8"))
    metadata["blocks"]["b1"]["content_hash"] = "0" * 64
    index_path.write_text(json.dumps(metadata), encoding="utf-8")
    index = _load_index(tmp_path)

    injection, tokens_used, refs = index.compile_injection(
        "needle", budget=1000, top_k=2, min_score=0
    )

    assert refs == ["docs/a.md", "docs/b.md"]
    assert "needle first context" in injection
    assert "needle second context" in injection
    assert tokens_used == vault_bridge.count_tokens(injection)
    assert tokens_used <= 1000


def test_search_keeps_one_generation_across_reload(tmp_path, monkeypatch):
    """A reload cannot mix old IDs with new postings or pollute the new cache."""
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    index_path = _write_index(tmp_path, {"b1": ("docs/old.md", "alpha old_marker")})
    index = _load_index(tmp_path)
    old_generation = index._snapshot_generation()

    generation_captured = threading.Event()
    resume_search = threading.Event()
    original_snapshot = index._snapshot_generation
    results: list[list[tuple[dict, float]]] = []
    errors: list[BaseException] = []
    search_thread: threading.Thread

    def paused_snapshot():
        generation = original_snapshot()
        if threading.current_thread() is search_thread:
            generation_captured.set()
            if not resume_search.wait(timeout=5):
                raise TimeoutError("reload did not resume the paused search")
        return generation

    monkeypatch.setattr(index, "_snapshot_generation", paused_snapshot)

    def run_search() -> None:
        try:
            results.append(index.search("alpha", top_k=1, min_score=0))
        except BaseException as exc:
            errors.append(exc)

    search_thread = threading.Thread(target=run_search)
    search_thread.start()
    assert generation_captured.wait(timeout=5)

    _write_index(
        tmp_path,
        {
            "b2": ("docs/new-one.md", "beta new_marker"),
            "b3": ("docs/new-two.md", "beta second_marker"),
        },
    )
    index._load(index_path, index_path.stat().st_mtime)
    new_generation = original_snapshot()
    assert new_generation.generation_id == old_generation.generation_id + 1
    assert new_generation.block_ids == ("b2", "b3")

    resume_search.set()
    search_thread.join(timeout=5)

    assert not search_thread.is_alive()
    assert not errors
    assert results[0][0][0]["block_id"] == "b1"
    assert results[0][0][0]["content"] == "alpha old_marker"
    assert tuple(index._content_cache) == ()


def test_hydration_rejects_content_from_a_different_generation(tmp_path, monkeypatch):
    """Old BM25 scores never pair with bytes written after that generation."""
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    _write_index(tmp_path, {"b1": ("docs/item.md", "alpha old_marker")})
    index = _load_index(tmp_path)
    generation = index._snapshot_generation()

    block_path = tmp_path / "blocks" / "b1.txt"
    block_path.write_text("beta replacement_marker", encoding="utf-8")
    changed_mtime_ns = max(
        time.time_ns() + 2_000_000_000,
        (generation.content_records["b1"].mtime_ns or 0) + 2_000_000_000,
    )
    os.utime(block_path, ns=(changed_mtime_ns, changed_mtime_ns))

    results = index.search("alpha", top_k=1, min_score=0)

    assert results[0][0]["block_id"] == "b1"
    assert results[0][0]["content"] == vault_bridge._CONTENT_GENERATION_MISMATCH
    assert "replacement_marker" not in results[0][0]["content"]
    assert results[0][0]["content_bytes_total"] == len("alpha old_marker")
    assert results[0][0]["content_bytes_loaded"] == 0
    assert results[0][0]["content_bytes_loaded"] <= results[0][0]["content_bytes_total"]
    assert tuple(index._content_cache) == ()


def test_bounded_reader_stops_on_a_utf8_boundary(tmp_path, monkeypatch):
    """A byte cap inside a code point yields a valid prefix, never replacement bytes."""
    prefix = "needle "
    content = prefix + "€-tail"
    cap = len(prefix.encode("utf-8")) + 1
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_MAX_BYTES", cap)
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    _write_index(tmp_path, {"b1": ("docs/utf8.md", content)})
    index = _load_index(tmp_path)

    def forbidden_read_text(*args, **kwargs):
        raise AssertionError("hydration must use a bounded binary read")

    monkeypatch.setattr(Path, "read_text", forbidden_read_text)
    block = index.search("needle", top_k=1, min_score=0)[0][0]

    assert block["content"] == prefix
    assert "�" not in block["content"]
    assert len(block["content"].encode("utf-8")) <= cap
    assert block["content_truncated"] is True
    assert block["content_bytes_total"] == len(content.encode("utf-8"))
    assert block["content_bytes_loaded"] == len(prefix.encode("utf-8"))
    assert index._cache_bytes == block["content_bytes_loaded"]


def test_search_aggregate_content_is_capped_without_ranking_changes(tmp_path, monkeypatch):
    """Ranked IDs and scores survive rank-priority clipping across top-k."""
    documents = {
        "b1": ("docs/a.md", "needle " + "a" * 30),
        "b2": ("docs/b.md", "needle " + "b" * 30),
        "b3": ("docs/c.md", "needle " + "c" * 30),
    }
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    _write_index(tmp_path, documents)

    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_MAX_BYTES", 4096)
    high_cap = _load_index(tmp_path)
    high_results = high_cap.search("needle", top_k=3, min_score=0)

    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_MAX_BYTES", 50)
    low_cap = _load_index(tmp_path)
    low_results = low_cap.search("needle", top_k=3, min_score=0)

    assert [block["block_id"] for block, _ in low_results] == [
        block["block_id"] for block, _ in high_results
    ]
    assert [score for _, score in low_results] == pytest.approx(
        [score for _, score in high_results]
    )
    assert sum(len(block["content"].encode("utf-8")) for block, _ in low_results) == 50
    assert low_results[0][0]["content"] == documents["b1"][1]
    assert "content_truncated" not in low_results[0][0]
    assert low_results[1][0]["content"] == documents["b2"][1][:13]
    assert low_results[1][0]["content_bytes_loaded"] == 13
    assert low_results[2][0]["content"] == ""
    assert low_results[2][0]["content_bytes_loaded"] == 0
    assert low_cap.cache_stats["vault_cache_physical_reads"] == 2


def test_same_block_concurrent_miss_is_generation_singleflight(tmp_path, monkeypatch):
    """Concurrent misses share one physical read and one cache charge."""
    worker_count = 16
    content = "needle shared concurrent content"
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_MAX_BYTES", 256)
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    _write_index(tmp_path, {"b1": ("docs/shared.md", content)})
    index = _load_index(tmp_path)

    original_reader = index._read_pinned_prefix
    read_started = threading.Event()
    release_read = threading.Event()
    read_calls = 0
    read_lock = threading.Lock()

    def stalled_reader(record, byte_limit):
        nonlocal read_calls
        with read_lock:
            read_calls += 1
        read_started.set()
        if not release_read.wait(timeout=5):
            raise TimeoutError("singleflight read was not released")
        return original_reader(record, byte_limit)

    monkeypatch.setattr(index, "_read_pinned_prefix", stalled_reader)
    start = threading.Barrier(worker_count + 1)
    results: list[list[tuple[dict, float]]] = []
    errors: list[BaseException] = []

    def search_once() -> None:
        try:
            start.wait(timeout=5)
            results.append(index.search("needle", top_k=1, min_score=0))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=search_once) for _ in range(worker_count)]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    assert read_started.wait(timeout=5)
    _wait_until(lambda: index.cache_stats["vault_cache_coalesced_hydrations"] == worker_count - 1)
    release_read.set()
    for thread in threads:
        thread.join(timeout=5)

    assert not errors
    assert all(not thread.is_alive() for thread in threads)
    assert read_calls == 1
    assert len(results) == worker_count
    assert {result[0][0]["content"] for result in results} == {content}
    assert len({id(result[0][0]["content"]) for result in results}) == 1
    assert index._cache_bytes == len(content.encode("utf-8"))
    assert index.cache_stats["vault_cache_physical_reads"] == 1
    assert index._hydration_reserved_bytes == 0
    assert index._hydration_flights == {}


def test_distinct_concurrent_misses_respect_combined_managed_budget(tmp_path, monkeypatch):
    """Distinct physical reads serialize admission when reservations would exceed max."""
    cap = 80
    documents = {
        "b1": ("docs/one.md", "needle_one " + "a" * 55),
        "b2": ("docs/two.md", "needle_two " + "b" * 55),
    }
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_MAX_BYTES", cap)
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    _write_index(tmp_path, documents)
    index = _load_index(tmp_path)

    original_reader = index._read_pinned_prefix
    first_read_started = threading.Event()
    release_first_read = threading.Event()
    read_count = 0
    read_lock = threading.Lock()

    def sequenced_reader(record, byte_limit):
        nonlocal read_count
        with read_lock:
            read_count += 1
            ordinal = read_count
        if ordinal == 1:
            first_read_started.set()
            if not release_first_read.wait(timeout=5):
                raise TimeoutError("first distinct read was not released")
        return original_reader(record, byte_limit)

    monkeypatch.setattr(index, "_read_pinned_prefix", sequenced_reader)
    start = threading.Barrier(3)
    results: list[list[tuple[dict, float]]] = []

    def search_once(query: str) -> None:
        start.wait(timeout=5)
        results.append(index.search(query, top_k=1, min_score=0))

    threads = [
        threading.Thread(target=search_once, args=("needle_one",)),
        threading.Thread(target=search_once, args=("needle_two",)),
    ]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    assert first_read_started.wait(timeout=5)
    _wait_until(lambda: len(index._hydration_flights) == 2)
    assert index._cache_bytes + index._hydration_reserved_bytes <= cap
    release_first_read.set()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert read_count == 2
    assert len(results) == 2
    assert index._max_managed_content_bytes <= cap
    assert index._cache_bytes + index._hydration_reserved_bytes <= cap
    assert index._hydration_reserved_bytes == 0
    assert index._hydration_flights == {}


def test_singleflight_failure_wakes_waiters_and_next_call_retries(tmp_path, monkeypatch):
    """A failed owner always releases reservations and leaves the key retryable."""
    worker_count = 8
    content = "needle retry succeeds"
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_MAX_BYTES", 256)
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    _write_index(tmp_path, {"b1": ("docs/retry.md", content)})
    index = _load_index(tmp_path)

    original_reader = index._read_pinned_prefix
    failed_read_started = threading.Event()
    release_failure = threading.Event()
    read_calls = 0
    read_lock = threading.Lock()

    def flaky_reader(record, byte_limit):
        nonlocal read_calls
        with read_lock:
            read_calls += 1
            call = read_calls
        if call == 1:
            failed_read_started.set()
            if not release_failure.wait(timeout=5):
                raise TimeoutError("failed read was not released")
            raise OSError("synthetic hydration failure")
        return original_reader(record, byte_limit)

    monkeypatch.setattr(index, "_read_pinned_prefix", flaky_reader)
    start = threading.Barrier(worker_count + 1)
    results: list[list[tuple[dict, float]]] = []

    def search_once() -> None:
        start.wait(timeout=5)
        results.append(index.search("needle", top_k=1, min_score=0))

    threads = [threading.Thread(target=search_once) for _ in range(worker_count)]
    for thread in threads:
        thread.start()
    start.wait(timeout=5)
    assert failed_read_started.wait(timeout=5)
    _wait_until(lambda: index.cache_stats["vault_cache_coalesced_hydrations"] == worker_count - 1)
    release_failure.set()
    for thread in threads:
        thread.join(timeout=5)

    assert all(not thread.is_alive() for thread in threads)
    assert len(results) == worker_count
    assert all(
        result[0][0]["content"] == vault_bridge._CONTENT_GENERATION_MISMATCH for result in results
    )
    assert index._hydration_reserved_bytes == 0
    assert index._hydration_flights == {}
    assert index.cache_stats["vault_cache_hydration_failures"] == 1

    retry = index.search("needle", top_k=1, min_score=0)
    assert retry[0][0]["content"] == content
    assert read_calls == 2
    assert index.cache_stats["vault_cache_physical_reads"] == 2


def test_reload_during_hydration_never_publishes_old_generation(tmp_path, monkeypatch):
    """An old-generation owner may finish for its caller but cannot enter the new cache."""
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_MAX_BYTES", 256)
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    index_path = _write_index(tmp_path, {"b1": ("docs/old.md", "alpha old_marker")})
    index = _load_index(tmp_path)
    old_generation = index._snapshot_generation()

    original_reader = index._read_pinned_prefix
    read_started = threading.Event()
    release_read = threading.Event()

    def stalled_reader(record, byte_limit):
        read_started.set()
        if not release_read.wait(timeout=5):
            raise TimeoutError("old hydration was not released")
        return original_reader(record, byte_limit)

    monkeypatch.setattr(index, "_read_pinned_prefix", stalled_reader)
    results: list[list[tuple[dict, float]]] = []
    search_thread = threading.Thread(
        target=lambda: results.append(index.search("alpha", top_k=1, min_score=0))
    )
    search_thread.start()
    assert read_started.wait(timeout=5)

    _write_index(tmp_path, {"b2": ("docs/new.md", "beta new_marker")})
    index._load(index_path, index_path.stat().st_mtime)
    new_generation = index._snapshot_generation()
    assert new_generation.generation_id == old_generation.generation_id + 1
    release_read.set()
    search_thread.join(timeout=5)

    assert not search_thread.is_alive()
    assert results[0][0][0]["content"] == "alpha old_marker"
    assert all(key[0] == new_generation.generation_id for key in index._content_cache)
    assert index._hydration_reserved_bytes == 0
    assert index._hydration_flights == {}


def test_zero_cap_returns_rank_only_without_physical_reads(tmp_path, monkeypatch):
    """A zero managed-content budget preserves ranking and performs no hydration I/O."""
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_MAX_BYTES", 0)
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    content = "needle zero-cap content"
    _write_index(tmp_path, {"b1": ("docs/zero.md", content)})
    index = _load_index(tmp_path)

    results = index.search("needle", top_k=1, min_score=0)

    assert results[0][0]["block_id"] == "b1"
    assert results[0][0]["content"] == ""
    assert results[0][0]["content_truncated"] is True
    assert results[0][0]["content_bytes_total"] == len(content.encode("utf-8"))
    assert results[0][0]["content_bytes_loaded"] == 0
    assert index.cache_stats["vault_cache_physical_reads"] == 0
    assert index._cache_bytes == 0
    assert index._hydration_reserved_bytes == 0
    assert index.compile_injection("needle", top_k=1, min_score=0) == ("", 0, [])


def test_truncated_injection_counts_loaded_content_not_full_document(tmp_path, monkeypatch):
    """Full-document raw_tokens cannot suppress a useful bounded prefix."""
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_MAX_BYTES", 32)
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    content = "needle usable bounded context " + "x" * 200
    index_path = _write_index(tmp_path, {"b1": ("docs/injection.md", content)})
    metadata = json.loads(index_path.read_text(encoding="utf-8"))
    metadata["blocks"]["b1"]["raw_tokens"] = 100_000
    index_path.write_text(json.dumps(metadata), encoding="utf-8")
    index = _load_index(tmp_path)

    injection, tokens_used, source_refs = index.compile_injection(
        "needle", budget=150, top_k=1, min_score=0
    )

    assert "needle usable bounded context" in injection
    assert tokens_used == vault_bridge.count_tokens(injection)
    assert source_refs == ["docs/injection.md"]


def _deep_size(value: Any, seen: set[int] | None = None) -> int:
    if seen is None:
        seen = set()
    value_id = id(value)
    if value_id in seen:
        return 0
    seen.add(value_id)

    size = sys.getsizeof(value)
    if isinstance(value, dict):
        size += sum(_deep_size(key, seen) + _deep_size(item, seen) for key, item in value.items())
    elif isinstance(value, (list, tuple, set, frozenset)):
        size += sum(_deep_size(item, seen) for item in value)
    elif is_dataclass(value):
        size += sum(_deep_size(getattr(value, field.name), seen) for field in fields(value))
    return size


def _legacy_state(documents: dict[str, tuple[str, str]]) -> tuple[Any, ...]:
    blocks = {}
    document_frequencies: dict[str, int] = {}
    block_term_frequencies: dict[str, dict[str, int]] = {}
    block_lengths = {}
    inverted: dict[str, set[str]] = {}

    for block_id, (source_path, content) in documents.items():
        blocks[block_id] = {
            "block_id": block_id,
            "source_path": source_path,
            "raw_tokens": len(content.split()),
            "content": content,
        }
        terms = vault_bridge._bm25_tokenize.__wrapped__(content)
        term_frequencies: dict[str, int] = {}
        for term in terms:
            term_frequencies[term] = term_frequencies.get(term, 0) + 1
        block_term_frequencies[block_id] = term_frequencies
        block_lengths[block_id] = len(terms)
        for term in set(terms):
            document_frequencies[term] = document_frequencies.get(term, 0) + 1
        for term in term_frequencies:
            inverted.setdefault(term, set()).add(block_id)

    return blocks, document_frequencies, block_term_frequencies, block_lengths, inverted


def test_compact_state_is_materially_smaller_than_legacy_shape(tmp_path, monkeypatch):
    """Regression: the resident BM25 shape stays compact on repeated-term corpora."""
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    common_terms = [f"common_{term}" for term in range(80)]
    documents = {}
    for doc_index in range(200):
        unique_terms = [f"unique_{doc_index}_{term}" for term in range(40)]
        content = " ".join(common_terms + unique_terms)
        documents[f"b{doc_index:04d}"] = (f"docs/{doc_index:04d}.md", content)

    _write_index(tmp_path, documents)
    index = _load_index(tmp_path)
    compact_bytes = _deep_size(
        (
            index.blocks,
            index._snapshot_generation().content_records,
            index._block_ids,
            index._postings,
            index._block_dl,
            index._content_cache,
        )
    )
    legacy_bytes = _deep_size(_legacy_state(documents))
    ratio = compact_bytes / legacy_bytes

    print(
        f"memory-shape compact_bytes={compact_bytes} legacy_bytes={legacy_bytes} ratio={ratio:.3f}"
    )
    assert not hasattr(index, "_block_tfs")
    assert not hasattr(index, "_inverted")
    assert all(posting.typecode == "Q" for posting in index._postings.values())
    assert ratio < 0.50
