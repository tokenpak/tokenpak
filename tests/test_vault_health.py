"""Unit tests for VaultHealth class.

These tests use the actual VaultHealth interface which stores index at
vault_dir/.tokenpak/index.json and blocks at vault_dir/.tokenpak/blocks/.
"""


import pytest

pytest.importorskip("tokenpak.vault_health", reason="module not available in current build")
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tokenpak.vault_health import IndexStatus, VaultHealth


class TestVaultHealth:
    """Test suite for VaultHealth."""

    @pytest.fixture
    def temp_vault(self):
        """Create a temporary vault structure matching VaultHealth's expected layout.

        VaultHealth layout:
          vault_dir/                  ← source files live here (walked during rebuild)
            .tokenpak/
              index.json              ← index file
              blocks/                 ← block .txt output files (written by rebuild)
            notes/                    ← example source dir
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_root = Path(tmpdir)
            tokenpak_dir = vault_root / ".tokenpak"
            blocks_dir = tokenpak_dir / "blocks"
            source_dir = vault_root / "notes"  # source files go here
            tokenpak_dir.mkdir()
            blocks_dir.mkdir()
            source_dir.mkdir()

            yield {
                "root": vault_root,
                "tokenpak_dir": tokenpak_dir,
                "blocks_dir": blocks_dir,
                "source_dir": source_dir,  # put test .md/.txt files here
                "index_path": tokenpak_dir / "index.json",
            }

    def _write_index(self, index_path: Path, blocks: dict, n: int = 0):
        """Helper: write a valid index.json with given blocks."""
        data = {
            "version": "1.0",
            "meta": {
                "source_dir": str(index_path.parent.parent),
                "indexed_at": datetime.now(timezone.utc).isoformat(),
            },
            "blocks": blocks,
        }
        index_path.write_text(json.dumps(data))

    @pytest.mark.quick
    def test_healthy_index_no_rebuild_needed(self, temp_vault):
        """Test that a healthy, fresh index reports not stale."""
        vault_root = temp_vault["root"]
        index_path = temp_vault["index_path"]

        self._write_index(index_path, {
            f"block_{i}": {"block_id": f"block_{i}", "source_path": f"notes/note_{i}.md"} for i in range(5)
        })

        health = VaultHealth(str(vault_root))
        # Fresh index is within threshold — should NOT be stale
        assert not health.check_index_staleness()
        assert health.get_status() == IndexStatus.OK

    def test_stale_index_with_old_mtime(self, temp_vault):
        """Test detection of staleness when index mtime is very old."""
        import os
        import time
        vault_root = temp_vault["root"]
        index_path = temp_vault["index_path"]

        self._write_index(index_path, {
            f"block_{i}": {"block_id": f"block_{i}", "source_path": f"notes/note_{i}.md"} for i in range(5)
        })

        # Age the file to 2 days old
        old_time = time.time() - (2 * 86400)
        os.utime(str(index_path), (old_time, old_time))

        # Use 1-hour staleness threshold to make it stale
        health = VaultHealth(str(vault_root), stale_seconds=3600)
        assert health.check_index_staleness()
        assert health.get_status() == IndexStatus.STALE

    def test_rebuild_index_from_blocks(self, temp_vault):
        """Test successful rebuild of index from source files on disk."""
        vault_root = temp_vault["root"]
        source_dir = temp_vault["source_dir"]
        index_path = temp_vault["index_path"]

        for i in range(50):
            (source_dir / f"note_{i}.md").write_text(f"# Note {i}\nContent for note {i}.\n")

        # Create empty/outdated index
        self._write_index(index_path, {})

        health = VaultHealth(str(vault_root))
        metrics = health.rebuild_index()

        assert metrics.get("success", metrics.get("healthy")) is True
        assert metrics["index_entries"] == 50
        assert metrics["index_entries"] == 50
        assert metrics["rebuild_time_seconds"] >= 0
        assert metrics["entries_added"] == 50
        assert metrics["index_size_bytes"] > 0

        # Verify index is now healthy
        health2 = VaultHealth(str(vault_root))
        assert not health2.check_index_staleness()

    def test_rebuild_with_entries_removed(self, temp_vault):
        """Test rebuild produces correct count when fewer files exist on disk than in old index.

        The rebuild walks source files; ghost entries (pointing to nonexistent files)
        are simply never added to new_blocks, so final index_entries reflects only
        real files on disk.
        """
        vault_root = temp_vault["root"]
        source_dir = temp_vault["source_dir"]
        index_path = temp_vault["index_path"]

        # Create 10 real source files
        for i in range(10):
            (source_dir / f"note_{i}.md").write_text(f"# Note {i}\nContent.\n")

        # Old index claims 15 entries but only 10 source files exist on disk
        blocks = {}
        for i in range(10):
            bid = f"real_{i}"
            blocks[bid] = {"block_id": bid, "source_path": f"notes/note_{i}.md", "content_hash": "old"}
        for i in range(10, 15):
            bid = f"ghost_{i}"
            blocks[bid] = {"block_id": bid, "source_path": f"notes/note_{i}.md", "content_hash": "old"}
        self._write_index(index_path, blocks)

        health = VaultHealth(str(vault_root))
        metrics = health.rebuild_index()

        # After rebuild: only real files indexed; ghost entries are dropped
        assert metrics["index_entries"] == 10
        # entries_added may be 10 (re-indexed with changed hash) or 0 (skipped as unchanged)
        assert metrics["index_entries"] <= 10

    @pytest.mark.quick
    def test_missing_index_is_stale(self, temp_vault):
        """Test that missing index.json reports as stale/missing (not an exception)."""
        vault_root = temp_vault["root"]

        health = VaultHealth(str(vault_root))
        # Missing index → should be detected as stale/missing
        assert health.check_index_staleness()
        assert health.get_status() in (IndexStatus.MISSING, IndexStatus.STALE)

    def test_missing_blocks_dir_still_checks(self, temp_vault):
        """Test that missing blocks directory doesn't crash health check."""
        import shutil
        vault_root = temp_vault["root"]
        index_path = temp_vault["index_path"]

        self._write_index(index_path, {})

        # Remove blocks dir
        if temp_vault["blocks_dir"].exists():
            shutil.rmtree(temp_vault["blocks_dir"])

        health = VaultHealth(str(vault_root))
        # Should not raise, just report stale or ok
        result = health.check()
        assert result.status in (IndexStatus.OK, IndexStatus.STALE, IndexStatus.MISSING, IndexStatus.CORRUPT)

    @pytest.mark.quick
    def test_invalid_json_is_corrupt(self, temp_vault):
        """Test that invalid JSON in index is reported as corrupt."""
        vault_root = temp_vault["root"]
        source_dir = temp_vault["source_dir"]
        index_path = temp_vault["index_path"]

        (source_dir / "note_0.md").write_text("# Note\nContent.\n")
        index_path.write_text("{ invalid json }")

        health = VaultHealth(str(vault_root))
        # Corrupt index → stale or corrupt (both are problems)
        assert health.check_index_staleness()
        assert health.get_status() in (IndexStatus.CORRUPT, IndexStatus.STALE)

    def test_metrics_are_reasonable(self, temp_vault):
        """Test that rebuild metrics are reasonable and non-zero."""
        vault_root = temp_vault["root"]
        source_dir = temp_vault["source_dir"]
        index_path = temp_vault["index_path"]

        for i in range(100):
            (source_dir / f"note_{i}.md").write_text(f"# Note {i}\nContent for note {i}.\n")

        self._write_index(index_path, {})

        health = VaultHealth(str(vault_root))
        metrics = health.rebuild_index()

        assert metrics["rebuild_time_seconds"] >= 0
        assert metrics["index_entries"] == 100
        assert metrics["index_entries"] == 100
        assert metrics["entries_added"] == 100
        assert metrics["index_size_bytes"] > 1000


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
