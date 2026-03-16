"""Vault Index Health Monitor

Detects vault index staleness and provides rebuild capabilities.

Usage:
    tokenpak vault-health repair
        Check if vault index is stale and rebuild if needed.
        
        Returns:
            0 if index is healthy
            1 if index was rebuilt
            2 if an error occurred
        
        Example output (healthy):
            Index: ~/.tokenpak/index.json
            Status: Index is current (6,366 entries, last modified 2026-03-16 04:23:15)
            ✅ Index is current (no rebuild needed)
            Exit code: 0

        Example output (rebuilt):
            Index: 2,130 indexed vs 6,366 blocks (4,236 missing)
            Rebuilding index from blocks...
            ✅ Rebuilt in 0.11 seconds
            Entries: 6,366
              Added: 4,236
              Removed: 0
            Index size: 1,458,307 bytes
            Exit code: 1

Exit Codes:
    0 - Index is healthy, no rebuild needed
    1 - Index was stale and successfully rebuilt
    2 - Error occurred during health check or rebuild
"""

import json
import time
import os
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from datetime import datetime


class VaultHealth:
    """Manages vault index health detection and repair."""
    
    def __init__(self, vault_root: Optional[str] = None):
        """Initialize VaultHealth.
        
        Args:
            vault_root: Root path to vault. Defaults to ~/.tokenpak
        """
        if vault_root is None:
            # Try to find the vault root by reading the symlink
            tokenpak_home = Path.home() / ".tokenpak"
            index_link = tokenpak_home / "index.json"
            
            if index_link.is_symlink():
                # Follow symlink to find actual vault location
                actual_index = index_link.resolve()
                vault_root = str(actual_index.parent)
            else:
                vault_root = str(tokenpak_home)
        
        self.vault_root = Path(vault_root)
        self.index_path = self.vault_root / "index.json"
        self.blocks_dir = self.vault_root / "blocks"
        self._rebuild_start_time: Optional[float] = None
        self._entries_before: int = 0
    
    def check_index_staleness(self) -> bool:
        """Check if index is stale (mismatch with actual blocks).
        
        Returns:
            True if index needs rebuild, False if current.
            
        Raises:
            FileNotFoundError: If index or blocks directory doesn't exist.
        """
        if not self.index_path.exists():
            raise FileNotFoundError(f"Index not found: {self.index_path}")
        
        if not self.blocks_dir.exists():
            raise FileNotFoundError(f"Blocks directory not found: {self.blocks_dir}")
        
        # Count actual blocks on disk
        block_count = len(list(self.blocks_dir.iterdir()))
        
        # Count indexed blocks
        try:
            with open(self.index_path, 'r') as f:
                index_data = json.load(f)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON in index: {e}")
        
        indexed_blocks = len(index_data.get("blocks", {}))
        
        # If counts don't match, index is stale
        return block_count != indexed_blocks
    
    def rebuild_index(self) -> Dict[str, Any]:
        """Rebuild index from blocks on disk.
        
        Returns:
            Dictionary with metrics:
            {
                "healthy": bool,
                "index_entries": int,
                "block_count": int,
                "rebuild_time_seconds": float,
                "entries_added": int,
                "entries_removed": int,
                "index_size_bytes": int,
            }
        """
        if not self.blocks_dir.exists():
            raise FileNotFoundError(f"Blocks directory not found: {self.blocks_dir}")
        
        self._rebuild_start_time = time.time()
        
        # Get before state
        self._entries_before = 0
        if self.index_path.exists():
            try:
                with open(self.index_path, 'r') as f:
                    index_data = json.load(f)
                    self._entries_before = len(index_data.get("blocks", {}))
            except (json.JSONDecodeError, IOError):
                self._entries_before = 0
        
        # Scan blocks directory
        blocks = {}
        block_files = list(self.blocks_dir.iterdir())
        
        for block_file in block_files:
            if block_file.is_file():
                block_id = block_file.name
                blocks[block_id] = {
                    "block_id": block_id,
                    "indexed_at": datetime.utcnow().isoformat() + "Z",
                }
        
        # Build new index
        new_index = {
            "version": "1.0",
            "meta": {
                "source_dir": str(self.vault_root),
                "indexed_at": datetime.utcnow().isoformat() + "Z",
                "rebuilt": True,
                "stats": {
                    "total_blocks": len(blocks),
                }
            },
            "blocks": blocks,
        }
        
        # Write index
        index_size = 0
        try:
            with open(self.index_path, 'w') as f:
                json.dump(new_index, f, indent=2)
            index_size = self.index_path.stat().st_size
        except IOError as e:
            raise RuntimeError(f"Failed to write index: {e}")
        
        rebuild_time = time.time() - self._rebuild_start_time
        entries_after = len(blocks)
        entries_added = max(0, entries_after - self._entries_before)
        entries_removed = max(0, self._entries_before - entries_after)
        
        return {
            "healthy": True,
            "index_entries": entries_after,
            "block_count": len(block_files),
            "rebuild_time_seconds": rebuild_time,
            "entries_added": entries_added,
            "entries_removed": entries_removed,
            "index_size_bytes": index_size,
        }
    
    def get_status(self) -> str:
        """Return human-readable status string.
        
        Returns:
            Status message indicating health or staleness.
        """
        try:
            if not self.index_path.exists():
                return "Index not found"
            
            if not self.blocks_dir.exists():
                return "Blocks directory not found"
            
            with open(self.index_path, 'r') as f:
                index_data = json.load(f)
            
            block_count = len(list(self.blocks_dir.iterdir()))
            indexed_blocks = len(index_data.get("blocks", {}))
            
            if block_count == indexed_blocks:
                mtime = self.index_path.stat().st_mtime
                mtime_str = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                return f"Index is current ({indexed_blocks} entries, last modified {mtime_str})"
            else:
                gap = abs(block_count - indexed_blocks)
                if block_count > indexed_blocks:
                    return f"Index is stale: {indexed_blocks} indexed vs {block_count} blocks ({gap} missing)"
                else:
                    return f"Index has extra entries: {indexed_blocks} indexed vs {block_count} blocks ({gap} extra)"
        except Exception as e:
            return f"Error checking status: {e}"
