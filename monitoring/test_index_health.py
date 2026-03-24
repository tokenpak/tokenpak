"""
Tests for TokenPak Vault Index Health Monitor

Coverage:
- Fresh index (normal state)
- Stale index (age > threshold)
- Corrupt JSON
- Missing required keys
- Invalid field types
- Missing block files
- Empty index
- Block file verification
"""

import json
import os
import pytest
import tempfile
import time
from pathlib import Path
from datetime import datetime, timedelta

from index_health import (
    VaultIndexHealthMonitor,
    IndexHealthStatus,
    IndexHealthError,
)


@pytest.fixture
def temp_index_dir():
    """Create temporary directory with index and blocks subdirs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)
        index_dir = tmpdir_path / ".tokenpak"
        blocks_dir = index_dir / "blocks"
        index_dir.mkdir(parents=True)
        blocks_dir.mkdir(parents=True)
        
        yield {
            "root": tmpdir_path,
            "index_dir": index_dir,
            "blocks_dir": blocks_dir,
            "index_path": index_dir / "index.json",
        }


@pytest.fixture
def monitor(temp_index_dir):
    """Create monitor pointing to temp directory."""
    return VaultIndexHealthMonitor(
        index_path=temp_index_dir["index_path"],
        blocks_dir=temp_index_dir["blocks_dir"],
    )


@pytest.fixture
def valid_index_data():
    """Sample valid index data."""
    return {
        "version": "1.0",
        "meta": {
            "source_dir": "/home/user/vault",
            "indexed_at": datetime.utcnow().isoformat(),
            "stats": {
                "scanned": 100,
                "indexed": 95,
                "updated": 95,
                "skipped": 5,
                "errors": 0,
            }
        },
        "blocks": {
            "readme.md": {
                "block_id": "readme.md",
                "source_path": "README.md",
                "content_hash": "abc123",
                "raw_tokens": 100,
                "raw_size": 400,
            },
            "config.yaml": {
                "block_id": "config.yaml",
                "source_path": "config.yaml",
                "content_hash": "def456",
                "raw_tokens": 50,
                "raw_size": 200,
            },
        }
    }


class TestIndexFreshness:
    """Test index freshness checks."""
    
    def test_fresh_index(self, monitor, temp_index_dir, valid_index_data):
        """Fresh index should have no staleness warning."""
        # Write valid index
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        age_seconds, issue = monitor.check_index_freshness()
        
        assert age_seconds >= 0
        assert age_seconds < 1  # Just written, should be < 1 second old
        assert issue is None
    
    def test_stale_index(self, monitor, temp_index_dir, valid_index_data):
        """Stale index should trigger warning."""
        # Write valid index
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        # Modify mtime to make it old
        old_time = time.time() - 400  # 400 seconds old
        os.utime(temp_index_dir["index_path"], (old_time, old_time))
        
        age_seconds, issue = monitor.check_index_freshness()
        
        assert age_seconds > 395  # At least ~400 seconds old
        assert issue is not None
        assert "stale" in issue.lower()
        assert "330" in issue  # Threshold value in message
    
    def test_missing_index_file(self, monitor):
        """Missing index file should report appropriate error."""
        age_seconds, issue = monitor.check_index_freshness()
        
        assert age_seconds == 0
        assert issue == "Index file does not exist"


class TestStructureValidation:
    """Test index structure validation."""
    
    def test_valid_structure(self, monitor, temp_index_dir, valid_index_data):
        """Valid structure should pass validation."""
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        is_valid, issues = monitor.validate_index_structure()
        
        assert is_valid is True
        assert len(issues) == 0
    
    def test_corrupt_json(self, monitor, temp_index_dir):
        """Corrupt JSON should fail validation."""
        with open(temp_index_dir["index_path"], "w") as f:
            f.write("{invalid json content")
        
        is_valid, issues = monitor.validate_index_structure()
        
        assert is_valid is False
        assert len(issues) > 0
        assert any("json" in issue.lower() for issue in issues)
    
    def test_missing_blocks_key(self, monitor, temp_index_dir):
        """Missing 'blocks' key should fail."""
        data = {"version": "1.0", "meta": {}}
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(data, f)
        
        is_valid, issues = monitor.validate_index_structure()
        
        assert is_valid is False
        assert any("blocks" in issue for issue in issues)
    
    def test_missing_meta_key(self, monitor, temp_index_dir):
        """Missing 'meta' key should fail."""
        data = {"version": "1.0", "blocks": {}}
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(data, f)
        
        is_valid, issues = monitor.validate_index_structure()
        
        assert is_valid is False
        assert any("meta" in issue for issue in issues)
    
    def test_missing_version_key(self, monitor, temp_index_dir):
        """Missing 'version' key should fail."""
        data = {"blocks": {}, "meta": {}}
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(data, f)
        
        is_valid, issues = monitor.validate_index_structure()
        
        assert is_valid is False
        assert any("version" in issue for issue in issues)
    
    def test_blocks_not_dict(self, monitor, temp_index_dir):
        """'blocks' must be a dict, not list or string."""
        data = {"version": "1.0", "meta": {}, "blocks": ["item1", "item2"]}
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(data, f)
        
        is_valid, issues = monitor.validate_index_structure()
        
        assert is_valid is False
        assert any("blocks" in issue and "dict" in issue for issue in issues)
    
    def test_meta_not_dict(self, monitor, temp_index_dir):
        """'meta' must be a dict."""
        data = {"version": "1.0", "blocks": {}, "meta": "not a dict"}
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(data, f)
        
        is_valid, issues = monitor.validate_index_structure()
        
        assert is_valid is False
        assert any("meta" in issue and "dict" in issue for issue in issues)
    
    def test_version_not_string(self, monitor, temp_index_dir):
        """'version' must be a string."""
        data = {"version": 1.0, "blocks": {}, "meta": {}}
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(data, f)
        
        is_valid, issues = monitor.validate_index_structure()
        
        assert is_valid is False
        assert any("version" in issue and "string" in issue for issue in issues)


class TestBlockFileVerification:
    """Test block file existence verification."""
    
    def test_all_blocks_exist(self, monitor, temp_index_dir, valid_index_data):
        """All block files present should pass verification."""
        # Write index
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        # Create block files
        for block_id in valid_index_data["blocks"].keys():
            block_file = temp_index_dir["blocks_dir"] / f"{block_id}.txt"
            block_file.write_text(f"Block content for {block_id}")
        
        missing, issues = monitor.verify_block_files_exist()
        
        assert len(missing) == 0
        assert len(issues) == 0
    
    def test_missing_block_files(self, monitor, temp_index_dir, valid_index_data):
        """Missing block files should be detected."""
        # Write index but don't create block files
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        missing, issues = monitor.verify_block_files_exist()
        
        assert len(missing) == 2  # Both blocks missing
        assert "readme.md" in missing
        assert "config.yaml" in missing
        assert any("missing" in issue.lower() for issue in issues)
    
    def test_partial_missing_blocks(self, monitor, temp_index_dir, valid_index_data):
        """Partially missing blocks should be reported."""
        # Write index
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        # Create only one block file
        block_file = temp_index_dir["blocks_dir"] / "readme.md.txt"
        block_file.write_text("Block content")
        
        missing, issues = monitor.verify_block_files_exist()
        
        assert len(missing) == 1
        assert "config.yaml" in missing
    
    def test_empty_blocks_list(self, monitor, temp_index_dir):
        """Index with empty blocks should not report missing blocks."""
        data = {"version": "1.0", "meta": {}, "blocks": {}}
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(data, f)
        
        missing, issues = monitor.verify_block_files_exist()
        
        assert len(missing) == 0
        assert any("no blocks" in issue.lower() for issue in issues)
    
    def test_corrupted_index_during_verification(self, monitor, temp_index_dir):
        """Corrupted index should not crash block verification."""
        with open(temp_index_dir["index_path"], "w") as f:
            f.write("{corrupted json")
        
        missing, issues = monitor.verify_block_files_exist()
        
        assert len(missing) == 0
        assert any("cannot read" in issue.lower() for issue in issues)


class TestComprehensiveHealthCheck:
    """Test the comprehensive health check (check_all)."""
    
    def test_healthy_index(self, monitor, temp_index_dir, valid_index_data):
        """Healthy index should return OK status."""
        # Write index
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        # Create block files
        for block_id in valid_index_data["blocks"].keys():
            block_file = temp_index_dir["blocks_dir"] / f"{block_id}.txt"
            block_file.write_text(f"Block content for {block_id}")
        
        status = monitor.check_all()
        
        assert status.status == IndexHealthStatus.STATUS_OK
        assert len(status.issues) == 0
        assert status.age_seconds >= 0
        assert status.age_seconds < 1
    
    def test_stale_healthy_structure(self, monitor, temp_index_dir, valid_index_data):
        """Stale but otherwise healthy index should return WARN."""
        # Write index
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        # Create block files
        for block_id in valid_index_data["blocks"].keys():
            block_file = temp_index_dir["blocks_dir"] / f"{block_id}.txt"
            block_file.write_text(f"Block content for {block_id}")
        
        # Make index old
        old_time = time.time() - 400
        os.utime(temp_index_dir["index_path"], (old_time, old_time))
        
        status = monitor.check_all()
        
        assert status.status == IndexHealthStatus.STATUS_WARN
        assert len(status.issues) > 0
        assert any("stale" in issue.lower() for issue in status.issues)
    
    def test_missing_block_files_warn(self, monitor, temp_index_dir, valid_index_data):
        """Missing block files should result in WARN status."""
        # Write index but no block files
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        status = monitor.check_all()
        
        assert status.status == IndexHealthStatus.STATUS_WARN
        assert any("missing" in issue.lower() for issue in status.issues)
    
    def test_corrupted_index_error(self, monitor, temp_index_dir):
        """Corrupted index should return ERROR status."""
        with open(temp_index_dir["index_path"], "w") as f:
            f.write("{bad json")
        
        status = monitor.check_all()
        
        assert status.status == IndexHealthStatus.STATUS_ERROR
        assert len(status.issues) > 0
    
    def test_missing_index_error(self, monitor):
        """Missing index should return ERROR status."""
        status = monitor.check_all()
        
        assert status.status == IndexHealthStatus.STATUS_ERROR
        assert len(status.issues) > 0
    
    def test_status_to_dict(self, monitor, temp_index_dir, valid_index_data):
        """Status should serialize to JSON-compatible dict."""
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(valid_index_data, f)
        
        for block_id in valid_index_data["blocks"].keys():
            block_file = temp_index_dir["blocks_dir"] / f"{block_id}.txt"
            block_file.write_text(f"Block content")
        
        status = monitor.check_all()
        status_dict = status.to_dict()
        
        assert "status" in status_dict
        assert "age_seconds" in status_dict
        assert "issues" in status_dict
        assert "timestamp" in status_dict
        
        # Should be JSON serializable
        json_str = json.dumps(status_dict)
        assert json_str is not None


class TestEdgeCases:
    """Test edge cases and boundary conditions."""
    
    def test_many_blocks(self, monitor, temp_index_dir):
        """Index with many blocks should be handled correctly."""
        # Create index with 1000 blocks
        blocks = {f"block_{i:04d}": {"block_id": f"block_{i:04d}"} for i in range(1000)}
        data = {"version": "1.0", "meta": {}, "blocks": blocks}
        
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(data, f)
        
        # Create only first 10 block files
        for i in range(10):
            block_file = temp_index_dir["blocks_dir"] / f"block_{i:04d}.txt"
            block_file.write_text("content")
        
        missing, issues = monitor.verify_block_files_exist()
        
        assert len(missing) == 990
        assert any("990" in issue for issue in issues)
    
    def test_very_large_index(self, monitor, temp_index_dir):
        """Large index file should be processed correctly."""
        # Create large index
        large_block = {"x" * 10000: f"y" * 1000 for _ in range(100)}
        blocks = {f"large_{i}": large_block for i in range(10)}
        data = {"version": "1.0", "meta": {"data": "x" * 50000}, "blocks": blocks}
        
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(data, f)
        
        is_valid, issues = monitor.validate_index_structure()
        
        assert is_valid is True
        assert len(issues) == 0
    
    def test_unicode_block_ids(self, monitor, temp_index_dir):
        """Unicode in block IDs should be handled."""
        data = {
            "version": "1.0",
            "meta": {},
            "blocks": {
                "readme_日本語.md": {"block_id": "readme_日本語.md"},
                "config_français.yaml": {"block_id": "config_français.yaml"},
            }
        }
        
        with open(temp_index_dir["index_path"], "w") as f:
            json.dump(data, f, ensure_ascii=False)
        
        # Create matching block files
        for block_id in data["blocks"]:
            block_file = temp_index_dir["blocks_dir"] / f"{block_id}.txt"
            block_file.write_text("content", encoding="utf-8")
        
        status = monitor.check_all()
        
        assert status.status == IndexHealthStatus.STATUS_OK


class TestMonitorInstances:
    """Test monitor instance management."""
    
    def test_custom_paths(self, temp_index_dir):
        """Monitor should work with custom paths."""
        monitor = VaultIndexHealthMonitor(
            index_path=temp_index_dir["index_path"],
            blocks_dir=temp_index_dir["blocks_dir"],
        )
        
        assert monitor.index_path == temp_index_dir["index_path"]
        assert monitor.blocks_dir == temp_index_dir["blocks_dir"]
    
    def test_default_paths(self):
        """Monitor should use default paths when not specified."""
        monitor = VaultIndexHealthMonitor()
        
        assert monitor.index_path == Path.home() / ".tokenpak" / "index.json"
        assert monitor.blocks_dir == Path.home() / ".tokenpak" / "blocks"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
