"""Atomic-write race tests (AC-6 from P1-VAULT-INDEX-ATOMIC-WRITE-HARDENING-2026-05-22).

Covers the three acceptance tests from the packet, with names that match
the packet text exactly:

  T6.1 → test_atomic_index_json_write
  T6.2 → test_atomic_block_file_write
  T6.3 → test_atomic_write_negative_control

T6.3 is a teeth-check: it runs the same race against a deliberately
non-atomic writer with an injected mid-write sleep, and asserts the
harness observes at least one partial. If T6.3 ever passed silently
against the real ``_atomic_write``, T6.1/T6.2 could be passing vacuously.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable, List, Tuple

from tokenpak.vault._atomic import _atomic_write

WriterFn = Callable[[Path, str], None]
Observation = Tuple[str, str]


def _run_race(
    writer_func: WriterFn,
    target: Path,
    payload_a: str,
    payload_b: str,
    iterations: int = 1000,
) -> List[Observation]:
    """Spawn a writer + reader thread; return the reader's observations.

    Writer alternates A and B for ``iterations`` rounds, then signals stop.
    Reader loops as tight as it can, recording every text read. The file is
    seeded with ``payload_a`` before the threads start, so the reader never
    needs to handle a pre-first-write window.
    """
    observations: List[Observation] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                content = target.read_text(encoding="utf-8", errors="replace")
                observations.append(("ok", content))
            except FileNotFoundError:
                # Should not happen after the seed write, but be defensive.
                pass
            except OSError as exc:
                observations.append(("oserr", str(exc)))

    def writer() -> None:
        for i in range(iterations):
            writer_func(target, payload_a if i % 2 == 0 else payload_b)
        stop.set()

    # Seed before starting reader so it doesn't see a missing file.
    writer_func(target, payload_a)

    r = threading.Thread(target=reader, name="atomic-test-reader", daemon=True)
    w = threading.Thread(target=writer, name="atomic-test-writer", daemon=True)
    r.start()
    w.start()
    w.join(timeout=60)
    stop.set()
    r.join(timeout=10)

    return observations


def _non_atomic_write_with_pause(target: Path, content: str) -> None:
    """Pre-S1 ``write_text`` behavior + a 1ms mid-write pause.

    Open-truncates the target, writes the first half, sleeps to widen the
    partial-write window, then writes the rest. Used only by T6.3 to prove
    the test harness would catch a regression to the non-atomic path.
    """
    target = Path(target)
    half = len(content) // 2
    with open(target, "w", encoding="utf-8") as f:
        f.write(content[:half])
        f.flush()
        time.sleep(0.001)
        f.write(content[half:])


# ---------------- T6.1 ----------------


def test_atomic_index_json_write(tmp_path: Path) -> None:
    index_path = tmp_path / "index.json"
    payload_a = json.dumps({"v": "A", "blocks": {"k": "x" * 4096}})
    payload_b = json.dumps({"v": "B", "blocks": {"k": "y" * 4096}})

    observations = _run_race(
        lambda t, c: _atomic_write(t, c),
        index_path,
        payload_a,
        payload_b,
    )
    assert observations, "reader recorded no observations"

    seen_values: set[str] = set()
    for kind, payload in observations:
        assert kind == "ok", f"reader hit non-ok branch: {kind}={payload!r}"
        parsed = json.loads(payload)
        assert parsed["v"] in {"A", "B"}, f"unexpected parsed value: {parsed!r}"
        seen_values.add(parsed["v"])

    # Race-quality sanity check: 1000 alternations should reach both values.
    assert seen_values == {"A", "B"}, (
        f"expected reader to observe both A and B, got {seen_values}"
    )


# ---------------- T6.2 ----------------


def test_atomic_block_file_write(tmp_path: Path) -> None:
    block_file = tmp_path / "block_abc.txt"
    payload_a = "A" * 8192
    payload_b = "B" * 8192

    observations = _run_race(
        lambda t, c: _atomic_write(t, c),
        block_file,
        payload_a,
        payload_b,
    )
    assert observations, "reader recorded no observations"

    seen_values: set[str] = set()
    for kind, payload in observations:
        assert kind == "ok", f"reader hit non-ok branch: {kind}={payload!r}"
        assert payload in {payload_a, payload_b}, (
            f"partial observed: len={len(payload)}, head={payload[:16]!r}"
        )
        seen_values.add(payload[:1])

    assert seen_values == {"A", "B"}, (
        f"expected reader to observe both A and B, got {seen_values}"
    )


# ---------------- T6.3 ----------------


def test_atomic_write_negative_control(tmp_path: Path) -> None:
    """Prove the T6.1/T6.2 harness would catch a regression.

    Reuses the same race against a deliberately non-atomic writer with an
    injected mid-write sleep. The reader MUST observe at least one partial
    (or non-A/non-B content); otherwise the atomic tests are vacuous.
    """
    block_file = tmp_path / "block_neg.txt"
    payload_a = "A" * 8192
    payload_b = "B" * 8192

    observations = _run_race(
        _non_atomic_write_with_pause,
        block_file,
        payload_a,
        payload_b,
    )
    assert observations, "reader recorded no observations"

    bad = [
        payload
        for kind, payload in observations
        if kind != "ok" or payload not in {payload_a, payload_b}
    ]
    assert bad, (
        "expected at least one partial observation from the non-atomic "
        "writer; the harness would not catch a regression to the old "
        "write_text path"
    )


# ---------------- converted-site coverage ----------------


def test_atomic_blockstore_flush_race(tmp_path: Path) -> None:
    """Same writer/reader race, driven through a converted production site.

    ``BlockStore.flush`` used to publish its JSON store via a plain
    ``write_text`` (open-truncate-write), so a concurrent reader could
    observe a torn file. After routing it through ``_atomic_write``, every
    observation must be complete, parseable JSON containing one of the two
    alternating payloads.
    """
    from tokenpak.vault.blocks import BlockRecord, BlockStore

    store_path = tmp_path / "blocks.json"
    store = BlockStore(str(store_path))
    payload_a = "A" * 4096
    payload_b = "B" * 4096

    def writer_func(target: Path, content: str) -> None:
        store.save(
            BlockRecord(
                block_id="race-block",
                path="src/example.py",
                content_hash="deadbeef",
                file_type="text",
                raw_tokens=1024,
                compressed_tokens=512,
                compressed_content=content,
            )
        )  # save() flushes to disk when the store is file-backed

    observations = _run_race(
        writer_func, store_path, payload_a, payload_b, iterations=400
    )
    assert observations, "reader recorded no observations"

    seen_values: set[str] = set()
    for kind, payload in observations:
        assert kind == "ok", f"reader hit non-ok branch: {kind}={payload!r}"
        parsed = json.loads(payload)  # torn write -> JSONDecodeError
        content = parsed["race-block"]["compressed_content"]
        assert content in {payload_a, payload_b}, (
            f"partial content observed: len={len(content)}"
        )
        seen_values.add(content[:1])

    assert seen_values == {"A", "B"}, (
        f"expected reader to observe both A and B, got {seen_values}"
    )
