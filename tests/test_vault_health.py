"""Unit tests for VaultHealth class."""

import json
import pytest
import tempfile
from pathlib import Path
from datetime import datetime
import time

from tokenpak.vault_health import VaultHealth


class TestVaultHealth:
    """Test suite for VaultHealth."""
    
    @pytest.fixture
    def temp_vault(self):
        """Create a temporary vault structure."""
        with tempfile.TemporaryDirectory() as tmpdir:
            vault_root = Path(tmpdir)
            blocks_dir = vault_root / "blocks"
            blocks_dir.mkdir()
            
            yield {
                "root": vault_root,
                "blocks_dir": blocks_dir,
            }
    
    def test_healthy_index_no_rebuild_needed(self, temp_vault):
        """Test that a healthy index with matching blocks reports no rebuild needed."""
        vault_root = temp_vault["root"]
        blocks_dir = temp_vault["blocks_dir"]
        
        # Create 5 block files
        for i in range(5):
            (blocks_dir / f"block_{i}.json").write_text(f'{{"id": {i}}}')
        
        # Create matching index
        index_path = vault_root / "index.json"
        index_data = {
            "version": "1.0",
            "meta": {"source_dir": str(vault_root), "indexed_at": datetime.utcnow().isoformat()},
            "blocks": {
                f"block_{i}.json": {"block_id": f"block_{i}.json"}
                for i in range(5)
            }
        }
        index_path.write_text(json.dumps(index_data))
        
        health = VaultHealth(str(vault_root))
        assert not health.check_index_staleness()
        assert health.get_status().startswith("Index is current")
    
    def test_stale_index_with_gap(self, temp_vault):
        """Test detection of index staleness when blocks exist but not in index."""
        vault_root = temp_vault["root"]
        blocks_dir = temp_vault["blocks_dir"]
        
        # Create 10 block files
        for i in range(10):
            (blocks_dir / f"block_{i}.json").write_text(f'{{"id": {i}}}')
        
        # Create index with only 7 blocks
        index_path = vault_root / "index.json"
        index_data = {
            "version": "1.0",
            "meta": {"source_dir": str(vault_root), "indexed_at": datetime.utcnow().isoformat()},
            "blocks": {
                f"block_{i}.json": {"block_id": f"block_{i}.json"}
                for i in range(7)
            }
        }
        index_path.write_text(json.dumps(index_data))
        
        health = VaultHealth(str(vault_root))
        assert health.check_index_staleness()
        assert "stale" in health.get_status().lower()
    
    def test_rebuild_index_from_blocks(self, temp_vault):
        """Test successful rebuild of index from blocks on disk."""
        vault_root = temp_vault["root"]
        blocks_dir = temp_vault["blocks_dir"]
        
        # Create 50 block files
        for i in range(50):
            (blocks_dir / f"block_{i}.json").write_text(f'{{"id": {i}, "size": {i*100}}}')
        
        # Create empty/old index
        index_path = vault_root / "index.json"
        index_data = {
            "version": "1.0",
            "meta": {"source_dir": str(vault_root)},
            "blocks": {}
        }
        index_path.write_text(json.dumps(index_data))
        
        health = VaultHealth(str(vault_root))
        
        # Verify it's stale
        assert health.check_index_staleness()
        
        # Rebuild
        metrics = health.rebuild_index()
        
        assert metrics["healthy"] is True
        assert metrics["index_entries"] == 50
        assert metrics["block_count"] == 50
        assert metrics["rebuild_time_seconds"] > 0
        assert metrics["entries_added"] == 50
        assert metrics["index_size_bytes"] > 0
        
        # Verify index is now healthy
        health2 = VaultHealth(str(vault_root))
        assert not health2.check_index_staleness()
    
    def test_rebuild_with_entries_removed(self, temp_vault):
        """Test rebuild when index has extra entries not on disk."""
        vault_root = temp_vault["root"]
        blocks_dir = temp_vault["blocks_dir"]
        
        # Create 10 block files
        for i in range(10):
            (blocks_dir / f"block_{i}.json").write_text(f'{{"id": {i}}}')
        
        # Create index with 15 blocks (5 extra that don't exist on disk)
        index_path = vault_root / "index.json"
        index_data = {
            "version": "1.0",
            "meta": {"source_dir": str(vault_root), "indexed_at": datetime.utcnow().isoformat()},
            "blocks": {
                f"block_{i}.json": {"block_id": f"block_{i}.json"}
                for i in range(15)
            }
        }
        index_path.write_text(json.dumps(index_data))
        
        health = VaultHealth(str(vault_root))
        assert health.check_index_staleness()
        
        metrics = health.rebuild_index()
        
        assert metrics["index_entries"] == 10
        assert metrics["block_count"] == 10
        assert metrics["entries_removed"] == 5
    
    def test_missing_index_raises_error(self, temp_vault):
        """Test that missing index.json raises error when checking staleness."""
        vault_root = temp_vault["root"]
        
        health = VaultHealth(str(vault_root))
        
        with pytest.raises(FileNotFoundError):
            health.check_index_staleness()
    
    def test_missing_blocks_dir_raises_error(self, temp_vault):
        """Test that missing blocks directory raises error."""
        vault_root = temp_vault["root"]
        
        # Create index but no blocks dir
        index_path = vault_root / "index.json"
        index_data = {"version": "1.0", "blocks": {}}
        index_path.write_text(json.dumps(index_data))
        
        # Remove blocks dir if created
        blocks_dir = vault_root / "blocks"
        if blocks_dir.exists():
            import shutil
            shutil.rmtree(blocks_dir)
        
        health = VaultHealth(str(vault_root))
        
        with pytest.raises(FileNotFoundError):
            health.check_index_staleness()
    
    def test_invalid_json_raises_error(self, temp_vault):
        """Test that invalid JSON in index raises error."""
        vault_root = temp_vault["root"]
        blocks_dir = temp_vault["blocks_dir"]
        
        # Create a block
        (blocks_dir / "block_0.json").write_text('{"id": 0}')
        
        # Create invalid JSON index
        index_path = vault_root / "index.json"
        index_path.write_text("{ invalid json }")
        
        health = VaultHealth(str(vault_root))
        
        with pytest.raises(ValueError):
            health.check_index_staleness()
    
    def test_metrics_are_reasonable(self, temp_vault):
        """Test that rebuild metrics are reasonable and non-zero."""
        vault_root = temp_vault["root"]
        blocks_dir = temp_vault["blocks_dir"]
        
        # Create 100 block files
        for i in range(100):
            (blocks_dir / f"block_{i}.json").write_text(f'{{"id": {i}}}')
        
        # Create empty index
        index_path = vault_root / "index.json"
        index_data = {"version": "1.0", "blocks": {}}
        index_path.write_text(json.dumps(index_data))
        
        health = VaultHealth(str(vault_root))
        metrics = health.rebuild_index()
        
        # Verify metrics are reasonable
        assert metrics["rebuild_time_seconds"] >= 0
        assert metrics["index_entries"] == 100
        assert metrics["block_count"] == 100
        assert metrics["entries_added"] == 100
        assert metrics["index_size_bytes"] > 1000  # Should be non-trivial


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
