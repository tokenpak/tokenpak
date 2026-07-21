"""Regression coverage for post-publication vault reload memory return."""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import subprocess
import sys
import textwrap
import time
import weakref
from pathlib import Path

import pytest

from tokenpak.proxy import vault_bridge


def _write_generation(root: Path, generation: int) -> tuple[Path, dict[str, str]]:
    blocks_dir = root / "blocks"
    blocks_dir.mkdir(exist_ok=True)
    contents = {
        f"g{generation}-primary": f"needle needle primary marker{generation}",
        f"g{generation}-secondary": f"needle secondary secondary marker{generation}",
    }
    metadata: dict[str, dict[str, object]] = {}
    for block_id, content in contents.items():
        content_path = blocks_dir / f"{block_id}.txt"
        content_path.write_text(content, encoding="utf-8")
        metadata[block_id] = {
            "source_path": f"docs/{block_id}.md",
            "content_hash": hashlib.sha256(content.encode()).hexdigest(),
            "raw_tokens": len(content.split()),
        }

    index_path = root / "index.json"
    index_path.write_text(json.dumps({"blocks": metadata}), encoding="utf-8")
    stamp = time.time() + generation + 1
    os.utime(index_path, (stamp, stamp))
    return index_path, contents


def test_reload_returns_memory_only_after_successful_publication(tmp_path, monkeypatch):
    """Regression: collection follows publication, not failed or unchanged reload attempts."""
    monkeypatch.setattr(vault_bridge, "VAULT_INDEX_RELOAD_INTERVAL", 0)
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    index_path, _ = _write_generation(tmp_path, 1)
    index = vault_bridge.VaultIndex(str(tmp_path))
    old_generation_ref = weakref.ref(index._snapshot_generation())
    release_observations: list[bool] = []

    def record_release() -> None:
        release_observations.append(old_generation_ref() is None)

    monkeypatch.setattr(vault_bridge, "_return_released_memory_to_os", record_release)
    index.maybe_reload()

    assert index._snapshot_generation().generation_id == 1
    assert release_observations == [True]

    index.maybe_reload()
    assert release_observations == [True]

    index_path.write_text("{not-json", encoding="utf-8")
    failed_stamp = time.time() + 20
    os.utime(index_path, (failed_stamp, failed_stamp))
    index.maybe_reload()

    assert index._snapshot_generation().generation_id == 1
    assert release_observations == [True]


def test_memory_return_uses_glibc_trim_after_collection(monkeypatch):
    """Linux/glibc returns allocator pages only after Python collection."""
    events: list[object] = []

    class FakeTrim:
        argtypes: object = None
        restype: object = None

        def __call__(self, pad: int) -> int:
            events.append(("trim", pad))
            return 1

    class FakeLibc:
        malloc_trim = FakeTrim()

    monkeypatch.setattr(vault_bridge.gc, "collect", lambda: events.append("collect"))
    monkeypatch.setattr(vault_bridge.sys, "platform", "linux")
    monkeypatch.setattr(vault_bridge.platform, "libc_ver", lambda: ("glibc", "2.39"))
    monkeypatch.setattr(vault_bridge.ctypes, "CDLL", lambda _name: FakeLibc())

    vault_bridge._return_released_memory_to_os()

    assert events == ["collect", ("trim", 0)]
    assert FakeLibc.malloc_trim.argtypes == [vault_bridge.ctypes.c_size_t]
    assert FakeLibc.malloc_trim.restype is vault_bridge.ctypes.c_int


@pytest.mark.parametrize(
    ("system_platform", "libc_name"),
    [("darwin", "glibc"), ("linux", "musl")],
)
def test_memory_return_is_no_fail_without_linux_glibc(
    monkeypatch, system_platform: str, libc_name: str
):
    """Unsupported allocators still collect Python objects without loading libc."""
    events: list[str] = []

    def unexpected_cdll(_name):
        raise AssertionError("malloc_trim must stay platform-gated")

    monkeypatch.setattr(vault_bridge.gc, "collect", lambda: events.append("collect"))
    monkeypatch.setattr(vault_bridge.sys, "platform", system_platform)
    monkeypatch.setattr(vault_bridge.platform, "libc_ver", lambda: (libc_name, "1.0"))
    monkeypatch.setattr(vault_bridge.ctypes, "CDLL", unexpected_cdll)

    vault_bridge._return_released_memory_to_os()

    assert events == ["collect"]


def test_memory_return_is_no_fail_when_glibc_trim_is_unavailable(monkeypatch):
    """A Linux runtime without an exported malloc_trim remains a supported no-op."""
    monkeypatch.setattr(vault_bridge.gc, "collect", lambda: 0)
    monkeypatch.setattr(vault_bridge.sys, "platform", "linux")
    monkeypatch.setattr(vault_bridge.platform, "libc_ver", lambda: ("glibc", "2.39"))

    def unavailable_cdll(_name):
        raise OSError("symbol unavailable")

    monkeypatch.setattr(vault_bridge.ctypes, "CDLL", unavailable_cdll)

    vault_bridge._return_released_memory_to_os()


