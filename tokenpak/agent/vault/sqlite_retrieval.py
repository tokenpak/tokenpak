"""
TokenPak — SQLite Retrieval Backend for proxy_v4
=================================================

Provides a drop-in alternative to the in-memory JSON/blocks BM25 backend.
Stores block content and precomputed BM25 stats in SQLite for faster
load times and incremental updates at larger index scales.

Usage (via env var in proxy_v4.py)::

    TOKENPAK_RETRIEVAL_BACKEND=sqlite  # or json_blocks (default)

The SQLite DB is stored alongside the vault index at::

    <TOKENPAK_VAULT_INDEX>/retrieval.db

Incremental update strategy:
- On each reload check, compare index.json mtime vs db checkpoint mtime.
- If index.json is newer, rebuild only changed/new/deleted blocks.
- Checkpoint mtime is stored in the ``meta`` table.

Schema::

    blocks(block_id TEXT PK, source_path TEXT, risk_class TEXT,
           must_keep INT, raw_tokens INT, content TEXT,
           term_count INT)

    block_terms(block_id TEXT, term TEXT, tf INT)
               INDEX(term) for IDF lookups

    doc_stats(id INT PK, doc_count INT, total_dl INT, avg_dl REAL)

    meta(key TEXT PK, value TEXT)
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Dict, List, Tuple

# ---------------------------------------------------------------------------
# BM25 tokenizer (mirrors proxy_v4._bm25_tokenize)
# ---------------------------------------------------------------------------


def _bm25_tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


# ---------------------------------------------------------------------------
# SQLiteRetrievalBackend
# ---------------------------------------------------------------------------


class SQLiteRetrievalBackend:
    """SQLite-backed BM25 retrieval for proxy_v4 vault injection.

    Implements the same public interface as ``VaultIndex``:
      - ``available: bool``
      - ``maybe_reload()``
      - ``search(query, top_k, min_score) -> [(block, score)]``
      - ``compile_injection(query, budget, top_k, min_score) -> (text, tokens, refs)``

    Block count and token count are exposed for metrics parity.
    """

    DB_VERSION = 1

    def __init__(self, tokenpak_dir: str):
        self.tokenpak_dir = Path(tokenpak_dir)
        self.db_path = self.tokenpak_dir / "retrieval.db"
        self._lock = threading.Lock()
        self._last_checked = 0.0
        self._block_count = 0
        self._token_count = 0
        self._check_interval = 60  # seconds between mtime checks
        self._initialized = False

    # ------------------------------------------------------------------
    # Public interface (mirrors VaultIndex)
    # ------------------------------------------------------------------

    @property
    def available(self) -> bool:
        return self._initialized and self._block_count > 0

    @property
    def block_count(self) -> int:
        return self._block_count

    @property
    def token_count(self) -> int:
        return self._token_count

    def maybe_reload(self):
        """Check if the vault index has changed and rebuild if necessary."""
        now = time.time()
        if now - self._last_checked < self._check_interval:
            return
        self._last_checked = now

        index_path = self.tokenpak_dir / "index.json"
        if not index_path.exists():
            return

        try:
            index_mtime = index_path.stat().st_mtime
        except OSError:
            return

        db_checkpoint = self._get_checkpoint()
        if not self._initialized or index_mtime > db_checkpoint:
            self._rebuild_incremental(index_path, index_mtime)

    def search(
        self,
        query: str,
        top_k: int = 5,
        min_score: float = 2.0,
    ) -> List[Tuple[dict, float]]:
        """BM25 search. Returns [(block_dict, score), ...] sorted deterministically."""
        if not self.available:
            return []

        query_terms = list(set(_bm25_tokenize(query)))
        if not query_terms:
            return []

        with self._lock:
            try:
                conn = self._connect()
                return self._bm25_search(conn, query_terms, top_k, min_score)
            except sqlite3.Error as e:
                print(f"  ⚠️ SQLite retrieval search error: {e}")
                return []

    def compile_injection(
        self,
        query: str,
        budget: int = 4000,
        top_k: int = 5,
        min_score: float = 2.0,
    ) -> Tuple[str, int, List[str]]:
        """Search and compile injection text within token budget.

        Returns (injection_text, tokens_used, source_refs).
        Mirrors VaultIndex.compile_injection exactly for cache stability.
        """
        results = self.search(query, top_k=top_k, min_score=min_score)
        if not results:
            return "", 0, []

        # Import count_tokens lazily to avoid circular imports
        try:
            from tokenpak.tokens import count_tokens as _count_tokens

            count_tokens_fn = _count_tokens
        except ImportError:

            def count_tokens_fn(t: str) -> int:  # type: ignore[misc]
                return max(1, len(t) // 4)

        injection_parts: List[str] = []
        tokens_used = 0
        source_refs: List[str] = []

        for block, score in results:
            content = block["content"]
            block_tokens = block.get("raw_tokens", count_tokens_fn(content))

            remaining = budget - tokens_used
            if remaining <= 100:
                break

            if block_tokens > remaining:
                char_limit = remaining * 4
                content = content[:char_limit].rsplit("\n", 1)[0]
                block_tokens = count_tokens_fn(content)

            source_path = block["source_path"]
            injection_parts.append(f"--- [{source_path}] (relevance: {score:.1f}) ---\n{content}")
            tokens_used += block_tokens
            source_refs.append(source_path)

        if not injection_parts:
            return "", 0, []

        header = "\n\n## Retrieved Context\n"
        injection_text = header + "\n\n".join(injection_parts)
        tokens_used = count_tokens_fn(injection_text)

        return injection_text, tokens_used, source_refs

    # ------------------------------------------------------------------
    # Internal — DB init + schema
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-16000")  # 16 MB page cache
        return conn

    def _ensure_schema(self, conn: sqlite3.Connection):
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS blocks (
                block_id    TEXT PRIMARY KEY,
                source_path TEXT NOT NULL,
                risk_class  TEXT DEFAULT 'narrative',
                must_keep   INTEGER DEFAULT 0,
                raw_tokens  INTEGER DEFAULT 0,
                content     TEXT NOT NULL DEFAULT '',
                term_count  INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS block_terms (
                block_id TEXT NOT NULL,
                term     TEXT NOT NULL,
                tf       INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (block_id, term)
            );
            CREATE INDEX IF NOT EXISTS idx_block_terms_term ON block_terms(term);

            CREATE TABLE IF NOT EXISTS doc_stats (
                id         INTEGER PRIMARY KEY CHECK (id = 1),
                doc_count  INTEGER NOT NULL DEFAULT 0,
                total_dl   INTEGER NOT NULL DEFAULT 0,
                avg_dl     REAL NOT NULL DEFAULT 0.0
            );
            INSERT OR IGNORE INTO doc_stats (id, doc_count, total_dl, avg_dl)
            VALUES (1, 0, 0, 0.0);

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
        """)
        conn.commit()

    # ------------------------------------------------------------------
    # Internal — checkpoint management
    # ------------------------------------------------------------------

    def _get_checkpoint(self) -> float:
        """Return the mtime of the last successful build, or 0.0."""
        if not self.db_path.exists():
            return 0.0
        try:
            conn = self._connect()
            self._ensure_schema(conn)
            row = conn.execute("SELECT value FROM meta WHERE key='index_mtime'").fetchone()
            conn.close()
            return float(row[0]) if row else 0.0
        except sqlite3.Error:
            return 0.0

    def _set_checkpoint(self, conn: sqlite3.Connection, mtime: float):
        conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('index_mtime', ?)",
            (str(mtime),),
        )

    # ------------------------------------------------------------------
    # Internal — incremental rebuild
    # ------------------------------------------------------------------

    def _rebuild_incremental(self, index_path: Path, mtime: float):
        """Load index.json, diff against DB, upsert changed blocks, prune deleted."""
        try:
            data = json.loads(index_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ⚠️ SQLite backend: index read error: {e}")
            return

        raw_blocks = data.get("blocks", {})
        if not isinstance(raw_blocks, dict):
            return

        blocks_dir = self.tokenpak_dir / "blocks"
        conn = self._connect()

        with self._lock:
            try:
                self._ensure_schema(conn)

                # Existing block_ids in DB
                existing_ids = {
                    r[0] for r in conn.execute("SELECT block_id FROM blocks").fetchall()
                }
                new_ids = set(raw_blocks.keys())

                # Blocks to delete (removed from index)
                deleted = existing_ids - new_ids
                if deleted:
                    placeholders = ",".join("?" * len(deleted))
                    conn.execute(
                        f"DELETE FROM blocks WHERE block_id IN ({placeholders})",
                        list(deleted),
                    )
                    conn.execute(
                        f"DELETE FROM block_terms WHERE block_id IN ({placeholders})",
                        list(deleted),
                    )

                # Blocks to upsert
                added = 0
                for bid, bdata in raw_blocks.items():
                    content_file = blocks_dir / f"{bid}.txt"
                    try:
                        content = (
                            content_file.read_text(errors="replace")
                            if content_file.exists()
                            else ""
                        )
                    except OSError:
                        content = ""

                    terms = _bm25_tokenize(content)
                    tf: Dict[str, int] = {}
                    for t in terms:
                        tf[t] = tf.get(t, 0) + 1

                    conn.execute(
                        """INSERT OR REPLACE INTO blocks
                           (block_id, source_path, risk_class, must_keep, raw_tokens, content, term_count)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            bid,
                            bdata.get("source_path", bid),
                            bdata.get("risk_class", "narrative"),
                            int(bdata.get("must_keep", False)),
                            bdata.get("raw_tokens", 0),
                            content,
                            len(terms),
                        ),
                    )
                    # Replace term frequencies
                    conn.execute("DELETE FROM block_terms WHERE block_id=?", (bid,))
                    if tf:
                        conn.executemany(
                            "INSERT INTO block_terms (block_id, term, tf) VALUES (?, ?, ?)",
                            [(bid, term, freq) for term, freq in tf.items()],
                        )
                    added += 1

                # Update global stats
                doc_count = len(new_ids)
                total_dl_row = conn.execute(
                    "SELECT COALESCE(SUM(term_count), 0) FROM blocks"
                ).fetchone()
                total_dl = total_dl_row[0] if total_dl_row else 0
                avg_dl = total_dl / doc_count if doc_count > 0 else 0.0

                conn.execute(
                    """INSERT OR REPLACE INTO doc_stats (id, doc_count, total_dl, avg_dl)
                       VALUES (1, ?, ?, ?)""",
                    (doc_count, total_dl, avg_dl),
                )
                self._set_checkpoint(conn, mtime)
                conn.commit()

                # Update cached counters
                self._block_count = doc_count
                token_row = conn.execute(
                    "SELECT COALESCE(SUM(raw_tokens), 0) FROM blocks"
                ).fetchone()
                self._token_count = token_row[0] if token_row else 0
                self._initialized = True

                print(
                    f"  📚 SQLite vault backend: {doc_count} blocks "
                    f"({added} upserted, {len(deleted)} removed), "
                    f"{self._token_count:,} tokens"
                )
            except sqlite3.Error as e:
                print(f"  ⚠️ SQLite backend rebuild error: {e}")
                conn.rollback()
            finally:
                conn.close()

    # ------------------------------------------------------------------
    # Internal — BM25 search
    # ------------------------------------------------------------------

    def _bm25_search(
        self,
        conn: sqlite3.Connection,
        query_terms: List[str],
        top_k: int,
        min_score: float,
    ) -> List[Tuple[dict, float]]:
        """Execute BM25 retrieval via SQL aggregation."""
        k1 = 1.5
        b_param = 0.75

        # Load global stats
        stats_row = conn.execute("SELECT doc_count, avg_dl FROM doc_stats WHERE id=1").fetchone()
        if not stats_row or stats_row[0] == 0:
            return []
        doc_count, avg_dl = stats_row

        if avg_dl == 0:
            return []

        # Per-term DF lookup
        placeholders = ",".join("?" * len(query_terms))
        df_rows = conn.execute(
            f"SELECT term, COUNT(DISTINCT block_id) FROM block_terms "
            f"WHERE term IN ({placeholders}) GROUP BY term",
            query_terms,
        ).fetchall()
        df: Dict[str, int] = {r[0]: r[1] for r in df_rows}

        # Filter to terms that actually appear
        active_terms = [t for t in query_terms if t in df]
        if not active_terms:
            return []

        # Phase 1: Score-only pass — no content fetch (fast)
        placeholders2 = ",".join("?" * len(active_terms))
        tf_rows = conn.execute(
            f"""SELECT bt.block_id, bt.term, bt.tf, b.term_count, b.source_path
                FROM block_terms bt
                JOIN blocks b ON bt.block_id = b.block_id
                WHERE bt.term IN ({placeholders2})""",
            active_terms,
        ).fetchall()

        # Aggregate per-block scores (no content loaded yet)
        block_scores: Dict[str, float] = {}
        block_source: Dict[str, str] = {}

        for row in tf_rows:
            bid, term, tf_val, dl, source_path = row
            df_val = df.get(term, 0)
            if df_val == 0:
                continue

            idf = math.log((doc_count - df_val + 0.5) / (df_val + 0.5) + 1)
            norm_dl = dl if dl > 0 else avg_dl
            numerator = tf_val * (k1 + 1)
            denominator = tf_val + k1 * (1 - b_param + b_param * norm_dl / avg_dl)
            term_score = idf * numerator / denominator

            block_scores[bid] = block_scores.get(bid, 0.0) + term_score
            block_source.setdefault(bid, source_path)

        # Filter by min_score, sort deterministically (score desc, path asc, id asc)
        ranked = sorted(
            ((bid, score) for bid, score in block_scores.items() if score >= min_score),
            key=lambda x: (
                -x[1],
                block_source.get(x[0], ""),
                x[0],
            ),
        )[:top_k]

        if not ranked:
            return []

        # Phase 2: Fetch full block data only for top_k results
        top_ids = [bid for bid, _ in ranked]
        top_placeholders = ",".join("?" * len(top_ids))
        detail_rows = conn.execute(
            f"""SELECT block_id, source_path, risk_class, must_keep, raw_tokens, content
                FROM blocks WHERE block_id IN ({top_placeholders})""",
            top_ids,
        ).fetchall()
        detail_map = {
            r[0]: {
                "block_id": r[0],
                "source_path": r[1],
                "risk_class": r[2],
                "must_keep": bool(r[3]),
                "raw_tokens": r[4],
                "content": r[5],
            }
            for r in detail_rows
        }

        return [(detail_map[bid], score) for bid, score in ranked if bid in detail_map]

    # ------------------------------------------------------------------
    # Compatibility — blocks dict (used by debug endpoints / metrics)
    # ------------------------------------------------------------------

    @property
    def blocks(self) -> Dict[str, dict]:
        """Return all blocks as a dict for compatibility with VaultIndex callers.

        NOTE: This loads all block content from SQLite — use for debug/metrics
        endpoints only, not hot-path retrieval.
        """
        if not self.db_path.exists():
            return {}
        try:
            conn = self._connect()
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT block_id, source_path, risk_class, must_keep, raw_tokens, content "
                "FROM blocks"
            ).fetchall()
            conn.close()
            return {
                r["block_id"]: {
                    "block_id": r["block_id"],
                    "source_path": r["source_path"],
                    "risk_class": r["risk_class"],
                    "must_keep": bool(r["must_keep"]),
                    "raw_tokens": r["raw_tokens"],
                    "content": r["content"],
                }
                for r in rows
            }
        except sqlite3.Error:
            return {}
