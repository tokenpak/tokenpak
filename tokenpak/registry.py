"""Block Registry — SQLite-backed content versioning and tracking."""

import hashlib
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


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


class BlockRegistry:
    """SQLite-backed registry for tracking processed content blocks."""

    def __init__(self, db_path: str = ".tokenpak/registry.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        conn = self._connect()
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
                processed_at REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_type ON blocks(file_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_hash ON blocks(content_hash)")
        conn.commit()
        conn.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def has_changed(self, path: str, content: str) -> bool:
        """Check if file content has changed since last processing."""
        new_hash = hashlib.sha256(content.encode()).hexdigest()
        conn = self._connect()
        row = conn.execute(
            "SELECT content_hash FROM blocks WHERE path = ?", (path,)
        ).fetchone()
        conn.close()
        if row is None:
            return True  # New file
        return row[0] != new_hash

    def add_block(self, block: Block) -> Block:
        """Add or update a block in the registry."""
        conn = self._connect()
        existing = conn.execute(
            "SELECT version FROM blocks WHERE path = ?", (block.path,)
        ).fetchone()

        if existing:
            block.version = existing[0] + 1
            conn.execute("""
                UPDATE blocks SET
                    content_hash = ?, version = ?, file_type = ?,
                    raw_tokens = ?, compressed_tokens = ?,
                    compressed_content = ?, quality_score = ?,
                    importance = ?, processed_at = ?
                WHERE path = ?
            """, (
                block.content_hash, block.version, block.file_type,
                block.raw_tokens, block.compressed_tokens,
                block.compressed_content, block.quality_score,
                block.importance, block.processed_at, block.path
            ))
        else:
            block.version = 1
            conn.execute("""
                INSERT INTO blocks
                    (path, content_hash, version, file_type, raw_tokens,
                     compressed_tokens, compressed_content, quality_score,
                     importance, processed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                block.path, block.content_hash, block.version, block.file_type,
                block.raw_tokens, block.compressed_tokens,
                block.compressed_content, block.quality_score,
                block.importance, block.processed_at
            ))

        conn.commit()
        conn.close()
        return block

    def get_block(self, path: str) -> Optional[Block]:
        """Retrieve a block by path."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM blocks WHERE path = ?", (path,)).fetchone()
        conn.close()
        if not row:
            return None
        return Block(**dict(row))

    def list_blocks(self, file_type: str = None) -> list[Block]:
        """List all blocks, optionally filtered by type."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        if file_type:
            rows = conn.execute(
                "SELECT * FROM blocks WHERE file_type = ? ORDER BY path", (file_type,)
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM blocks ORDER BY path").fetchall()
        conn.close()
        return [Block(**dict(r)) for r in rows]

    def search(self, query: str, top_k: int = 10) -> list[Block]:
        """Simple keyword search across compressed content."""
        conn = self._connect()
        conn.row_factory = sqlite3.Row
        terms = query.lower().split()
        # Score by number of matching terms
        rows = conn.execute("SELECT * FROM blocks").fetchall()
        conn.close()

        scored = []
        for row in rows:
            block = Block(**dict(row))
            content_lower = block.compressed_content.lower()
            path_lower = block.path.lower()
            score = sum(
                1 for term in terms
                if term in content_lower or term in path_lower
            )
            if score > 0:
                scored.append((score / len(terms), block))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [block for _, block in scored[:top_k]]

    def get_stats(self) -> dict:
        """Get registry statistics."""
        conn = self._connect()
        stats = {}

        row = conn.execute("""
            SELECT COUNT(*), COALESCE(SUM(raw_tokens), 0),
                   COALESCE(SUM(compressed_tokens), 0)
            FROM blocks
        """).fetchone()
        stats["total_files"] = row[0]
        stats["total_raw_tokens"] = row[1]
        stats["total_compressed_tokens"] = row[2]
        stats["compression_ratio"] = (
            round(row[1] / row[2], 2) if row[2] > 0 else 0
        )

        type_rows = conn.execute("""
            SELECT file_type, COUNT(*), SUM(raw_tokens), SUM(compressed_tokens)
            FROM blocks GROUP BY file_type ORDER BY COUNT(*) DESC
        """).fetchall()
        stats["by_type"] = {
            r[0]: {"files": r[1], "raw_tokens": r[2], "compressed_tokens": r[3]}
            for r in type_rows
        }

        conn.close()
        return stats

    def clear(self):
        """Clear all blocks."""
        conn = self._connect()
        conn.execute("DELETE FROM blocks")
        conn.commit()
        conn.close()
