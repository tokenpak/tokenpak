"""
Chaos tests for vault-index corruption handling.

Exercises the failure paths inside ``tokenpak.proxy.vault_bridge.VaultIndex._load()``:
a corrupt/unparseable ``index.json`` must never fail silently (a bare stdout
``print()``). It must emit an explicit, actionable degradation event and must
never crash the proxy — in either of two distinct situations:

  * Cold start: no generation has ever loaded successfully. The index starts
    up empty (nothing to fall back on) and the event carries the
    ``cold_start_empty`` variant.
  * Warm reload: a previous generation is already loaded and serving. A later
    reload hits corruption (e.g. a git pull landing mid-write). The prior
    in-memory generation must keep being served, unchanged, and the event
    carries the ``warm_reload_stale`` variant.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from tokenpak.proxy import vault_bridge
from tokenpak.proxy.degradation import DegradationEventType, DegradationTracker


def _write_block(root: Path, block_id: str, content: str) -> str:
    """Write a content block file and return its expected content hash."""
    blocks_dir = root / "blocks"
    blocks_dir.mkdir(parents=True, exist_ok=True)
    (blocks_dir / f"{block_id}.txt").write_text(content, encoding="utf-8")
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _write_valid_index(root: Path) -> Path:
    """Seed a small, valid index.json + backing block so a load succeeds."""
    content = "hello vault world " * 20
    content_hash = _write_block(root, "b1", content)
    index_path = root / "index.json"
    index_path.write_text(
        json.dumps(
            {
                "blocks": {
                    "b1": {
                        "source_path": "b1.md",
                        "content_hash": content_hash,
                        "raw_tokens": len(content.split()),
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return index_path


def _corrupt(index_path: Path) -> None:
    """Overwrite index.json with unparseable content, simulating a torn write."""
    index_path.write_text("{this is not valid json at all", encoding="utf-8")


@pytest.fixture
def fresh_tracker(monkeypatch):
    """Isolate degradation-tracker state so assertions are exact, not additive
    against whatever the process-wide singleton has already accumulated.

    ``vault_bridge._record_vault_index_load_failure`` resolves
    ``get_degradation_tracker`` via a lazy ``from ... import`` at call time, so
    patching the attribute on the defining module is picked up on the next call.
    """
    tracker = DegradationTracker()
    monkeypatch.setattr("tokenpak.proxy.degradation.get_degradation_tracker", lambda: tracker)
    return tracker


def _vault_events(tracker: DegradationTracker):
    return [
        e
        for e in tracker.get_recent(limit=50)
        if e["event_type"] == DegradationEventType.VAULT_INDEX_STALE
    ]


@pytest.mark.chaos
class TestVaultIndexCorruptionColdStart:
    """No prior generation exists; a corrupt index.json must not crash startup."""

    def test_cold_start_corrupt_index_emits_event_and_stays_empty(self, tmp_path, fresh_tracker):
        root = tmp_path / ".tokenpak"
        root.mkdir()
        (root / "blocks").mkdir()
        index_path = root / "index.json"
        _corrupt(index_path)

        index = vault_bridge.VaultIndex(str(root))
        # Must not raise.
        index._load(index_path, index_path.stat().st_mtime)

        assert index.available is False
        assert index._doc_count == 0
        assert index._snapshot_generation().generation_id == 0

        events = _vault_events(fresh_tracker)
        assert len(events) == 1
        assert "cold_start_empty" in events[0]["detail"]
        # Cold start: nothing to fall back on yet — not a "recovered" state.
        assert events[0]["recovered"] is False

        summary = fresh_tracker.summary()
        assert summary["lifetime_vault_index_load_failures"] == 1

    def test_cold_start_via_maybe_reload_does_not_crash(self, tmp_path, fresh_tracker):
        """maybe_reload() is the real entrypoint hit at proxy startup/timer fire."""
        root = tmp_path / ".tokenpak"
        root.mkdir()
        (root / "blocks").mkdir()
        _corrupt(root / "index.json")

        index = vault_bridge.VaultIndex(str(root))
        index.maybe_reload()  # must not raise

        assert index.available is False
        events = _vault_events(fresh_tracker)
        assert len(events) == 1
        assert "cold_start_empty" in events[0]["detail"]

    def test_cold_start_missing_blocks_field_emits_event(self, tmp_path, fresh_tracker):
        """A structurally-valid-JSON but semantically-wrong index (e.g. 'blocks'
        is not a dict) must also be signaled, not silently swallowed."""
        root = tmp_path / ".tokenpak"
        root.mkdir()
        (root / "blocks").mkdir()
        index_path = root / "index.json"
        index_path.write_text(json.dumps({"blocks": "not-a-dict"}), encoding="utf-8")

        index = vault_bridge.VaultIndex(str(root))
        index._load(index_path, index_path.stat().st_mtime)

        assert index.available is False
        events = _vault_events(fresh_tracker)
        assert len(events) == 1
        assert "cold_start_empty" in events[0]["detail"]
        assert "invalid_blocks_field" in events[0]["detail"]


@pytest.mark.chaos
class TestVaultIndexCorruptionWarmReload:
    """A prior generation is already loaded; a later corrupt reload must keep
    serving the last-good in-memory generation rather than wiping it."""

    def test_warm_reload_corruption_keeps_serving_prior_index(self, tmp_path, fresh_tracker):
        root = tmp_path / ".tokenpak"
        root.mkdir()
        index_path = _write_valid_index(root)

        index = vault_bridge.VaultIndex(str(root))
        index._load(index_path, index_path.stat().st_mtime)

        assert index.available is True
        assert index._doc_count == 1
        first_generation_id = index._snapshot_generation().generation_id
        assert first_generation_id > 0

        # Simulate corruption landing mid-run (e.g. a torn write from a
        # concurrent reindex), then hit the same load path again.
        _corrupt(index_path)
        index._load(index_path, index_path.stat().st_mtime)

        # The prior generation must still be being served, unchanged.
        assert index.available is True
        assert index._doc_count == 1
        assert index._snapshot_generation().generation_id == first_generation_id

        events = _vault_events(fresh_tracker)
        assert len(events) == 1
        assert "warm_reload_stale" in events[0]["detail"]
        # Warm reload: a prior good generation is still being served — graceful.
        assert events[0]["recovered"] is True

        summary = fresh_tracker.summary()
        assert summary["lifetime_vault_index_load_failures"] == 1

    def test_warm_reload_corruption_via_maybe_reload_does_not_crash(self, tmp_path, fresh_tracker):
        root = tmp_path / ".tokenpak"
        root.mkdir()
        index_path = _write_valid_index(root)

        index = vault_bridge.VaultIndex(str(root))
        index.maybe_reload()
        assert index.available is True
        first_generation_id = index._snapshot_generation().generation_id
        prior_mtime = index_path.stat().st_mtime

        _corrupt(index_path)
        # maybe_reload() no-ops if index.json's mtime looks unchanged since the
        # last published generation — force it to look distinctly newer so the
        # reload path is actually re-entered regardless of filesystem mtime
        # resolution (mirrors a real edit landing sometime after the last load).
        os.utime(index_path, (prior_mtime + 5, prior_mtime + 5))
        # Force past the reload-interval gate too — mirrors a periodic
        # VAULT_INDEX_RELOAD_INTERVAL timer tick hitting corruption between
        # successful loads.
        index._last_loaded = 0
        index.maybe_reload()  # must not raise

        assert index.available is True
        assert index._snapshot_generation().generation_id == first_generation_id

        events = _vault_events(fresh_tracker)
        assert len(events) == 1
        assert "warm_reload_stale" in events[0]["detail"]
