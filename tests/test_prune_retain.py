"""Tests for /tokenpak prune and retain commands.

Covers:
1. Prune identifies correct low-priority candidates
2. --dry-run doesn't modify state
3. Retain pins persist across compression runs
4. Retained blocks never appear in prune candidates
5. --auto respects threshold
6. --list shows all pins
"""

from __future__ import annotations

import json
import sys
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def block_store(tmp_path):
    """Create a temporary block store with test blocks."""
    store_path = tmp_path / "vault_index.json"
    blocks = {
        "file_a.py#aaa111": {
            "block_id": "file_a.py#aaa111",
            "path": "file_a.py",
            "quality_score": 0.1,  # LOW — should be pruned
            "raw_tokens": 1000,
            "compressed_tokens": 600,
            "tokens_saved": 400,
            "content_hash": "abc",
            "file_type": "code",
            "compressed_content": "...",
            "indexed_at": 1.0,
            "metadata": {},
        },
        "file_b.py#bbb222": {
            "block_id": "file_b.py#bbb222",
            "path": "file_b.py",
            "quality_score": 0.2,  # LOW — should be pruned
            "raw_tokens": 500,
            "compressed_tokens": 300,
            "tokens_saved": 200,
            "content_hash": "def",
            "file_type": "code",
            "compressed_content": "...",
            "indexed_at": 2.0,
            "metadata": {},
        },
        "file_c.py#ccc333": {
            "block_id": "file_c.py#ccc333",
            "path": "file_c.py",
            "quality_score": 0.8,  # HIGH — should be kept
            "raw_tokens": 800,
            "compressed_tokens": 400,
            "tokens_saved": 400,
            "content_hash": "ghi",
            "file_type": "code",
            "compressed_content": "...",
            "indexed_at": 3.0,
            "metadata": {},
        },
    }
    store_data = {"blocks": blocks}
    store_path.write_text(json.dumps(store_data))
    return store_path


@pytest.fixture()
def pins_path(tmp_path):
    """Return a fresh pins file path."""
    return tmp_path / "pinned_blocks.json"


# ---------------------------------------------------------------------------
# Helper to patch paths
# ---------------------------------------------------------------------------


def _patch_paths(store_path, pins_path):
    """Context manager to override module-level paths."""
    return [
        patch("tokenpak.cli.commands.prune._BLOCK_STORE_PATH", str(store_path)),
        patch("tokenpak.cli.commands.prune._PINS_PATH", str(pins_path)),
        patch("tokenpak.cli.commands.retain._BLOCK_STORE_PATH", str(store_path)),
        patch("tokenpak.cli.commands.retain._PINS_PATH", str(pins_path)),
    ]


# ---------------------------------------------------------------------------
# Test 1 — Prune identifies correct low-priority candidates
# ---------------------------------------------------------------------------


def test_prune_identifies_candidates(block_store, pins_path):
    """Low-score blocks are flagged as candidates; high-score are kept."""
    from tokenpak.cli.commands.prune import _load_blocks, _load_pins, _prune_candidates

    patches = _patch_paths(block_store, pins_path)
    for p in patches:
        p.start()
    try:
        blocks = _load_blocks()
        pins = _load_pins()
        candidates, keep = _prune_candidates(blocks, pins, threshold=0.4)

        candidate_ids = {b["block_id"] for b in candidates}
        keep_ids = {b["block_id"] for b in keep}

        assert "file_a.py#aaa111" in candidate_ids  # score 0.1 < 0.4
        assert "file_b.py#bbb222" in candidate_ids  # score 0.2 < 0.4
        assert "file_c.py#ccc333" in keep_ids  # score 0.8 >= 0.4
    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# Test 2 — --dry-run doesn't modify state
# ---------------------------------------------------------------------------


