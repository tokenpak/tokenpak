"""
TokenPak Vault Index Health Monitoring

Provides health checks for the TokenPak vault index (~/.tokenpak/index.json):
- Freshness validation (detects staleness)
- Structure validation (JSON integrity + required keys)
- Block file verification (ensures all referenced blocks exist)
"""

import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple


class IndexHealthError(Exception):
    """Base exception for index health check failures."""
    pass


class IndexHealthStatus:
    """Represents the health status of the TokenPak index."""
    
    STATUS_OK = "ok"
    STATUS_WARN = "warn"
    STATUS_ERROR = "error"
    
    def __init__(self, status: str, age_seconds: float, issues: Optional[List[str]] = None):
        """Initialize health status."""
        self.status = status
        self.age_seconds = age_seconds
        self.issues = issues or []
        self.timestamp = datetime.utcnow().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "status": self.status,
            "age_seconds": round(self.age_seconds, 2),
            "issues": self.issues,
            "timestamp": self.timestamp,
        }


class VaultIndexHealthMonitor:
    """Monitor and validate TokenPak vault index health."""
    
    # Configuration
    INDEX_PATH = Path.home() / ".tokenpak" / "index.json"
    BLOCKS_DIR = Path.home() / ".tokenpak" / "blocks"
    FRESHNESS_THRESHOLD_SECONDS = 330  # 5 min + 30s drift
    
    def __init__(self, index_path: Optional[Path] = None, blocks_dir: Optional[Path] = None):
        """Initialize monitor with optional custom paths."""
        self.index_path = index_path or self.INDEX_PATH
        self.blocks_dir = blocks_dir or self.BLOCKS_DIR
    
    def check_index_freshness(self) -> Tuple[float, Optional[str]]:
        """
        Check index freshness by comparing modification time to current time.
        
        Returns:
            Tuple of (age_seconds, issue_message or None)
            - age_seconds: how old the index is in seconds
            - issue_message: warning if stale, None if fresh
        """
        if not self.index_path.exists():
            return 0.0, "Index file does not exist"
        
        mtime = self.index_path.stat().st_mtime
        now = time.time()
        age_seconds = now - mtime
        
        if age_seconds > self.FRESHNESS_THRESHOLD_SECONDS:
            return age_seconds, f"Index is stale: {age_seconds:.1f}s old (threshold: {self.FRESHNESS_THRESHOLD_SECONDS}s)"
        
        return age_seconds, None
    
    def validate_index_structure(self) -> Tuple[bool, List[str]]:
        """
        Validate that index.json exists, is valid JSON, and contains required keys.
        
        Returns:
            Tuple of (is_valid, issues_list)
        """
        issues = []
        
        # Check file exists
        if not self.index_path.exists():
            return False, ["Index file does not exist"]
        
        # Try to parse JSON
        try:
            with open(self.index_path, 'r') as f:
                index_data = json.load(f)
        except json.JSONDecodeError as e:
            return False, [f"Invalid JSON in index file: {str(e)}"]
        except IOError as e:
            return False, [f"Cannot read index file: {str(e)}"]
        
        # Validate required keys
        required_keys = ["blocks", "meta", "version"]
        missing_keys = [k for k in required_keys if k not in index_data]
        
        if missing_keys:
            issues.append(f"Missing required keys: {missing_keys}")
        
        # Validate blocks is a dict
        if "blocks" in index_data and not isinstance(index_data["blocks"], dict):
            issues.append("'blocks' field must be a dictionary")
        
        # Validate meta is a dict
        if "meta" in index_data and not isinstance(index_data["meta"], dict):
            issues.append("'meta' field must be a dictionary")
        
        # Validate version is a string
        if "version" in index_data and not isinstance(index_data["version"], str):
            issues.append("'version' field must be a string")
        
        is_valid = len(issues) == 0
        return is_valid, issues
    
    def verify_block_files_exist(self) -> Tuple[List[str], List[str]]:
        """
        Verify that all block files referenced in index.json actually exist on disk.
        
        Returns:
            Tuple of (missing_blocks, issues_list)
        """
        issues = []
        missing_blocks = []
        
        # Read index
        try:
            with open(self.index_path, 'r') as f:
                index_data = json.load(f)
        except (json.JSONDecodeError, IOError) as e:
            return [], [f"Cannot read index for block verification: {str(e)}"]
        
        blocks = index_data.get("blocks", {})
        
        if not blocks:
            issues.append("Index contains no blocks")
            return [], issues
        
        # Check each block's file reference
        # Format: blocks are stored with IDs, but files should be checked
        # Standard pattern: blocks/*.txt
        
        for block_id in blocks.keys():
            # Expect block file at ~/.tokenpak/blocks/<block_id>.txt
            block_file = self.blocks_dir / f"{block_id}.txt"
            
            if not block_file.exists():
                missing_blocks.append(block_id)
        
        if missing_blocks:
            issues.append(f"Missing {len(missing_blocks)} block files (checked in {self.blocks_dir})")
        
        return missing_blocks, issues
    
    def get_index_data(self) -> Optional[Dict[str, Any]]:
        """Load and return the full index data."""
        try:
            with open(self.index_path, 'r') as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return None
    
    def check_all(self) -> IndexHealthStatus:
        """
        Run all health checks and return comprehensive status.
        
        Returns:
            IndexHealthStatus with overall status (ok|warn|error) and issues.
        """
        issues = []
        age_seconds = 0
        
        # 1. Check freshness
        age_seconds, freshness_issue = self.check_index_freshness()
        if freshness_issue:
            issues.append(freshness_issue)
        
        # 2. Check structure
        is_valid_structure, structure_issues = self.validate_index_structure()
        issues.extend(structure_issues)
        
        # 3. Check block files (only if structure is valid)
        if is_valid_structure:
            missing_blocks, block_issues = self.verify_block_files_exist()
            if missing_blocks:
                issues.append(f"Missing blocks: {missing_blocks[:5]}" + 
                            (f" ... and {len(missing_blocks)-5} more" if len(missing_blocks) > 5 else ""))
            issues.extend(block_issues)
        
        # Determine status
        if not is_valid_structure:
            status = IndexHealthStatus.STATUS_ERROR
        elif issues:
            status = IndexHealthStatus.STATUS_WARN
        else:
            status = IndexHealthStatus.STATUS_OK
        
        return IndexHealthStatus(status, age_seconds, issues)


# Convenience functions for direct use
_monitor = None


def get_monitor(index_path: Optional[Path] = None, blocks_dir: Optional[Path] = None) -> VaultIndexHealthMonitor:
    """Get or create a monitor instance."""
    global _monitor
    if _monitor is None:
        _monitor = VaultIndexHealthMonitor(index_path, blocks_dir)
    return _monitor


def check_index_freshness() -> Tuple[float, Optional[str]]:
    """Check index freshness (age in seconds and warning if stale)."""
    return get_monitor().check_index_freshness()


def validate_index_structure() -> Tuple[bool, List[str]]:
    """Validate index JSON structure and required keys."""
    return get_monitor().validate_index_structure()


def verify_block_files_exist() -> Tuple[List[str], List[str]]:
    """Verify all block files referenced in index exist on disk."""
    return get_monitor().verify_block_files_exist()


def check_all() -> IndexHealthStatus:
    """Run all checks and return comprehensive health status."""
    return get_monitor().check_all()