def test_six_generations_preserve_search_cache_and_injection_bytes(tmp_path, monkeypatch):
    """Six publications invalidate old cache entries without changing retrieval behavior."""
    monkeypatch.setattr(vault_bridge, "VAULT_INDEX_RELOAD_INTERVAL", 0)
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_MAX_BYTES", 512)
    monkeypatch.setattr(vault_bridge, "_VAULT_CACHE_PRELOAD", 0)
    index = vault_bridge.VaultIndex(str(tmp_path))
    idf = math.log(1.2)
    expected_scores = [idf * 5 / 3.5, idf]

    for generation in range(1, 7):
        previous_cache = index._content_cache
        _, contents = _write_generation(tmp_path, generation)
        index.maybe_reload()
        snapshot = index._snapshot_generation()
        expected_ids = (f"g{generation}-primary", f"g{generation}-secondary")

        assert snapshot.generation_id == generation
        assert snapshot.block_ids == expected_ids
        assert index._content_cache is not previous_cache
        assert tuple(index._content_cache) == ()

        results = index.search("needle", top_k=2, min_score=0)
        assert [block["block_id"] for block, _score in results] == list(expected_ids)
        assert [score for _block, score in results] == pytest.approx(expected_scores, rel=1e-12)
        assert [block["content"] for block, _score in results] == [
            contents[block_id] for block_id in expected_ids
        ]

        injection, tokens_used, refs = index.compile_injection(
            "needle", budget=1000, top_k=2, min_score=0
        )
        expected_injection = (
            "\n\n## Retrieved Context\n"
            f"--- [docs/{expected_ids[0]}.md] (relevance: 0.3) ---\n"
            f"{contents[expected_ids[0]]}\n\n"
            f"--- [docs/{expected_ids[1]}.md] (relevance: 0.2) ---\n"
            f"{contents[expected_ids[1]]}"
        )
        expected_cache_bytes = sum(len(content.encode()) for content in contents.values())

        assert injection.encode() == expected_injection.encode()
        assert tokens_used == vault_bridge.count_tokens(expected_injection)
        assert refs == [f"docs/{block_id}.md" for block_id in expected_ids]
        assert index._cache_bytes == expected_cache_bytes
        assert index._cache_bytes <= index._max_cache_bytes == 512
        assert all(key[0] == generation for key in index._content_cache)


