# SPDX-License-Identifier: MIT
"""Block Registry — SQLite-backed content versioning with optimized I/O.

Hardened for stability:
- Connection pooling with thread-local storage
- WAL mode + busy timeout for concurrent access
- Explicit cleanup hooks
- Graceful error recovery
"""

import atexit
import hashlib
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Any, TYPE_CHECKING, Generator, List, Optional, cast

if TYPE_CHECKING:
    pass

# Global registry of all instances for cleanup
_REGISTRIES: List["BlockRegistry"] = []
_CLEANUP_REGISTERED = False


def _cleanup_all_registries() -> None:
    """Cleanup hook for process exit."""
    for reg in _REGISTRIES:
        try:
            reg.close()
        except Exception:
            pass


@dataclass
class Block:
    """A processed content block."""

    path: str
    content_hash: str
    version: int
    file_type: str
    raw_tokens: int
    compressed_tokens: int
    compressed_content: str
    quality_score: float = 1.0
    importance: float = 5.0
    processed_at: float = field(default_factory=time.time)
    slice_id: str = ""
    provenance: Optional[object] = None  # Optional[Provenance] — avoid circular import

    def __post_init__(self) -> None:
        """Auto-generate slice_id if not provided."""
        if not self.slice_id:
            digest = hashlib.sha256(f"{self.path}:{self.content_hash}".encode()).hexdigest()[:8]
            self.slice_id = f"s_{digest}"


