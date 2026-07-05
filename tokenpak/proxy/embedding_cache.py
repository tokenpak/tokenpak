"""
Embedding cache backed by SQLite in WAL mode.

Key:    SHA256(model + str(dimensions) + input_text)
TTL:    TOKENPAK_EMBEDDING_CACHE_TTL_DAYS  (default 7)
MaxMB:  TOKENPAK_EMBEDDING_CACHE_MAX_MB   (default 100)
Evict:  oldest rows deleted when the *logical* content size exceeds max_mb.
        The DB file size (page_count) is deliberately NOT the signal: SQLite
        never shrinks page_count on DELETE, so a file-size loop would empty
        the whole cache on every put once the file had ever crossed the cap.
Bypass: get(..., no_cache=True) always returns None
"""

import hashlib
import os
import sqlite3
import time
from typing import Optional

_DEFAULT_TTL_DAYS = int(os.environ.get("TOKENPAK_EMBEDDING_CACHE_TTL_DAYS", "7"))
_DEFAULT_MAX_MB = int(os.environ.get("TOKENPAK_EMBEDDING_CACHE_MAX_MB", "100"))

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    model      TEXT NOT NULL,
    dims       INT  NOT NULL,
    embedding  BLOB NOT NULL,
    tokens     INT  NOT NULL,
    created_at INTEGER NOT NULL
)
"""

_CREATE_IDX = "CREATE INDEX IF NOT EXISTS idx_created ON cache (created_at)"

# Rough per-row cost of the non-embedding columns (key hash, model name,
# ints, index entry). Only used to budget logical size for eviction.
_ROW_OVERHEAD_BYTES = 256


def _cache_key(model: str, dims: int, text: str) -> str:
    raw = model + str(dims) + text
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _connect(db_path: str) -> sqlite3.Connection:
    con = sqlite3.connect(db_path, timeout=10)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(_CREATE_TABLE)
    con.execute(_CREATE_IDX)
    con.commit()
    return con


class EmbeddingCache:
    """SQLite-backed cache for embedding responses.

    Each public method opens and closes its own connection (connection-per-call
    pattern) so concurrent readers don't block each other under WAL mode.
    """

    def __init__(
        self,
        db_path: str,
        ttl_days: int = _DEFAULT_TTL_DAYS,
        max_mb: int = _DEFAULT_MAX_MB,
    ) -> None:
        self.db_path = db_path
        self.ttl_days = ttl_days
        self.max_mb = max_mb
        # Ensure schema exists on first construction.
        con = _connect(db_path)
        con.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(
        self,
        model: str,
        dims: int,
        text: str,
        *,
        no_cache: bool = False,
    ) -> Optional[bytes]:
        """Return cached embedding bytes, or None on miss / bypass / expiry."""
        if no_cache:
            return None

        key = _cache_key(model, dims, text)
        cutoff = int(time.time()) - self.ttl_days * 86400

        con = _connect(self.db_path)
        try:
            row = con.execute(
                "SELECT embedding FROM cache WHERE key = ? AND created_at > ?",
                (key, cutoff),
            ).fetchone()
            return row[0] if row else None
        finally:
            con.close()

    def put(
        self,
        model: str,
        dims: int,
        text: str,
        response_json: bytes,
        tokens: int,
    ) -> None:
        """Store an embedding response, then enforce TTL and size limits."""
        key = _cache_key(model, dims, text)
        now = int(time.time())

        con = _connect(self.db_path)
        try:
            con.execute(
                """
                INSERT OR REPLACE INTO cache (key, model, dims, embedding, tokens, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (key, model, dims, response_json, tokens, now),
            )
            con.commit()
        finally:
            con.close()

        self._expire()
        self._evict()

    # ------------------------------------------------------------------
    # Internal maintenance
    # ------------------------------------------------------------------

    def _expire(self) -> None:
        """Delete entries older than ttl_days."""
        cutoff = int(time.time()) - self.ttl_days * 86400
        con = _connect(self.db_path)
        try:
            con.execute("DELETE FROM cache WHERE created_at <= ?", (cutoff,))
            con.commit()
        finally:
            con.close()

    def _evict(self) -> None:
        """Delete oldest rows until the logical cache size is under max_mb.

        Sizing: SQLite's ``page_count`` never shrinks on DELETE (freed pages
        are recycled, not returned to the OS, absent VACUUM/auto_vacuum), so
        the DB *file* size is a one-way ratchet and must not drive eviction —
        once the file crossed max_mb, a file-size loop could only terminate
        at row count zero, wiping the entire cache on every subsequent put.
        Track the logical content size (embedding bytes + per-row overhead)
        instead.

        Concurrency: the measure-and-delete pass runs in one IMMEDIATE
        transaction so concurrent ``put()`` calls serialise against the purge
        (WAL mode + busy timeout) instead of racing it. Eviction is
        best-effort maintenance — if the write lock cannot be acquired the
        pass is skipped rather than failing the caller's put.
        """
        max_bytes = self.max_mb * 1024 * 1024
        con = _connect(self.db_path)
        try:
            try:
                con.execute("BEGIN IMMEDIATE")
                total_bytes, count = con.execute(
                    "SELECT COALESCE(SUM(LENGTH(embedding)), 0) + COUNT(*) * ?,"
                    " COUNT(*) FROM cache",
                    (_ROW_OVERHEAD_BYTES,),
                ).fetchone()
                if count == 0 or total_bytes <= max_bytes:
                    con.commit()
                    return
                excess = total_bytes - max_bytes
                # Oldest first; rowid breaks created_at ties in insertion
                # order so the newest rows always survive.
                rows = con.execute(
                    "SELECT key, LENGTH(embedding) + ? FROM cache"
                    " ORDER BY created_at ASC, rowid ASC",
                    (_ROW_OVERHEAD_BYTES,),
                ).fetchall()
                freed = 0
                doomed = []
                for key, row_bytes in rows:
                    if freed >= excess:
                        break
                    doomed.append((key,))
                    freed += row_bytes
                con.executemany("DELETE FROM cache WHERE key = ?", doomed)
                con.commit()
            except sqlite3.OperationalError:
                # Lock contention past the busy timeout: skip this pass.
                con.rollback()
        finally:
            con.close()
