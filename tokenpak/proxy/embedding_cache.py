"""tokenpak.proxy.embedding_cache — SQLite-backed embedding cache.

Keyed by SHA256(model + str(dims) + text). WAL mode for concurrent reads.
TTL and max-size enforcement with LRU eviction.

Environment:
    TOKENPAK_EMBEDDING_CACHE_TTL_DAYS  — entry lifetime in days (default 7)
    TOKENPAK_EMBEDDING_CACHE_MAX_MB    — max DB size in megabytes (default 100)
"""

import hashlib
import os
import sqlite3
import time
from typing import Optional


_DEFAULT_DB_PATH = os.path.join(
    os.path.expanduser("~"), ".tokenpak", "embedding_cache.db"
)
_DEFAULT_TTL_DAYS = int(os.environ.get("TOKENPAK_EMBEDDING_CACHE_TTL_DAYS", "7"))
_DEFAULT_MAX_MB = int(os.environ.get("TOKENPAK_EMBEDDING_CACHE_MAX_MB", "100"))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cache (
    key        TEXT PRIMARY KEY,
    model      TEXT NOT NULL,
    dims       INT  NOT NULL,
    embedding  BLOB NOT NULL,
    tokens     INT  NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_created_at ON cache (created_at);
"""


def _cache_key(model: str, dims: int, text: str) -> str:
    raw = model + str(dims) + text
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


class EmbeddingCache:
    """SQLite-backed cache for embedding vectors.

    Each public method opens and closes its own connection (connection-per-call).
    WAL mode is set on the first connection to the database.
    """

    def __init__(
        self,
        db_path: str = _DEFAULT_DB_PATH,
        ttl_days: int = _DEFAULT_TTL_DAYS,
        max_mb: int = _DEFAULT_MAX_MB,
    ) -> None:
        self.db_path = db_path
        self.ttl_days = ttl_days
        self.max_mb = max_mb
        self._init_db()

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
        """Return cached embedding bytes or None on miss/bypass.

        Args:
            model:    Embedding model name.
            dims:     Number of dimensions requested (0 = model default).
            text:     Input text that was embedded.
            no_cache: When True (e.g. Cache-Control: no-cache) skip cache lookup.

        Returns:
            Raw response JSON bytes if cached and not expired, else None.
        """
        if no_cache:
            return None

        key = _cache_key(model, dims, text)
        cutoff = int(time.time()) - self.ttl_days * 86400

        with self._connect() as conn:
            row = conn.execute(
                "SELECT embedding FROM cache WHERE key = ? AND created_at >= ?",
                (key, cutoff),
            ).fetchone()

        return bytes(row[0]) if row else None

    def put(
        self,
        model: str,
        dims: int,
        text: str,
        response_json: bytes,
        tokens: int,
    ) -> None:
        """Store an embedding response in the cache.

        After inserting, runs TTL expiry and size-based eviction.

        Args:
            model:         Embedding model name.
            dims:          Number of dimensions.
            text:          Input text that was embedded.
            response_json: Raw upstream response bytes to cache.
            tokens:        Token count for the request.
        """
        key = _cache_key(model, dims, text)
        now = int(time.time())

        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cache (key, model, dims, embedding, tokens, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (key, model, dims, response_json, tokens, now),
            )

        self._expire()
        self._evict()

    # ------------------------------------------------------------------
    # Maintenance helpers
    # ------------------------------------------------------------------

    def _expire(self) -> int:
        """Delete entries older than ttl_days. Returns count removed."""
        cutoff = int(time.time()) - self.ttl_days * 86400
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM cache WHERE created_at < ?", (cutoff,))
            return cur.rowcount

    def _evict(self) -> int:
        """Delete oldest entries until DB is under max_mb. Returns count removed."""
        max_bytes = self.max_mb * 1024 * 1024
        removed = 0

        with self._connect() as conn:
            while True:
                size_row = conn.execute(
                    "SELECT page_count * page_size FROM pragma_page_count(), pragma_page_size()"
                ).fetchone()
                if size_row is None:
                    break
                db_size = size_row[0]
                if db_size <= max_bytes:
                    break
                # Delete the oldest single entry
                deleted = conn.execute(
                    "DELETE FROM cache WHERE key = (SELECT key FROM cache ORDER BY created_at ASC LIMIT 1)"
                ).rowcount
                if deleted == 0:
                    break
                removed += deleted

        return removed

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open a new SQLite connection in WAL mode with autocommit context."""
        conn = sqlite3.connect(self.db_path, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        """Create schema if not present and ensure WAL mode is set."""
        db_dir = os.path.dirname(self.db_path)
        if db_dir:
            os.makedirs(db_dir, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