class BlockRegistry:
    """
    SQLite-backed registry with connection pooling and batch transactions.

    Optimizations:
    - Connection pooling (reuse instead of open/close per operation)
    - WAL mode for better concurrent read/write
    - Batch transaction context manager
    - Busy timeout for lock contention
    - Prepared statement caching (SQLite handles this)

    Stability:
    - Thread-local connections
    - Graceful cleanup on exit
    - Error recovery in transactions
    """

    def __init__(self, db_path: str = ".tokenpak/registry.db"):
        global _CLEANUP_REGISTERED

        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._closed = False
        self._init_db()

        # Register for cleanup
        _REGISTRIES.append(self)
        if not _CLEANUP_REGISTERED:
            atexit.register(_cleanup_all_registries)
            _CLEANUP_REGISTERED = True

    def _get_connection(self) -> sqlite3.Connection:
        """Get thread-local connection (pooling)."""
        if self._closed:
            raise RuntimeError("Registry is closed")

        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(
                self.db_path,
                check_same_thread=False,
                timeout=30.0,  # 30s busy timeout for lock contention
            )
            # Performance pragmas
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
            conn.execute("PRAGMA busy_timeout=30000")  # 30s busy timeout
            conn.execute("PRAGMA temp_store=MEMORY")
            conn.execute("PRAGMA mmap_size=268435456")  # 256MB mmap
            self._local.conn = conn
        conn_obj: sqlite3.Connection = self._local.conn
        return conn_obj

    def _init_db(self) -> None:
        conn = self._get_connection()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS blocks (
                path TEXT PRIMARY KEY,
                content_hash TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                file_type TEXT NOT NULL,
                raw_tokens INTEGER NOT NULL,
                compressed_tokens INTEGER NOT NULL,
                compressed_content TEXT NOT NULL,
                quality_score REAL NOT NULL DEFAULT 1.0,
                importance REAL NOT NULL DEFAULT 5.0,
                processed_at REAL NOT NULL,
                slice_id TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_type ON blocks(file_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hash ON blocks(content_hash)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_slice ON blocks(slice_id)")
        # Migration: add slice_id column to existing DBs
        try:
            conn.execute("ALTER TABLE blocks ADD COLUMN slice_id TEXT NOT NULL DEFAULT ''")
        except Exception:
            pass  # Column already exists
        conn.commit()

    @contextmanager
    def batch_transaction(self) -> Generator[sqlite3.Connection, None, None]:
        """
        Context manager for batched writes.

        All operations within the context share one transaction,
        committing only at the end. ~60% faster for bulk indexing.

        Usage:
            with registry.batch_transaction() as conn:
                for file in files:
                    registry.add_block_batch(block, conn)
        """
        conn = self._get_connection()
        try:
            conn.execute("BEGIN IMMEDIATE")  # Acquire write lock early
            yield conn
            conn.commit()
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            raise

    def has_changed(self, path: str, content: str) -> bool:
        """Check if file content has changed since last processing."""
        new_hash = hashlib.sha256(content.encode()).hexdigest()
        conn = self._get_connection()
        row = conn.execute("SELECT content_hash FROM blocks WHERE path = ?", (path,)).fetchone()
        if row is None:
            return True  # New file
        existing_hash: str = row[0]
        return existing_hash != new_hash

    def add_block(self, block: Block) -> Block:
        """Add or update a block (auto-commit per call)."""
        conn = self._get_connection()
        block = self._upsert_block(block, conn)
        conn.commit()
        return block

    def add_block_batch(self, block: Block, conn: sqlite3.Connection) -> Block:
        """Add or update a block within a batch transaction (no auto-commit)."""
        return self._upsert_block(block, conn)

    def _upsert_block(self, block: Block, conn: sqlite3.Connection) -> Block:
        """Internal upsert logic using INSERT OR REPLACE for atomicity."""
        # Check existing version
        existing = conn.execute(
            "SELECT version FROM blocks WHERE path = ?", (block.path,)
        ).fetchone()

        if existing:
            block.version = existing[0] + 1
        else:
            block.version = 1

        # Use INSERT OR REPLACE for atomic upsert
        conn.execute(
            """
            INSERT OR REPLACE INTO blocks
                (path, content_hash, version, file_type, raw_tokens,
                 compressed_tokens, compressed_content, quality_score,
                 importance, processed_at, slice_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                block.path,
                block.content_hash,
                block.version,
                block.file_type,
                block.raw_tokens,
                block.compressed_tokens,
                block.compressed_content,
                block.quality_score,
                block.importance,
                block.processed_at,
                block.slice_id,
            ),
        )

        return block

    def get_block(self, path: str) -> Optional[Block]:
        """Retrieve a block by path."""
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM blocks WHERE path = ?", (path,)).fetchone()
        conn.row_factory = None
        if not row:
            return None
        return Block(**dict(row))

    def list_blocks(self, file_type: Optional[str] = None) -> List[Block]:
        """List all blocks, optionally filtered by type."""
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        if file_type:
            rows = conn.execute(
                "SELECT * FROM blocks WHERE file_type = ? ORDER BY path", (file_type,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM blocks ORDER BY path").fetchall()
        conn.row_factory = None
        return [Block(**dict(r)) for r in rows]

    def search(self, query: str, top_k: int = 10) -> List[Block]:
        """Simple keyword search across compressed content."""
        conn = self._get_connection()
        conn.row_factory = sqlite3.Row
        terms = query.lower().split()
        if not terms:
            return []

        rows = conn.execute("SELECT * FROM blocks").fetchall()
        conn.row_factory = None

        scored = []
        for row in rows:
            block = Block(**dict(row))
            content_lower = block.compressed_content.lower()
            path_lower = block.path.lower()
            score = sum(1 for term in terms if term in content_lower or term in path_lower)
            if score > 0:
                scored.append((score / len(terms), block))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [block for _, block in scored[:top_k]]

    def get_stats(self) -> Dict[str, Any]:
        """Get registry statistics."""
        conn = self._get_connection()
        stats = {}

        row = conn.execute("""
            SELECT COUNT(*), COALESCE(SUM(raw_tokens), 0),
                   COALESCE(SUM(compressed_tokens), 0)
            FROM blocks
        """).fetchone()
        stats["total_files"] = row[0]
        stats["total_raw_tokens"] = row[1]
        stats["total_compressed_tokens"] = row[2]
        stats["compression_ratio"] = round(row[1] / row[2], 2) if row[2] > 0 else 0

        type_rows = conn.execute("""
            SELECT file_type, COUNT(*), SUM(raw_tokens), SUM(compressed_tokens)
            FROM blocks GROUP BY file_type ORDER BY COUNT(*) DESC
        """).fetchall()
        stats["by_type"] = {
            r[0]: {"files": r[1], "raw_tokens": r[2], "compressed_tokens": r[3]} for r in type_rows
        }

        return stats

    def clear(self) -> None:
        """Clear all blocks."""
        conn = self._get_connection()
        conn.execute("DELETE FROM blocks")
        conn.commit()

    def close(self) -> None:
        """Close the connection pool."""
        if self._closed:
            return
        self._closed = True
        if hasattr(self._local, "conn") and self._local.conn:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

        # Remove from global registry
        try:
            _REGISTRIES.remove(self)
        except ValueError:
            pass

    def __enter__(self) -> "BlockRegistry":
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.close()