def test_prune_dry_run_no_changes(block_store, pins_path, capsys):
    """Dry-run shows candidates but doesn't remove any blocks."""
    from tokenpak.cli.commands.prune import run_prune

    store_content_before = block_store.read_text()

    patches = _patch_paths(block_store, pins_path)
    for p in patches:
        p.start()
    try:
        with patch("tokenpak.cli.commands.prune.is_pro", return_value=True, create=True):
            # Patch the tier gate away
            with patch("tokenpak.cli.commands.prune.run_prune.__module__", create=False):
                pass
        # Call directly with tier gate bypassed via import mock
        with patch.dict(sys.modules, {"tokenpak.infrastructure.license_activation": None}):
            run_prune(dry_run=True, threshold=0.4)
    finally:
        for p in patches:
            p.stop()

    store_content_after = block_store.read_text()
    assert store_content_before == store_content_after  # unchanged

    captured = capsys.readouterr()
    assert "Dry Run" in captured.out
    assert "file_a.py#aaa111" in captured.out


# ---------------------------------------------------------------------------
# Test 3 — Retain pins persist across calls
# ---------------------------------------------------------------------------


def test_retain_pins_persist(block_store, pins_path):
    """Pinning a block writes to disk and survives a fresh load."""
    from tokenpak.cli.commands.retain import load_pins, pin_block, unpin_block

    patches = _patch_paths(block_store, pins_path)
    for p in patches:
        p.start()
    try:
        result = pin_block("file_a.py#aaa111")
        assert result is True  # newly pinned

        # Reload from disk
        reloaded_pins = load_pins()
        assert "file_a.py#aaa111" in reloaded_pins

        # Pin again — should return False (already pinned)
        result2 = pin_block("file_a.py#aaa111")
        assert result2 is False

        # Unpin
        removed = unpin_block("file_a.py#aaa111")
        assert removed is True

        # Verify it's gone
        final_pins = load_pins()
        assert "file_a.py#aaa111" not in final_pins
    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# Test 4 — Retained blocks never appear in prune candidates
# ---------------------------------------------------------------------------


def test_pinned_blocks_skipped_by_prune(block_store, pins_path):
    """A pinned block with low quality_score is NOT a prune candidate."""
    from tokenpak.cli.commands.prune import _load_blocks, _prune_candidates
    from tokenpak.cli.commands.retain import pin_block

    patches = _patch_paths(block_store, pins_path)
    for p in patches:
        p.start()
    try:
        # Pin the lowest-score block
        pin_block("file_a.py#aaa111")

        pins_set = {"file_a.py#aaa111"}
        blocks = _load_blocks()
        candidates, keep = _prune_candidates(blocks, pins_set, threshold=0.4)

        candidate_ids = {b["block_id"] for b in candidates}
        keep_ids = {b["block_id"] for b in keep}

        # Pinned block must NOT appear in candidates
        assert "file_a.py#aaa111" not in candidate_ids
        assert "file_a.py#aaa111" in keep_ids

        # The other low-score block (not pinned) should still be a candidate
        assert "file_b.py#bbb222" in candidate_ids
    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# Test 5 — --auto respects threshold
# ---------------------------------------------------------------------------


def test_prune_auto_respects_threshold(block_store, pins_path):
    """Auto-prune removes exactly the blocks below the threshold."""
    from tokenpak.cli.commands.prune import run_prune

    patches = _patch_paths(block_store, pins_path)
    for p in patches:
        p.start()
    try:
        with patch.dict(sys.modules, {"tokenpak.infrastructure.license_activation": None}):
            run_prune(auto=True, threshold=0.4)

        # Read updated store
        data = json.loads(block_store.read_text())
        remaining_ids = set(data["blocks"].keys())

        # file_c.py#ccc333 (score 0.8) should remain
        assert "file_c.py#ccc333" in remaining_ids
        # file_a and file_b (scores 0.1, 0.2) should be gone
        assert "file_a.py#aaa111" not in remaining_ids
        assert "file_b.py#bbb222" not in remaining_ids
    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# Test 6 — --list shows all pins
# ---------------------------------------------------------------------------


def test_retain_list_shows_pins(block_store, pins_path, capsys):
    """retain --list displays all currently pinned blocks."""
    from tokenpak.cli.commands.retain import pin_block, run_retain_list

    patches = _patch_paths(block_store, pins_path)
    for p in patches:
        p.start()
    try:
        pin_block("file_a.py#aaa111")
        pin_block("file_c.py#ccc333")

        run_retain_list()
        captured = capsys.readouterr()

        assert "file_a.py#aaa111" in captured.out
        assert "file_c.py#ccc333" in captured.out
        assert "2 pinned" in captured.out
    finally:
        for p in patches:
            p.stop()
