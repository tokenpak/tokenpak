"""VaultIndex — BM25-searchable index for .tokenpak/index.json + blocks/.

Extracted from proxy.py for standalone testability and coverage measurement.

Public API
----------
VaultIndex
    Read-only BM25-searchable index.  Instantiate with the path to a
    .tokenpak/ directory that contains index.json and blocks/*.txt.

_bm25_tokenize(text)
    Tokenize text for BM25 search (lru_cache accelerated).
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from collections import OrderedDict
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Constants (mirror proxy.py defaults; override via env vars)
VAULT_INDEX_RELOAD_INTERVAL: int = int(os.environ.get("TOKENPAK_VAULT_INDEX_RELOAD_INTERVAL", 300))
VAULT_CACHE_MAX_BYTES: int = int(os.environ.get("TOKENPAK_VAULT_MEMORY_MAX", 256 * 1024 * 1024))
VAULT_CACHE_PRELOAD: int = int(os.environ.get("TOKENPAK_VAULT_CACHE_PRELOAD", 200))


# BM25 tokenizer — lru_cache gives 50x speedup on repeated queries (search terms repeat often)
@lru_cache(maxsize=512)
def _bm25_tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


class VaultIndex:
    """
    Read-only BM25-searchable index loaded from .tokenpak/index.json + blocks/.
    Reloads periodically to pick up git-pulled changes.
    """

    def __init__(self, tokenpak_dir: str):
        self.tokenpak_dir = Path(tokenpak_dir)
        self.blocks: Dict[str, dict] = {}  # block_id -> {meta only, no content}
        self._last_loaded = 0
        self._last_mtime = 0
        self._lock = threading.Lock()
        # BM25 precomputed
        self._df: Dict[str, int] = {}
        self._block_tfs: Dict[str, Dict[str, int]] = {}
        self._block_dl: Dict[str, int] = {}  # precomputed doc lengths (sum of tf values)
        self._avg_dl: float = 0
        self._doc_count: int = 0
        self._inverted: Dict[str, set] = {}  # term -> set(block_ids)
        # Tiered memory — LRU content cache (Tier 2)
        self._content_cache: OrderedDict = OrderedDict()  # block_id -> content str
        self._cache_bytes: int = 0
        self._max_cache_bytes: int = VAULT_CACHE_MAX_BYTES
        self._cache_hits: int = 0
        self._cache_misses: int = 0
        self._cache_evictions: int = 0
        # Background load state
        self._ready: bool = False

    @property
    def available(self) -> bool:
        return len(self.blocks) > 0

    def is_ready(self) -> bool:
        """Returns True once the vault index has completed its initial load."""
        return self._ready

    def maybe_reload(self):
        """Reload if index file changed or enough time passed."""
        now = time.time()
        if now - self._last_loaded < VAULT_INDEX_RELOAD_INTERVAL:
            return

        index_path = self.tokenpak_dir / "index.json"
        if not index_path.exists():
            return

        try:
            mtime = index_path.stat().st_mtime
            if mtime == self._last_mtime and self.blocks:
                self._last_loaded = now
                return
        except OSError:
            return

        self._load(index_path, mtime)
        self._last_loaded = now

    def _bm25_cache_path(self, index_path: Path) -> Path:
        """Return path for BM25 precomputed cache file."""
        return index_path.parent / ".bm25_cache.pkl"

    def _try_load_bm25_cache(self, index_path: Path, mtime: float):
        """
        Try to load precomputed BM25 state from cache file.
        Returns True and populates self if cache is valid (mtime matches).
        Returns False if cache is missing, stale, or corrupt.
        """
        import pickle
        cache_path = self._bm25_cache_path(index_path)
        if not cache_path.exists():
            return False
        try:
            with open(cache_path, "rb") as f:
                cached = pickle.load(f)
            if cached.get("mtime") != mtime:
                return False  # stale — index changed
            # Restore block metadata (no content stored)
            self.blocks = cached["blocks"]
            self._df = cached["df"]
            self._block_tfs = cached["block_tfs"]
            self._block_dl = cached["block_dl"]
            self._avg_dl = cached["avg_dl"]
            self._doc_count = cached["doc_count"]
            self._inverted = cached["inverted"]
            self._last_mtime = mtime
            # Rebuild LRU cache from blocks dir (preload top-N recent)
            blocks_dir = index_path.parent / "blocks"
            preload_n = VAULT_CACHE_PRELOAD
            new_cache: OrderedDict = OrderedDict()
            new_cache_bytes = 0
            if preload_n > 0:
                candidates = []
                for bid in self.blocks:
                    cf = blocks_dir / f"{bid}.txt"
                    try:
                        mt = cf.stat().st_mtime
                        candidates.append((bid, cf, mt))
                    except OSError:
                        pass
                candidates.sort(key=lambda x: -x[2])
                for bid, cf, _mt in candidates[:preload_n]:
                    try:
                        content = cf.read_text(errors="replace")
                        sz = len(content.encode("utf-8"))
                        if new_cache_bytes + sz <= self._max_cache_bytes:
                            new_cache[bid] = content
                            new_cache_bytes += sz
                    except OSError:
                        pass
            with self._lock:
                self._content_cache = new_cache
                self._cache_bytes = new_cache_bytes
                self._cache_hits = 0
                self._cache_misses = 0
                self._cache_evictions = 0
            print(
                f"  📚 Vault index loaded from BM25 cache: {self._doc_count} blocks"
                f" | cache preloaded: {len(new_cache)} blocks ({new_cache_bytes // 1024 // 1024}MB)"
            )
            self._ready = True
            return True
        except Exception as e:
            print(f"  ⚠️ BM25 cache load failed ({e}), rebuilding...")
            return False

    def _save_bm25_cache(self, index_path: Path, mtime: float):
        """Persist BM25 precomputed state to cache file for fast future loads."""
        import pickle
        cache_path = self._bm25_cache_path(index_path)
        try:
            payload = {
                "mtime": mtime,
                "blocks": self.blocks,
                "df": self._df,
                "block_tfs": self._block_tfs,
                "block_dl": self._block_dl,
                "avg_dl": self._avg_dl,
                "doc_count": self._doc_count,
                "inverted": self._inverted,
            }
            tmp = cache_path.with_suffix(".pkl.tmp")
            with open(tmp, "wb") as f:
                pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
            tmp.replace(cache_path)
            print(f"  💾 BM25 cache saved ({cache_path.stat().st_size // 1024 // 1024}MB)")
        except Exception as e:
            print(f"  ⚠️ BM25 cache save failed: {e}")

    def _load_from_sqlite(self, db_path: str) -> None:
        """Load block index from a SQLite blocks.db (fast path, ~50x vs files).

        Populates self.blocks with metadata stubs and self._content_cache with
        all content in-memory.  BM25 structures are rebuilt from the loaded
        content so search() continues to work.

        Env-flag gate (caller's responsibility):
            TOKENPAK_USE_SQLITE_BLOCKS=1  and  <tokenpak_dir>/blocks.db exists.
        """
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(db_path)
        rows = conn.execute("SELECT id, content FROM blocks").fetchall()
        conn.close()

        new_blocks: Dict[str, dict] = {}
        df: Dict[str, int] = {}
        block_tfs: Dict[str, Dict[str, int]] = {}
        block_dl: Dict[str, int] = {}
        total_dl = 0
        new_cache: OrderedDict = OrderedDict()
        new_cache_bytes = 0

        for block_id, content in rows:
            if content is None:
                content = ""
            new_blocks[block_id] = {
                "block_id": block_id,
                "source_path": block_id,
                "risk_class": "narrative",
                "must_keep": False,
                "raw_tokens": count_tokens(content),
                "_content_file": str(Path(self.tokenpak_dir) / "blocks" / f"{block_id}.txt"),
            }
            # BM25 stats
            terms = _bm25_tokenize(content)
            tf: Dict[str, int] = {}
            for t in terms:
                tf[t] = tf.get(t, 0) + 1
            dl = len(terms)
            block_tfs[block_id] = tf
            block_dl[block_id] = dl
            total_dl += dl
            for t in set(terms):
                df[t] = df.get(t, 0) + 1
            # Fill LRU cache with all content (SQLite loads everything already)
            sz = len(content.encode("utf-8"))
            if new_cache_bytes + sz <= self._max_cache_bytes:
                new_cache[block_id] = content
                new_cache_bytes += sz

        doc_count = len(new_blocks)
        avg_dl = total_dl / doc_count if doc_count > 0 else 0

        # Build inverted index
        inverted: Dict[str, set] = {}
        for bid, tf in block_tfs.items():
            for term in tf:
                if term not in inverted:
                    inverted[term] = set()
                inverted[term].add(bid)

        with self._lock:
            self.blocks = new_blocks
            self._df = df
            self._block_tfs = block_tfs
            self._block_dl = block_dl
            self._avg_dl = avg_dl
            self._doc_count = doc_count
            self._inverted = inverted
            self._content_cache = new_cache
            self._cache_bytes = new_cache_bytes
            self._cache_hits = 0
            self._cache_misses = 0
            self._cache_evictions = 0
            self._ready = True

        print(
            f"  📚 Vault index loaded from SQLite: {doc_count} blocks"
            f" | cache: {len(new_cache)} blocks ({new_cache_bytes // 1024 // 1024}MB)"
        )

    def _load(self, index_path: Path, mtime: float):
        """Load index + block contents, precompute BM25 stats."""
        import os as _os
        db_path = str(self.tokenpak_dir / "blocks.db")
        if _os.environ.get("TOKENPAK_USE_SQLITE_BLOCKS") and _os.path.exists(db_path):
            self._last_mtime = mtime
            self._load_from_sqlite(db_path)
            return

        # Fast path: load from precomputed BM25 cache if index unchanged
        if self._try_load_bm25_cache(index_path, mtime):
            return

        try:
            data = json.loads(index_path.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ⚠️ Vault index load error: {e}")
            return

        blocks_dir = self.tokenpak_dir / "blocks"
        new_blocks: Dict[str, dict] = {}

        raw_blocks = data.get("blocks", {})
        if isinstance(raw_blocks, dict):
            items = raw_blocks.items()
        else:
            return  # unexpected format

        # Collect mtime for preload scoring
        preload_candidates: list = []

        # Parallel file reads — 16 workers dramatically reduce cold I/O time
        # (serial reads: ~70s cold; parallel: ~15-20s cold)
        _PARALLEL_READ_WORKERS = 16

        def _read_one_block(bid_bdata):
            bid, bdata = bid_bdata
            cf = blocks_dir / f"{bid}.txt"
            try:
                if not cf.exists():
                    return None
                content = cf.read_text(errors="replace")
                mtime = cf.stat().st_mtime
                return (bid, bdata, content, mtime)
            except OSError:
                return None

        from concurrent.futures import ThreadPoolExecutor as _ThreadPoolExecutor
        with _ThreadPoolExecutor(max_workers=_PARALLEL_READ_WORKERS) as _pool:
            _read_results = list(_pool.map(_read_one_block, list(items)))

        for _result in _read_results:
            if _result is None:
                continue
            bid, bdata, content, _mtime = _result

            new_blocks[bid] = {
                "block_id": bid,
                "source_path": bdata.get("source_path", bid),
                "risk_class": bdata.get("risk_class", "narrative"),
                "must_keep": bdata.get("must_keep", False),
                "raw_tokens": bdata.get("raw_tokens", 0),
                # NOTE: content NOT stored here — fetched on demand via _get_content()
                "_content_file": str(blocks_dir / f"{bid}.txt"),
            }

            # Collect for BM25 (content used here then discarded)
            preload_candidates.append((bid, content, _mtime))

        # Precompute BM25 from content (content discarded after this pass)
        df: Dict[str, int] = {}
        block_tfs: Dict[str, Dict[str, int]] = {}
        block_dl: Dict[str, int] = {}  # precomputed doc lengths
        total_dl = 0

        for bid, content, _mtime in preload_candidates:
            terms = _bm25_tokenize(content)
            tf: Dict[str, int] = {}
            for t in terms:
                tf[t] = tf.get(t, 0) + 1
            dl = len(terms)
            block_tfs[bid] = tf
            block_dl[bid] = dl  # store precomputed length
            total_dl += dl
            for t in set(terms):
                df[t] = df.get(t, 0) + 1

        doc_count = len(new_blocks)
        avg_dl = total_dl / doc_count if doc_count > 0 else 0

        # Build inverted index: term -> set(block_ids)
        inverted: Dict[str, set] = {}
        for bid, tf in block_tfs.items():
            for term in tf:
                if term not in inverted:
                    inverted[term] = set()
                inverted[term].add(bid)

        # Build new LRU cache — preload top-N recently-modified blocks
        new_cache: OrderedDict = OrderedDict()
        new_cache_bytes = 0
        preload_n = VAULT_CACHE_PRELOAD
        if preload_n > 0:
            sorted_by_mtime = sorted(preload_candidates, key=lambda x: -x[2])[:preload_n]
            for bid, content, _mtime in sorted_by_mtime:
                content_size = len(content.encode("utf-8"))
                if new_cache_bytes + content_size <= self._max_cache_bytes:
                    new_cache[bid] = content
                    new_cache_bytes += content_size

        # Atomic swap — all heavy work done above, lock held briefly
        with self._lock:
            self.blocks = new_blocks
            self._df = df
            self._block_tfs = block_tfs
            self._block_dl = block_dl
            self._avg_dl = avg_dl
            self._doc_count = doc_count
            self._inverted = inverted
            self._last_mtime = mtime
            self._content_cache = new_cache
            self._cache_bytes = new_cache_bytes
            # Reset counters on reload
            self._cache_hits = 0
            self._cache_misses = 0
            self._cache_evictions = 0
            self._ready = True

        print(
            f"  📚 Vault index loaded: {doc_count} blocks, {sum(b['raw_tokens'] for b in new_blocks.values()):,} tokens"
            f" | cache preloaded: {len(new_cache)} blocks ({new_cache_bytes // 1024 // 1024}MB)"
        )

        # Persist BM25 state for fast future loads
        self._save_bm25_cache(index_path, mtime)

    def _enforce_cache_limit(self):
        """Evict LRU entries until cache is within byte limit. Must be called with lock held."""
        while self._cache_bytes > self._max_cache_bytes and self._content_cache:
            _bid, evicted = self._content_cache.popitem(last=False)
            self._cache_bytes -= len(evicted.encode("utf-8"))
            self._cache_evictions += 1

    def _get_content(self, block_id: str) -> str:
        """Fetch block content from LRU cache (Tier 2) or disk (Tier 3)."""
        with self._lock:
            if block_id in self._content_cache:
                # Cache hit — move to end (most recently used)
                content = self._content_cache.pop(block_id)
                self._content_cache[block_id] = content
                self._cache_hits += 1
                return content

            self._cache_misses += 1

        # Cache miss — read from disk (Tier 3), outside lock to avoid blocking search
        block = self.blocks.get(block_id)
        if not block:
            return ""
        content_file = Path(block.get("_content_file", ""))
        if not content_file.exists():
            return ""
        try:
            content = content_file.read_text(errors="replace")
        except OSError:
            return ""

        # Insert into cache
        content_size = len(content.encode("utf-8"))
        with self._lock:
            self._content_cache[block_id] = content
            self._cache_bytes += content_size
            self._enforce_cache_limit()

        return content

    @property
    def cache_stats(self) -> dict:
        """Return current cache statistics (thread-safe snapshot)."""
        with self._lock:
            return {
                "vault_cache_entries": len(self._content_cache),
                "vault_cache_memory_mb": round(self._cache_bytes / 1024 / 1024, 2),
                "vault_cache_hits": self._cache_hits,
                "vault_cache_misses": self._cache_misses,
                "vault_cache_evictions": self._cache_evictions,
                "vault_cache_hit_rate": round(
                    self._cache_hits / (self._cache_hits + self._cache_misses)
                    if (self._cache_hits + self._cache_misses) > 0
                    else 0.0,
                    3,
                ),
            }

    def search(
        self, query: str, top_k: int = 5, min_score: float = 2.0
    ) -> List[Tuple[dict, float]]:
        """BM25 search across vault blocks. Returns [(block_dict, score), ...]."""
        query_terms = _bm25_tokenize(query)
        if not query_terms or not self.blocks:
            return []

        # Snapshot refs atomically under GIL — no lock held during scoring
        df = self._df
        block_tfs = self._block_tfs
        block_dl = self._block_dl  # precomputed doc lengths — avoids sum(tf.values()) per request
        avg_dl = self._avg_dl
        doc_count = self._doc_count
        blocks = self.blocks
        inverted = self._inverted

        k1 = 1.5
        b_param = 0.75
        scores: Dict[str, float] = {}

        # IDF-gated candidate expansion with MAX_CANDIDATES cap:
        # 1. Skip terms appearing in >40% of docs (too common to discriminate).
        # 2. Sort remaining terms by ascending frequency (most selective first).
        # 3. Add their posting lists until we hit MAX_CANDIDATES (prevents exploding to 6k+).
        # 4. Fall back to common terms only if no selective terms exist.
        # At cap=500, top-5 results are identical to full scan; scoring time drops from 67ms→8ms.
        _idf_gate = 0.40  # skip terms in >40% of corpus for candidate expansion
        _max_candidates = 500
        _selective: List[Tuple[str, int]] = []
        _fallback: List[str] = []
        for qt in query_terms:
            if qt not in inverted:
                continue
            term_freq = df.get(qt, 0)
            if doc_count > 0 and term_freq / doc_count > _idf_gate:
                _fallback.append(qt)
            else:
                _selective.append((qt, term_freq))
        _selective.sort(key=lambda x: x[1])  # most selective first

        candidates: set = set()
        if _selective:
            for qt, _ in _selective:
                candidates.update(inverted[qt])
                if len(candidates) >= _max_candidates:
                    break  # enough; less selective terms contribute diminishing returns
        if not candidates:
            # All terms are corpus-wide — fall back to common terms (fast path)
            for qt in _fallback:
                candidates.update(inverted[qt])
        if not candidates:
            return []

        for bid in candidates:
            tf = block_tfs.get(bid, {})
            dl = block_dl.get(bid, 0)  # O(1) lookup instead of O(terms) sum
            score = 0.0
            for qt in query_terms:
                if qt not in df:
                    continue
                idf = math.log((doc_count - df[qt] + 0.5) / (df[qt] + 0.5) + 1)
                term_freq = tf.get(qt, 0)
                if term_freq == 0:
                    continue
                numerator = term_freq * (k1 + 1)
                denominator = term_freq + k1 * (1 - b_param + b_param * dl / avg_dl)
                score += idf * numerator / denominator
            if score >= min_score:
                scores[bid] = score

        # Sort deterministically: score desc, then path asc, then block_id asc
        # This ensures byte-identical ordering for cache stability even on score ties
        ranked = sorted(
            scores.items(),
            key=lambda x: (-x[1], blocks[x[0]].get("source_path", ""), x[0]),
        )[:top_k]
        return [(blocks[bid], score) for bid, score in ranked]

    def compile_injection(
        self, query: str, budget: int = 4000, top_k: int = 5, min_score: float = 2.0
    ) -> Tuple[str, int, List[str]]:
        """
        Search vault and compile injection text within budget.
        Returns (injection_text, tokens_used, source_refs).
        """
        results = self.search(query, top_k=top_k, min_score=min_score)
        if not results:
            return "", 0, []

        injection_parts = []
        tokens_used = 0
        source_refs = []

        for block, score in results:
            # Fetch content from LRU cache (Tier 2) or disk (Tier 3) — never from block dict
            content = self._get_content(block["block_id"])
            block_tokens = block["raw_tokens"]

            # Budget check
            remaining = budget - tokens_used
            if remaining <= 100:
                break

            # Truncate if needed
            if block_tokens > remaining:
                # Rough char-to-token truncation
                char_limit = remaining * 4
                content = content[:char_limit].rsplit("\n", 1)[0]
                block_tokens = count_tokens(content)

            source_path = block["source_path"]
            injection_parts.append(f"--- [{source_path}] (relevance: {score:.1f}) ---\n{content}")
            tokens_used += block_tokens
            source_refs.append(source_path)

        if not injection_parts:
            return "", 0, []

        header = "\n\n## Retrieved Context\n"  # fixed header for cache stability
        injection_text = header + "\n\n".join(injection_parts)
        # Recount with header
        tokens_used = count_tokens(injection_text)

        return injection_text, tokens_used, source_refs