@pytest.mark.skipif(
    sys.platform != "linux" or platform.libc_ver()[0].lower() != "glibc",
    reason="RSS return assertion requires Linux /proc and glibc malloc_trim",
)
def test_linux_glibc_reload_rss_has_no_positive_ratchet():
    """Six measured reloads stay inside bounds frozen from no-change controls."""
    script = textwrap.dedent(
        """
        import gc
        import hashlib
        import json
        import os
        import tempfile
        import time
        import tracemalloc
        from pathlib import Path

        from tokenpak.proxy import vault_bridge

        CONTROL_RUNS = 3
        TREATMENT_RELOADS = 6
        # Fixed before treatment: /proc RSS is page-accounted and allocator
        # scheduling can move a few MiB without retained Python objects.
        RSS_FIXED_MARGIN_KIB = 4 * 1024
        # Fixed before treatment: tracemalloc bookkeeping and retained evidence
        # records can vary slightly even when the indexed generation is unchanged.
        TRACED_FIXED_MARGIN_BYTES = 512 * 1024

        def rss_kib():
            for line in Path("/proc/self/status").read_text().splitlines():
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
            raise RuntimeError("VmRSS missing from /proc/self/status")

        def sample():
            rss = rss_kib()
            traced_current, traced_peak = tracemalloc.get_traced_memory()
            return {
                "rss_kib": rss,
                "traced_current_bytes": traced_current,
                "traced_peak_bytes": traced_peak,
            }

        def settle_and_sample():
            gc.collect()
            time.sleep(0.01)
            return sample()

        def jitter(values):
            return max(values) - min(values)

        with tempfile.TemporaryDirectory() as raw_root:
            root = Path(raw_root)
            blocks_dir = root / "blocks"
            blocks_dir.mkdir()
            metadata = {}
            fixture_hasher = hashlib.sha256()
            for block_number in range(64):
                block_id = f"block-{block_number}"
                content = " ".join(
                    f"term_{block_number}_{term_number}" for term_number in range(500)
                )
                content_bytes = content.encode()
                (blocks_dir / f"{block_id}.txt").write_bytes(content_bytes)
                fixture_hasher.update(block_id.encode())
                fixture_hasher.update(b"\\0")
                fixture_hasher.update(content_bytes)
                metadata[block_id] = {
                    "source_path": f"docs/{block_id}.md",
                    "content_hash": hashlib.sha256(content_bytes).hexdigest(),
                    "raw_tokens": 500,
                }

            index_path = root / "index.json"
            index_path.write_text(json.dumps({"blocks": metadata}), encoding="utf-8")
            vault_bridge.VAULT_INDEX_RELOAD_INTERVAL = 0
            vault_bridge._VAULT_CACHE_MAX_BYTES = 0
            vault_bridge._VAULT_CACHE_PRELOAD = 0
            index = vault_bridge.VaultIndex(str(root))

            tracemalloc.start()
            before_load = sample()
            tracemalloc.reset_peak()
            warmup_stamp = time.time() + 10
            os.utime(index_path, (warmup_stamp, warmup_stamp))
            index.maybe_reload()
            warmup = {
                "generation_id": index._snapshot_generation().generation_id,
                "after_reload": sample(),
                "settled": settle_and_sample(),
            }

            controls = []
            for control_number in range(1, CONTROL_RUNS + 1):
                # Force the loader while keeping every fixture byte unchanged,
                # so controls measure reload allocator jitter rather than idle noise.
                control_stamp = time.time() + control_number + 20
                os.utime(index_path, (control_stamp, control_stamp))
                tracemalloc.reset_peak()
                index.maybe_reload()
                controls.append(
                    {
                        "generation_id": index._snapshot_generation().generation_id,
                        "after_no_change_reload": sample(),
                        "settled": settle_and_sample(),
                    }
                )

            control_rss = [entry["settled"]["rss_kib"] for entry in controls]
            control_traced = [
                entry["settled"]["traced_current_bytes"] for entry in controls
            ]
            # Freeze both tolerances before any treatment reload is observed.
            frozen_bounds = {
                "rss_control_jitter_kib": jitter(control_rss),
                "rss_fixed_margin_kib": RSS_FIXED_MARGIN_KIB,
                "rss_no_ratchet_bound_kib": jitter(control_rss) + RSS_FIXED_MARGIN_KIB,
                "traced_control_jitter_bytes": jitter(control_traced),
                "traced_fixed_margin_bytes": TRACED_FIXED_MARGIN_BYTES,
                "traced_no_ratchet_bound_bytes": (
                    jitter(control_traced) + TRACED_FIXED_MARGIN_BYTES
                ),
            }

            treatments = []
            for treatment_number in range(1, TREATMENT_RELOADS + 1):
                stamp = time.time() + treatment_number + 40
                os.utime(index_path, (stamp, stamp))
                tracemalloc.reset_peak()
                index.maybe_reload()
                treatments.append(
                    {
                        "generation_id": index._snapshot_generation().generation_id,
                        "after_reload": sample(),
                        "settled": settle_and_sample(),
                    }
                )

            print(
                json.dumps(
                    {
                        "fixture_sha256": fixture_hasher.hexdigest(),
                        "before_load": before_load,
                        "warmup": warmup,
                        "controls": controls,
                        "frozen_bounds": frozen_bounds,
                        "treatments": treatments,
                    }
                )
            )
        """
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        env={**os.environ, "PYTHONHASHSEED": "0"},
        text=True,
        timeout=25,
    )
    payload = json.loads(completed.stdout.strip().splitlines()[-1])
    controls = payload["controls"]
    bounds = payload["frozen_bounds"]
    treatments = payload["treatments"]
    control_rss = [entry["settled"]["rss_kib"] for entry in controls]
    control_traced = [entry["settled"]["traced_current_bytes"] for entry in controls]
    treatment_rss = [entry["settled"]["rss_kib"] for entry in treatments]
    treatment_traced = [entry["settled"]["traced_current_bytes"] for entry in treatments]

    assert payload["fixture_sha256"] == (
        "d926d9af61c17509e771d0bd202f07e3bc1062099178a9db316bdb7e83c1dcd8"
    )
    assert payload["warmup"]["generation_id"] == 1
    assert [entry["generation_id"] for entry in controls] == list(range(2, 5))
    assert [entry["generation_id"] for entry in treatments] == list(range(5, 11))
    assert bounds["rss_control_jitter_kib"] == max(control_rss) - min(control_rss)
    assert bounds["rss_fixed_margin_kib"] == 4 * 1024
    assert bounds["rss_no_ratchet_bound_kib"] == (
        bounds["rss_control_jitter_kib"] + bounds["rss_fixed_margin_kib"]
    )
    assert bounds["traced_control_jitter_bytes"] == max(control_traced) - min(control_traced)
    assert bounds["traced_fixed_margin_bytes"] == 512 * 1024
    assert bounds["traced_no_ratchet_bound_bytes"] == (
        bounds["traced_control_jitter_bytes"] + bounds["traced_fixed_margin_bytes"]
    )
    assert treatment_rss[-1] <= treatment_rss[0] + bounds["rss_no_ratchet_bound_kib"]
    assert max(treatment_rss) - min(treatment_rss) <= bounds["rss_no_ratchet_bound_kib"]
    assert treatment_traced[-1] <= (treatment_traced[0] + bounds["traced_no_ratchet_bound_bytes"])
    assert max(treatment_traced) - min(treatment_traced) <= bounds["traced_no_ratchet_bound_bytes"]
