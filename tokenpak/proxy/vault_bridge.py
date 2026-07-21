"""
tokenpak.proxy.vault_bridge — VaultIndex (BM25 search), global singletons,
and startup initialization for vault retrieval, term resolver, and capsule builder.

Extracted from runtime/proxy.py (L1177-1498) as part of TPK-RESTRUCTURE-004.
"""

import codecs
import ctypes
import gc
import hashlib
import json
import logging
import math
import os
import platform
import re
import sys
import threading
import time
from array import array as _array
from bisect import bisect_left as _bisect_left
from collections import Counter as _Counter
from collections import OrderedDict as _OrderedDict
from dataclasses import dataclass as _dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from tokenpak.vault.walker import MAX_FILE_SIZE as _VAULT_BLOCK_MAX_BYTES

if TYPE_CHECKING:
    from .request import ProxyRequest

from .config import (
    INJECT_BUDGET,
    INJECT_MIN_SCORE,
    INJECT_TOP_K,
    RETRIEVAL_BACKEND,
    SKELETON_ENABLED,
    VAULT_AUTO_REINDEX_INTERVAL,
    VAULT_INDEX_PATH,
    VAULT_INDEX_RELOAD_INTERVAL,
    _cfg,
)
from .config import (
    VAULT_CACHE_MAX_BYTES as _VAULT_CACHE_MAX_BYTES,
)
from .config import (
    VAULT_CACHE_PRELOAD as _VAULT_CACHE_PRELOAD,
)
from .token_cache import count_tokens

logger = logging.getLogger(__name__)

# Term resolver feature flags — enabled by default; opt out with TOKENPAK_TERM_RESOLVER_ENABLED=0
TERM_RESOLVER_ENABLED: bool = _cfg(
    "features.term_resolver", True, "TOKENPAK_TERM_RESOLVER_ENABLED", bool
)
TERM_RESOLVER_TOP_K: int = _cfg(
    "features.term_resolver_top_k", 3, "TOKENPAK_TERM_RESOLVER_TOP_K", int
)
TERM_RESOLVER_MAX_BYTES: int = _cfg(
    "features.term_resolver_max_bytes", 512, "TOKENPAK_TERM_RESOLVER_MAX_BYTES", int
)

# Capsule builder feature flags.
# Prefer the canonical TOKENPAK_CAPSULE_BUILDER name; fall back to the legacy
# TOKENPAK_CAPSULE_BUILDER_ENABLED name for one release. Both use the shared
# truthy semantics ({"1","true","yes","on"}, case-insensitive).
if os.environ.get("TOKENPAK_CAPSULE_BUILDER") is not None:
    from tokenpak.core.config_loader import _bool_env as _bool_env_cb

    ENABLE_CAPSULE_BUILDER: bool = _bool_env_cb(os.environ["TOKENPAK_CAPSULE_BUILDER"])
else:
    ENABLE_CAPSULE_BUILDER = _cfg(
        "features.capsule_builder", False, "TOKENPAK_CAPSULE_BUILDER_ENABLED", bool
    )
CAPSULE_MIN_CHARS: int = _cfg("features.capsule_min_chars", 400, "TOKENPAK_CAPSULE_MIN_CHARS", int)
CAPSULE_HOT_WINDOW: int = _cfg(
    "features.capsule_hot_window", 12, "TOKENPAK_CAPSULE_HOT_WINDOW", int
)

# Query expansion feature flag — enabled by default; opt out with TOKENPAK_QUERY_EXPANSION_ENABLED=0
QUERY_EXPANSION_ENABLED: bool = _cfg(
    "features.query_expansion", True, "TOKENPAK_QUERY_EXPANSION_ENABLED", bool
)

# ---------------------------------------------------------------------------
# Query expansion — optional; falls back to plain re.findall when unavailable
# ---------------------------------------------------------------------------
try:
    from tokenpak.vault.query_expansion import (
        expand_query as _qe_expand,
    )
    from tokenpak.vault.query_expansion import (
        tokenize as _qe_tokenize,
    )

    _QE_AVAILABLE = True
except ImportError:
    _QE_AVAILABLE = False

# ---------------------------------------------------------------------------
# VaultIndex — BM25-searchable read-only index
# ---------------------------------------------------------------------------


@_dataclass(frozen=True)
class _ContentRecord:
    """File identity pinned to the BM25 generation built from its content."""

    path: str
    content_hash: str
    mtime_ns: Optional[int]
    ctime_ns: Optional[int]
    file_size: Optional[int]
    device: Optional[int]
    inode: Optional[int]
    metadata_hash_stale: bool
    actual_tokens: Optional[int]


@_dataclass(frozen=True)
class _HydratedContent:
    """A bounded, generation-verified UTF-8 content value."""

    text: str
    utf8_bytes: int
    source_bytes: int
    source_bytes_loaded: int
    truncated: bool


@_dataclass
class _HydrationFlight:
    """One physical read shared by concurrent readers of a generation key."""

    event: threading.Event
    result: Optional[_HydratedContent] = None


class _OversizedVaultBlock(Exception):
    """Raised internally when a block exceeds the canonical walker limit."""


@_dataclass(frozen=True)
class _IndexGeneration:
    """One atomically published, read-only BM25 generation."""

    generation_id: int
    blocks: Dict[str, dict]
    content_records: Dict[str, _ContentRecord]
    block_ids: Tuple[str, ...]
    postings: Dict[str, _array]
    block_dl: _array
    avg_dl: float
    doc_count: int
    index_mtime: float
    stale_content_hashes: int
    skipped_blocks: int
    oversized_blocks: int


_CONTENT_GENERATION_MISMATCH = (
    "[TokenPak: vault block content changed after indexing; reload required]"
)
_CONTENT_READ_CHUNK_BYTES = 64 * 1024


def _utf8_prefix(text: str, byte_limit: int) -> str:
    """Return the longest valid UTF-8 prefix whose encoding fits the limit."""
    if byte_limit <= 0:
        return ""
    encoded = text.encode("utf-8")
    if len(encoded) <= byte_limit:
        return text
    return encoded[:byte_limit].decode("utf-8", errors="ignore")


def _return_released_memory_to_os() -> None:
    """Best-effort return of post-reload allocator pages to the operating system."""
    gc.collect()
    if sys.platform != "linux" or platform.libc_ver()[0].lower() != "glibc":
        return

    try:
        libc = ctypes.CDLL(None)
        malloc_trim = libc.malloc_trim
        malloc_trim.argtypes = [ctypes.c_size_t]
        malloc_trim.restype = ctypes.c_int
        malloc_trim(0)
    except (AttributeError, OSError, TypeError, ValueError, ctypes.ArgumentError):
        logger.debug("glibc malloc_trim unavailable after vault reload", exc_info=True)


class VaultIndex:
    """
    Read-only BM25-searchable index loaded from .tokenpak/index.json + blocks/.
    Reloads periodically to pick up git-pulled changes.
    """

    def __init__(self, tokenpak_dir: str):
        self.tokenpak_dir = Path(tokenpak_dir)
        self._last_loaded = 0
        self._lock = threading.Lock()
        self._cache_condition = threading.Condition(self._lock)
        self._reload_lock = threading.Lock()
        # Compact BM25 state. Each posting is one packed uint64:
        # (document_index << 32) | term_frequency. This keeps each term string
        # once instead of duplicating it in every document TF dict and in a
        # second set-based inverted index.
        self._generation = _IndexGeneration(
            generation_id=0,
            blocks={},
            content_records={},
            block_ids=(),
            postings={},
            block_dl=_array("I"),
            avg_dl=0.0,
            doc_count=0,
            index_mtime=0.0,
            stale_content_hashes=0,
            skipped_blocks=0,
            oversized_blocks=0,
        )
        # Full block content is loaded only for selected results and retained
        # in a byte-bounded, generation-keyed LRU.
        self._content_cache: _OrderedDict[Tuple[int, str], _HydratedContent] = _OrderedDict()
        self._cache_bytes = 0
        self._hydration_reserved_bytes = 0
        self._max_managed_content_bytes = 0
        self._hydration_flights: Dict[Tuple[int, str], _HydrationFlight] = {}
        self._max_cache_bytes = max(0, _VAULT_CACHE_MAX_BYTES)
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_evictions = 0
        self._coalesced_hydrations = 0
        self._physical_hydration_reads = 0
        self._hydration_failures = 0

    @property
    def available(self) -> bool:
        return self._snapshot_generation().doc_count > 0

    def _snapshot_generation(self) -> _IndexGeneration:
        """Return one coherent generation pointer under the publication lock."""
        with self._lock:
            return self._generation

    # Compatibility views for existing metrics/debug consumers. Search never
    # reads these separately; it captures one _IndexGeneration instead.
    @property
    def blocks(self) -> Dict[str, dict]:
        return self._snapshot_generation().blocks

    @property
    def _block_ids(self) -> Tuple[str, ...]:
        return self._snapshot_generation().block_ids

    @property
    def _postings(self) -> Dict[str, _array]:
        return self._snapshot_generation().postings

    @property
    def _block_dl(self) -> _array:
        return self._snapshot_generation().block_dl

    @property
    def _avg_dl(self) -> float:
        return self._snapshot_generation().avg_dl

    @property
    def _doc_count(self) -> int:
        return self._snapshot_generation().doc_count

    @property
    def _last_mtime(self) -> float:
        return self._snapshot_generation().index_mtime

    def maybe_reload(self):
        """Reload if index file changed or enough time passed."""
        now = __import__("time").time()
        if now - self._last_loaded < VAULT_INDEX_RELOAD_INTERVAL:
            return

        index_path = self.tokenpak_dir / "index.json"
        if not index_path.exists():
            return

        try:
            mtime = index_path.stat().st_mtime
            generation = self._snapshot_generation()
            if mtime == generation.index_mtime and generation.doc_count:
                self._last_loaded = now
                return
        except OSError:
            return

        with self._reload_lock:
            now = __import__("time").time()
            if now - self._last_loaded < VAULT_INDEX_RELOAD_INTERVAL:
                return

            try:
                mtime = index_path.stat().st_mtime
                generation = self._snapshot_generation()
                if mtime == generation.index_mtime and generation.doc_count:
                    self._last_loaded = now
                    return
            except OSError:
                return

            previous_generation_id = generation.generation_id
            del generation
            self._load(index_path, mtime)
            published_generation_id = self._snapshot_generation().generation_id
            if published_generation_id > previous_generation_id:
                _return_released_memory_to_os()
            self._last_loaded = now

    @staticmethod
    def _scan_content_file(
        content_file: Path, expected_hash: Optional[str]
    ) -> Optional[Tuple[Dict[str, int], int, _ContentRecord]]:
        """Build BM25 from one stable block without retaining corpus content."""
        try:
            with content_file.open("rb") as handle:
                stat_before = os.fstat(handle.fileno())
                if stat_before.st_size > _VAULT_BLOCK_MAX_BYTES:
                    raise _OversizedVaultBlock
                raw_content = handle.read(stat_before.st_size)
                stat_after = os.fstat(handle.fileno())
        except OSError:
            return None

        if (
            stat_before.st_mtime_ns != stat_after.st_mtime_ns
            or stat_before.st_ctime_ns != stat_after.st_ctime_ns
            or stat_before.st_dev != stat_after.st_dev
            or stat_before.st_ino != stat_after.st_ino
            or len(raw_content) != stat_before.st_size
        ):
            return None

        content = raw_content.decode("utf-8", errors="replace")
        actual_hash = hashlib.sha256(content.encode("utf-8", errors="replace")).hexdigest()
        metadata_hash_stale = expected_hash is not None and expected_hash != actual_hash
        tf, document_length = _bm25_count_document(content)
        record = _ContentRecord(
            path=str(content_file),
            content_hash=actual_hash,
            mtime_ns=stat_after.st_mtime_ns,
            ctime_ns=stat_after.st_ctime_ns,
            file_size=stat_after.st_size,
            device=stat_after.st_dev,
            inode=stat_after.st_ino,
            metadata_hash_stale=metadata_hash_stale,
            actual_tokens=count_tokens(content) if metadata_hash_stale else None,
        )
        return tf, document_length, record

    def _load(self, index_path: Path, mtime: float):
        """Load metadata and build compact BM25 postings.

        Block text is consumed one document at a time and is not retained in
        the index, and selected results are hydrated through the bounded
        content cache, so steady-state memory does not hold the full block
        text. Postings, vocabulary, and per-block index metadata still scale
        with corpus size (block count and distinct terms), so index memory
        grows with the corpus even though raw block bytes are not retained.
        """
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"  ⚠️ Vault index load error: {e}")
            return

        blocks_dir = self.tokenpak_dir / "blocks"
        new_blocks: Dict[str, dict] = {}
        content_records: Dict[str, _ContentRecord] = {}
        block_ids: List[str] = []
        posting_lists: Dict[str, List[int]] = {}
        block_dl = _array("I")
        preload_candidates: List[Tuple[float, str]] = []
        total_dl = 0
        total_raw_tokens = 0
        stale_content_hashes = 0
        unreadable_blocks = 0
        oversized_blocks = 0

        raw_blocks = data.get("blocks", {})
        if isinstance(raw_blocks, dict):
            items = raw_blocks.items()
        else:
            return  # unexpected format

        for bid, bdata in items:
            if not isinstance(bdata, dict):
                print(f"  ⚠️ Vault index load error: invalid block metadata for {bid}")
                return

            content_file = blocks_dir / f"{bid}.txt"
            raw_expected_hash = bdata.get("content_hash")
            expected_hash = (
                raw_expected_hash
                if isinstance(raw_expected_hash, str) and raw_expected_hash
                else None
            )
            try:
                scanned = self._scan_content_file(content_file, expected_hash)
            except _OversizedVaultBlock:
                oversized_blocks += 1
                continue
            if scanned is None:
                unreadable_blocks += 1
                continue
            tf, dl, content_record = scanned
            if content_record.metadata_hash_stale:
                stale_content_hashes += 1

            doc_idx = len(block_ids)
            block_ids.append(bid)
            raw_tokens = bdata.get("raw_tokens", 0)

            new_blocks[bid] = {
                "block_id": bid,
                "source_path": bdata.get("source_path", bid),
                "risk_class": bdata.get("risk_class", "narrative"),
                "must_keep": bdata.get("must_keep", False),
                "raw_tokens": raw_tokens,
                "source_type": bdata.get("source_type", "filesystem"),
                "claude_transcript": bdata.get("claude_transcript"),
                "_content_file": str(content_file),
            }
            if content_record.metadata_hash_stale:
                new_blocks[bid]["_content_metadata_stale"] = True
                new_blocks[bid]["_content_tokens_actual"] = content_record.actual_tokens
            content_records[bid] = content_record
            total_raw_tokens += raw_tokens

            block_dl.append(dl)
            total_dl += dl

            for term, term_frequency in tf.items():
                posting = posting_lists.get(term)
                if posting is None:
                    posting = []
                    posting_lists[term] = posting
                posting.append((doc_idx << 32) | term_frequency)

            if content_record.mtime_ns is not None:
                preload_candidates.append((content_record.mtime_ns / 1_000_000_000, bid))

        doc_count = len(new_blocks)
        avg_dl = total_dl / doc_count if doc_count > 0 else 0

        # Python lists make the one-time build substantially faster. Convert
        # and release them one at a time so the published state is compact and
        # conversion does not retain a second complete representation.
        postings: Dict[str, _array] = {}
        while posting_lists:
            term, posting = posting_lists.popitem()
            postings[term] = _array("Q", posting)

        # Atomic swap — all heavy work done above, lock held briefly
        with self._cache_condition:
            generation_id = self._generation.generation_id + 1
            new_generation = _IndexGeneration(
                generation_id=generation_id,
                blocks=new_blocks,
                content_records=content_records,
                block_ids=tuple(block_ids),
                postings=postings,
                block_dl=block_dl,
                avg_dl=avg_dl,
                doc_count=doc_count,
                index_mtime=mtime,
                stale_content_hashes=stale_content_hashes,
                skipped_blocks=unreadable_blocks + oversized_blocks,
                oversized_blocks=oversized_blocks,
            )
            self._generation = new_generation
            self._content_cache = _OrderedDict()
            self._cache_bytes = 0
            self._cache_hits = 0
            self._cache_misses = 0
            self._cache_evictions = 0
            self._coalesced_hydrations = 0
            self._physical_hydration_reads = 0
            self._hydration_failures = 0
            self._max_managed_content_bytes = self._hydration_reserved_bytes
            self._cache_condition.notify_all()

        # Warm the new generation through the same bounded admission path as
        # request-time hydration. Old-generation reads remain separately keyed.
        if _VAULT_CACHE_PRELOAD > 0 and self._max_cache_bytes > 0:
            preload_candidates.sort(reverse=True)
            for _, bid in preload_candidates[:_VAULT_CACHE_PRELOAD]:
                self._hydrate_content(new_generation, bid)

        with self._lock:
            if self._generation is new_generation:
                cache_entries = sum(key[0] == generation_id for key in self._content_cache)
                cache_bytes = self._cache_bytes
            else:
                cache_entries = 0
                cache_bytes = 0

        print(
            f"  📚 Vault index loaded: {doc_count} blocks, {total_raw_tokens:,} tokens"
            f" | content cache: {cache_entries} blocks ({cache_bytes // 1024 // 1024}MB)"
        )
        if stale_content_hashes:
            print(
                "  ⚠️ Vault index metadata had "
                f"{stale_content_hashes} stale content hash(es); indexed stable block bytes"
            )
        if unreadable_blocks:
            print(f"  ⚠️ Vault index skipped {unreadable_blocks} unreadable or missing block(s)")
        if oversized_blocks:
            print(
                f"  ⚠️ Vault index skipped {oversized_blocks} block(s) over "
                f"{_VAULT_BLOCK_MAX_BYTES} bytes"
            )

    @staticmethod
    def _record_matches_stat(record: _ContentRecord, stat_result: os.stat_result) -> bool:
        """Return whether an open file still has the pinned generation identity."""
        return (
            record.mtime_ns is not None
            and record.ctime_ns is not None
            and record.file_size is not None
            and record.device is not None
            and record.inode is not None
            and stat_result.st_mtime_ns == record.mtime_ns
            and stat_result.st_ctime_ns == record.ctime_ns
            and stat_result.st_size == record.file_size
            and stat_result.st_dev == record.device
            and stat_result.st_ino == record.inode
        )

    @classmethod
    def _read_pinned_prefix(
        cls, record: _ContentRecord, byte_limit: int
    ) -> Optional[_HydratedContent]:
        """Read at most ``byte_limit`` bytes and return a valid UTF-8 prefix."""
        if record.file_size is None or byte_limit < 0:
            return None

        try:
            with Path(record.path).open("rb") as handle:
                stat_before = os.fstat(handle.fileno())
                if not cls._record_matches_stat(record, stat_before):
                    return None

                target_bytes = min(byte_limit, record.file_size)
                bytes_read = 0
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                pieces: List[str] = []
                digest = hashlib.sha256()
                while bytes_read < target_bytes:
                    raw_chunk = handle.read(
                        min(_CONTENT_READ_CHUNK_BYTES, target_bytes - bytes_read)
                    )
                    if not raw_chunk:
                        return None
                    bytes_read += len(raw_chunk)
                    decoded = decoder.decode(raw_chunk, final=False)
                    if decoded:
                        pieces.append(decoded)
                        digest.update(decoded.encode("utf-8", errors="replace"))

                source_truncated = target_bytes < record.file_size
                decoded = decoder.decode(b"", final=not source_truncated)
                if decoded:
                    pieces.append(decoded)
                    digest.update(decoded.encode("utf-8", errors="replace"))
                stat_after = os.fstat(handle.fileno())
        except OSError:
            return None

        if not cls._record_matches_stat(record, stat_after):
            return None
        content = "".join(pieces)
        content_size = len(content.encode("utf-8"))
        truncated = source_truncated
        if content_size > byte_limit:
            content = _utf8_prefix(content, byte_limit)
            content_size = len(content.encode("utf-8"))
            truncated = True
        if not truncated and digest.hexdigest() != record.content_hash:
            return None
        return _HydratedContent(
            text=content,
            utf8_bytes=content_size,
            source_bytes=record.file_size,
            source_bytes_loaded=content_size,
            truncated=truncated,
        )

    def _evict_for_reservation(self, requested_bytes: int) -> None:
        """Free LRU bytes for a hydration reservation. Lock must be held."""
        while (
            self._content_cache
            and self._cache_bytes + self._hydration_reserved_bytes + requested_bytes
            > self._max_cache_bytes
        ):
            _, evicted = self._content_cache.popitem(last=False)
            self._cache_bytes -= evicted.utf8_bytes
            self._cache_evictions += 1

    def _hydrate_content(
        self, generation: _IndexGeneration, block_id: str
    ) -> Optional[_HydratedContent]:
        """Hydrate one generation key through bounded cache and singleflight."""
        record = generation.content_records.get(block_id)
        if record is None:
            return None
        source_bytes = record.file_size or 0
        if self._max_cache_bytes == 0:
            return _HydratedContent("", 0, source_bytes, 0, source_bytes > 0)

        cache_key = (generation.generation_id, block_id)
        with self._lock:
            cached = self._content_cache.pop(cache_key, None)
            if cached is not None:
                self._content_cache[cache_key] = cached
                self._cache_hits += 1
                return cached
            self._cache_misses += 1
            flight = self._hydration_flights.get(cache_key)
            if flight is None:
                flight = _HydrationFlight(threading.Event())
                self._hydration_flights[cache_key] = flight
                owns_flight = True
            else:
                self._coalesced_hydrations += 1
                owns_flight = False

        if not owns_flight:
            flight.event.wait()
            return flight.result

        requested_bytes = min(source_bytes, self._max_cache_bytes)
        reserved = False
        result: Optional[_HydratedContent] = None
        try:
            with self._cache_condition:
                while True:
                    self._evict_for_reservation(requested_bytes)
                    managed_bytes = (
                        self._cache_bytes + self._hydration_reserved_bytes + requested_bytes
                    )
                    if managed_bytes <= self._max_cache_bytes:
                        self._hydration_reserved_bytes += requested_bytes
                        self._max_managed_content_bytes = max(
                            self._max_managed_content_bytes, managed_bytes
                        )
                        reserved = True
                        break
                    self._cache_condition.wait()
                self._physical_hydration_reads += 1
            result = self._read_pinned_prefix(record, requested_bytes)
        except Exception:
            result = None
        finally:
            with self._cache_condition:
                if reserved:
                    self._hydration_reserved_bytes -= requested_bytes
                if result is not None and self._generation is generation:
                    existing = self._content_cache.pop(cache_key, None)
                    if existing is not None:
                        self._cache_bytes -= existing.utf8_bytes
                    self._content_cache[cache_key] = result
                    self._cache_bytes += result.utf8_bytes
                elif result is None:
                    self._hydration_failures += 1
                flight.result = result
                if self._hydration_flights.get(cache_key) is flight:
                    self._hydration_flights.pop(cache_key)
                flight.event.set()
                self._cache_condition.notify_all()
        return result

    @staticmethod
    def _fit_hydration(hydrated: _HydratedContent, byte_limit: int) -> _HydratedContent:
        """Clip one hydrated value to a search's remaining aggregate budget."""
        if hydrated.utf8_bytes <= byte_limit:
            return hydrated
        content = _utf8_prefix(hydrated.text, byte_limit)
        content_size = len(content.encode("utf-8"))
        return _HydratedContent(
            text=content,
            utf8_bytes=content_size,
            source_bytes=hydrated.source_bytes,
            source_bytes_loaded=min(hydrated.source_bytes_loaded, content_size),
            truncated=True,
        )

    def _get_content(self, block_id: str, *, generation: Optional[_IndexGeneration] = None) -> str:
        """Compatibility helper returning one globally bounded content string."""
        if generation is None:
            generation = self._snapshot_generation()
        hydrated = self._hydrate_content(generation, block_id)
        if hydrated is not None:
            return hydrated.text
        return _utf8_prefix(_CONTENT_GENERATION_MISMATCH, self._max_cache_bytes)

    @property
    def cache_stats(self) -> dict:
        """Return a thread-safe snapshot of bounded content-cache metrics."""
        with self._lock:
            accesses = self._cache_hits + self._cache_misses
            generation = self._generation
            return {
                "vault_cache_entries": len(self._content_cache),
                "vault_cache_memory_mb": round(self._cache_bytes / 1024 / 1024, 2),
                "vault_cache_max_mb": round(self._max_cache_bytes / 1024 / 1024, 2),
                "vault_cache_reserved_memory_mb": round(
                    self._hydration_reserved_bytes / 1024 / 1024, 2
                ),
                "vault_cache_active_hydrations": len(self._hydration_flights),
                "vault_cache_hits": self._cache_hits,
                "vault_cache_misses": self._cache_misses,
                "vault_cache_evictions": self._cache_evictions,
                "vault_cache_coalesced_hydrations": self._coalesced_hydrations,
                "vault_cache_physical_reads": self._physical_hydration_reads,
                "vault_cache_hydration_failures": self._hydration_failures,
                "vault_cache_hit_rate": round(self._cache_hits / accesses if accesses else 0.0, 3),
                "vault_index_stale_content_hashes": generation.stale_content_hashes,
                "vault_index_skipped_blocks": generation.skipped_blocks,
                "vault_index_oversized_blocks": generation.oversized_blocks,
            }

    def search(
        self, query: str, top_k: int = 5, min_score: float = 2.0
    ) -> List[Tuple[dict, float]]:
        """BM25 search across vault blocks. Returns [(block_dict, score), ...]."""
        return self._search(query, top_k=top_k, min_score=min_score, include_internal=False)

    def _search(
        self,
        query: str,
        top_k: int,
        min_score: float,
        *,
        include_internal: bool,
    ) -> List[Tuple[dict, float]]:
        """Search with optional private metadata for the injection compiler."""
        query_terms = _bm25_tokenize_query(query)
        generation = self._snapshot_generation()
        if not query_terms or generation.doc_count == 0:
            return []

        # One immutable generation keeps IDs, postings, lengths, and metadata
        # coherent while reload work builds and publishes its successor.
        block_ids = generation.block_ids
        postings = generation.postings
        block_dl = generation.block_dl
        avg_dl = generation.avg_dl
        doc_count = generation.doc_count
        blocks = generation.blocks

        k1 = 1.5
        b_param = 0.75
        scores: Dict[int, float] = {}

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
            posting = postings.get(qt)
            if posting is None:
                continue
            term_freq = len(posting)
            if doc_count > 0 and term_freq / doc_count > _idf_gate:
                _fallback.append(qt)
            else:
                _selective.append((qt, term_freq))
        _selective.sort(key=lambda x: x[1])  # most selective first

        candidates: set[int] = set()
        if _selective:
            for qt, _ in _selective:
                posting = postings[qt]
                candidates.update(packed >> 32 for packed in posting)
                if len(candidates) >= _max_candidates:
                    break  # enough; less selective terms contribute diminishing returns
        if not candidates:
            # All terms are corpus-wide — fall back to common terms (fast path)
            for qt in _fallback:
                posting = postings[qt]
                candidates.update(packed >> 32 for packed in posting)
        if not candidates:
            return []

        # Score directly from compact postings. Query-term duplication is
        # intentionally preserved because the prior implementation added each
        # expanded term occurrence independently.
        for qt in query_terms:
            posting = postings.get(qt)
            if posting is None:
                continue
            df = len(posting)
            idf = math.log((doc_count - df + 0.5) / (df + 0.5) + 1)
            if len(posting) > len(candidates) * 4:
                # Posting doc IDs are sorted. For a selective candidate set,
                # binary lookup avoids scanning corpus-wide common terms.
                for doc_idx in candidates:
                    position = _bisect_left(posting, doc_idx << 32)
                    if position >= len(posting):
                        continue
                    packed = posting[position]
                    if packed >> 32 != doc_idx:
                        continue
                    term_freq = packed & 0xFFFFFFFF
                    dl = block_dl[doc_idx]
                    numerator = term_freq * (k1 + 1)
                    denominator = term_freq + k1 * (1 - b_param + b_param * dl / avg_dl)
                    scores[doc_idx] = scores.get(doc_idx, 0.0) + idf * numerator / denominator
            else:
                for packed in posting:
                    doc_idx = packed >> 32
                    if doc_idx not in candidates:
                        continue
                    term_freq = packed & 0xFFFFFFFF
                    dl = block_dl[doc_idx]
                    numerator = term_freq * (k1 + 1)
                    denominator = term_freq + k1 * (1 - b_param + b_param * dl / avg_dl)
                    scores[doc_idx] = scores.get(doc_idx, 0.0) + idf * numerator / denominator

        # Sort deterministically: score desc, then path asc, then block_id asc
        # This ensures byte-identical ordering for cache stability even on score ties
        ranked = sorted(
            ((doc_idx, score) for doc_idx, score in scores.items() if score >= min_score),
            key=lambda x: (
                -x[1],
                blocks[block_ids[x[0]]].get("source_path", ""),
                block_ids[x[0]],
            ),
        )[:top_k]

        # Preserve the proxy VaultIndex API: search results include content.
        # Only top-k results are hydrated, and the retained bytes stay bounded.
        results: List[Tuple[dict, float]] = []
        remaining_content_bytes = self._max_cache_bytes
        for doc_idx, score in ranked:
            bid = block_ids[doc_idx]
            record = generation.content_records.get(bid)
            source_bytes = record.file_size if record and record.file_size else 0
            if remaining_content_bytes > 0:
                hydrated = self._hydrate_content(generation, bid)
            else:
                hydrated = _HydratedContent(
                    text="",
                    utf8_bytes=0,
                    source_bytes=source_bytes,
                    source_bytes_loaded=0,
                    truncated=source_bytes > 0,
                )
            if hydrated is None:
                marker = _utf8_prefix(_CONTENT_GENERATION_MISMATCH, remaining_content_bytes)
                hydrated = _HydratedContent(
                    text=marker,
                    utf8_bytes=len(marker.encode("utf-8")),
                    source_bytes=source_bytes,
                    source_bytes_loaded=0,
                    truncated=True,
                )
            hydrated = self._fit_hydration(hydrated, remaining_content_bytes)
            remaining_content_bytes -= hydrated.utf8_bytes

            block = dict(blocks[bid])
            block.pop("_content_file", None)
            if not include_internal:
                block.pop("_content_metadata_stale", None)
                block.pop("_content_tokens_actual", None)
            block["content"] = hydrated.text
            if hydrated.truncated:
                block["content_truncated"] = True
                block["content_bytes_total"] = hydrated.source_bytes
                block["content_bytes_loaded"] = hydrated.source_bytes_loaded
            results.append((block, score))
        return results

    def compile_injection(
        self, query: str, budget: int = 4000, top_k: int = 5, min_score: float = 2.0
    ) -> Tuple[str, int, List[str]]:
        """
        Search vault and compile injection text within budget.
        Returns (injection_text, tokens_used, source_refs).
        """
        results = self._search(
            query,
            top_k=top_k,
            min_score=min_score,
            include_internal=True,
        )
        if not results:
            return "", 0, []

        injection_parts = []
        tokens_used = 0
        source_refs = []
        header = "\n\n## Retrieved Context\n"  # fixed header for cache stability

        for block, score in results:
            content = block["content"]
            if not content and block.get("content_truncated"):
                continue
            source_path = block["source_path"]
            part_prefix = f"--- [{source_path}] (relevance: {score:.1f}) ---\n"

            # Budget check
            remaining = budget - tokens_used
            if remaining <= 100:
                break

            complete_parts = injection_parts + [part_prefix + content]
            complete_tokens = count_tokens(header + "\n\n".join(complete_parts))
            if complete_tokens <= budget:
                injection_parts = complete_parts
                tokens_used = complete_tokens
                source_refs.append(source_path)
                continue

            # The complete next result does not fit. Find the largest content
            # prefix whose fully rendered injection, including fixed headers
            # and separators, stays within the hard token budget.
            low = 0
            high = len(content)
            best = 0
            while low <= high:
                midpoint = (low + high) // 2
                candidate_parts = injection_parts + [part_prefix + content[:midpoint]]
                candidate_tokens = count_tokens(header + "\n\n".join(candidate_parts))
                if candidate_tokens <= budget:
                    best = midpoint
                    low = midpoint + 1
                else:
                    high = midpoint - 1
            if best == 0:
                break
            injection_parts.append(part_prefix + content[:best])
            tokens_used = count_tokens(header + "\n\n".join(injection_parts))
            source_refs.append(source_path)
            break

        if not injection_parts:
            return "", 0, []

        injection_text = header + "\n\n".join(injection_parts)
        # Recount with header
        tokens_used = count_tokens(injection_text)

        return injection_text, tokens_used, source_refs


# ---------------------------------------------------------------------------
# BM25 tokenizer — lru_cache gives 50x speedup on repeated queries
# ---------------------------------------------------------------------------


@lru_cache(maxsize=512)
def _bm25_tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


def _bm25_count_document(text: str) -> Tuple[Dict[str, int], int]:
    """Count document terms without retaining the document in the query LRU."""
    terms = re.findall(r"[a-z0-9_]+", text.lower())
    return _Counter(terms), len(terms)


@lru_cache(maxsize=512)
def _bm25_tokenize_query(query: str) -> List[str]:
    """Tokenize a search query with optional expansion (aliases + stems).

    Uses query_expansion when QUERY_EXPANSION_ENABLED is True and the module is
    available; falls back to plain tokenization otherwise.  Index-time tokenization
    always uses _bm25_tokenize so the two paths stay consistent: expansion terms
    that don't appear in the index score 0 and are harmlessly skipped.
    """
    if QUERY_EXPANSION_ENABLED and _QE_AVAILABLE:
        tokens = list(_qe_tokenize(query, mode="query"))
        return [t for t, _ in _qe_expand(tokens)]
    return _bm25_tokenize(query)


# ---------------------------------------------------------------------------
# Global vault index instance — lazy-loaded on first access
# ---------------------------------------------------------------------------
# LAZY-INIT: Do NOT instantiate or load anything at module import time.
# Importing vault_bridge triggered a >10s hang because VaultIndex.__init__
# reads the entire vault index from disk.  All three singletons are now
# initialised on first call to get_vault_index() / get_term_resolver() /
# get_capsule_builder(), which happens only when the proxy server actually
# starts handling requests — not during `import`.

_VAULT_INDEX: Optional[object] = None  # type: ignore[assignment]
_VAULT_INDEX_LOCK = threading.Lock()

_TERM_RESOLVER: Optional[object] = None  # type: ignore[assignment]
_CAPSULE_BUILDER: Optional[object] = None  # type: ignore[assignment]
_SINGLETONS_INITIALIZED = False
_SINGLETONS_LOCK = threading.Lock()


def _build_vault_index() -> object:
    """Create the correct VaultIndex backend (sqlite or json_blocks)."""
    if RETRIEVAL_BACKEND == "sqlite":
        try:
            from tokenpak.vault.sqlite_backend import (
                SQLiteRetrievalBackend as _SQLiteBackend,
            )

            idx = _SQLiteBackend(VAULT_INDEX_PATH)
            print(f"  📦 Vault retrieval backend: sqlite ({VAULT_INDEX_PATH})")
            return idx
        except ImportError as _sqlite_err:
            print(
                f"  ⚠️  SQLite retrieval backend unavailable ({_sqlite_err}), falling back to json_blocks"
            )
    idx = VaultIndex(VAULT_INDEX_PATH)
    print(f"  📦 Vault retrieval backend: json_blocks ({VAULT_INDEX_PATH})")
    return idx


def _init_singletons() -> None:
    """Initialize VAULT_INDEX, TERM_RESOLVER, and CAPSULE_BUILDER on first use."""
    global _VAULT_INDEX, _TERM_RESOLVER, _CAPSULE_BUILDER, _SINGLETONS_INITIALIZED

    with _SINGLETONS_LOCK:
        if _SINGLETONS_INITIALIZED:
            return

        # --- VaultIndex ---
        _VAULT_INDEX = _build_vault_index()
        _VAULT_INDEX.maybe_reload()  # type: ignore[union-attr]
        print(f"  ✅ Vault index loaded: {len(_VAULT_INDEX.blocks)} blocks")  # type: ignore[union-attr]

        # Start background reload timer
        def _vault_index_reload_timer() -> None:
            _VAULT_INDEX.maybe_reload()  # type: ignore[union-attr]
            t = threading.Timer(VAULT_INDEX_RELOAD_INTERVAL, _vault_index_reload_timer)
            t.daemon = True
            t.start()

        _vault_index_reload_timer()

        # Start background auto-reindex timer (rebuilds index from source files)
        if VAULT_AUTO_REINDEX_INTERVAL > 0:

            def _vault_auto_reindex_timer() -> None:
                try:
                    from tokenpak.vault.vault_health import VaultHealth

                    # VAULT_INDEX_PATH points to the .tokenpak dir inside vault
                    # VaultHealth expects the vault root (parent of .tokenpak)
                    vault_dir = Path(VAULT_INDEX_PATH).parent
                    checker = VaultHealth(vault_dir=vault_dir)
                    result = checker.repair()
                    if result.success and result.log_entry and "Rebuilt" in str(result.log_entry):
                        print(f"  🔄 Auto-reindex: {result.index_entries} blocks", flush=True)
                        _VAULT_INDEX.maybe_reload()  # type: ignore[union-attr]
                except Exception as e:
                    print(f"  ⚠️ Auto-reindex failed: {e}", flush=True)

                t = threading.Timer(VAULT_AUTO_REINDEX_INTERVAL, _vault_auto_reindex_timer)
                t.daemon = True
                t.start()

            # First auto-reindex check after the configured interval
            t_reindex = threading.Timer(VAULT_AUTO_REINDEX_INTERVAL, _vault_auto_reindex_timer)
            t_reindex.daemon = True
            t_reindex.start()
            print(f"  🔄 Auto-reindex: every {VAULT_AUTO_REINDEX_INTERVAL}s", flush=True)

        # --- Term Resolver ---
        _TERM_RESOLVER_AVAILABLE = False
        try:
            from tokenpak.vault.semantic import (  # type: ignore[assignment]
                TermResolver,
                TermResolverConfig,
            )

            _TERM_RESOLVER_AVAILABLE = True
        except ImportError:
            TermResolver = None  # type: ignore[assignment,misc]
            TermResolverConfig = None  # type: ignore[assignment,misc]

        if _TERM_RESOLVER_AVAILABLE and TERM_RESOLVER_ENABLED:
            try:
                _config = TermResolverConfig(
                    top_k=TERM_RESOLVER_TOP_K,
                    max_bytes_per_card=TERM_RESOLVER_MAX_BYTES,
                    enabled=True,
                )
                _TERM_RESOLVER = TermResolver(config=_config)
                print(
                    f"  🔤 Term resolver initialized (top_k={TERM_RESOLVER_TOP_K}, enabled={TERM_RESOLVER_ENABLED})"
                )
            except Exception as e:
                print(f"  ⚠️ Failed to initialize term resolver: {e}")

        # --- Capsule Builder ---
        try:
            from tokenpak.companion.capsules.builder import (
                CapsuleBuilder as _CapsuleBuilder,  # type: ignore[assignment]
            )

            _CAPSULE_BUILDER = _CapsuleBuilder(
                enabled=ENABLE_CAPSULE_BUILDER,
                min_block_chars=CAPSULE_MIN_CHARS,
                hot_window=CAPSULE_HOT_WINDOW,
            )
            print(
                f"  💊 Capsule builder loaded (enabled={ENABLE_CAPSULE_BUILDER}, min_chars={CAPSULE_MIN_CHARS})"
            )
        except ImportError as _cb_err:
            print(f"  ⚠️  Capsule builder unavailable: {_cb_err}")

        _SINGLETONS_INITIALIZED = True


def get_vault_index() -> "VaultIndex":
    """Return the global VaultIndex, initialising on first call."""
    if not _SINGLETONS_INITIALIZED:
        _init_singletons()
    return _VAULT_INDEX  # type: ignore[return-value]


def get_term_resolver() -> Optional[object]:
    """Return the global TermResolver (may be None), initialising on first call."""
    if not _SINGLETONS_INITIALIZED:
        _init_singletons()
    return _TERM_RESOLVER


def get_capsule_builder() -> Optional[object]:
    """Return the global CapsuleBuilder (may be None), initialising on first call."""
    if not _SINGLETONS_INITIALIZED:
        _init_singletons()
    return _CAPSULE_BUILDER


# ---------------------------------------------------------------------------
# Module-level aliases — kept for backward compatibility with callers that do
#   `from tokenpak.proxy.vault_bridge import VAULT_INDEX`
# These point to the lazy accessors; code that mutates these names directly
# will still see the correct object after first access.
# ---------------------------------------------------------------------------


class _LazyAlias:
    """Descriptor-like proxy that forwards attribute access to the real singleton."""

    def __init__(self, getter):
        self._getter = getter

    def __getattr__(self, name):
        return getattr(self._getter(), name)

    def __bool__(self):
        obj = self._getter()
        return obj is not None

    def __repr__(self):
        return repr(self._getter())


# For callers using module-level names in the *same* module only.
# server.py imports these from tokenpak.core.runtime.proxy, not here —
# so backward-compat aliases are only needed for direct vault_bridge importers.
VAULT_INDEX = _LazyAlias(get_vault_index)  # type: ignore[assignment]
TERM_RESOLVER = _LazyAlias(get_term_resolver)  # type: ignore[assignment]
CAPSULE_BUILDER = _LazyAlias(get_capsule_builder)  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# inject_vault_context — vault search + injection entry point (A2b transfer)
# ---------------------------------------------------------------------------


def inject_vault_context(
    body_bytes: bytes, adapter=None, *, request: "Optional[ProxyRequest]" = None
) -> "tuple[bytes, int, list[str]]":
    """
    Search vault index for relevant context and inject into the system prompt.
    Optionally resolves glossary terms and injects term cards.
    Returns (new_body_bytes, injected_tokens, source_refs).
    """
    if request is not None:
        body_bytes = request.body
    # Lazy imports for proxy-layer dependencies (transferred to subpackages in A2c)
    from tokenpak.proxy.adapters.utils import _detect_adapter, extract_query_signal

    try:
        from tokenpak.core.runtime.proxy import SESSION  # type: ignore[import]
    except ImportError:
        SESSION = {}  # type: ignore[assignment]
    from tokenpak.vault.chunk_shaping import _inject_skeleton_into_blocks  # type: ignore[import]
    from tokenpak.vault.search import _compile_from_results, score_and_sort  # type: ignore[import]

    vault_idx = get_vault_index()
    if not vault_idx.available:
        return body_bytes, 0, []

    active_adapter = adapter or _detect_adapter("", {}, body_bytes)

    # --- Sub-step timing (surfaced in vault_stage.details via SESSION) ---
    _t = time.perf_counter()

    query = extract_query_signal(body_bytes, adapter=active_adapter)
    _t_query_ms = (time.perf_counter() - _t) * 1000

    if not query:
        return body_bytes, 0, []

    term_resolver = get_term_resolver()

    # Resolve glossary terms (optional, feature-flagged)
    glossary_injection = ""
    glossary_tokens = 0
    _t2 = time.perf_counter()
    if term_resolver is not None and TERM_RESOLVER_ENABLED:
        try:
            resolution = term_resolver.resolve_terms(query)
            if resolution.injection_text and resolution.canonical_ids:
                glossary_injection = resolution.injection_text
                glossary_tokens = resolution.tokens_estimate
                # Adjust vault budget to account for glossary tokens
                remaining_budget = max(1000, INJECT_BUDGET - glossary_tokens)
            else:
                remaining_budget = INJECT_BUDGET
        except Exception:
            remaining_budget = INJECT_BUDGET
    else:
        remaining_budget = INJECT_BUDGET
    _t_resolver_ms = (time.perf_counter() - _t2) * 1000

    _t3 = time.perf_counter()
    semantic_scorer = None
    try:
        from tokenpak.core.runtime.proxy import (
            SEMANTIC_SCORER as semantic_scorer,  # type: ignore[import]
        )
    except Exception:
        pass

    # Augment mode: if a semantic scorer is configured, fuse BM25 + semantic scores
    if semantic_scorer is not None:
        try:
            bm25_results = vault_idx.search(
                query, top_k=INJECT_TOP_K * 2, min_score=INJECT_MIN_SCORE
            )
            if bm25_results:
                block_ids = [b["block_id"] for b, _ in bm25_results]
                semantic_scores = semantic_scorer.score(query, block_ids)
                rescored = score_and_sort(
                    bm25_results, query=query, semantic_scores=semantic_scores
                )[:INJECT_TOP_K]
                # Build injection from rescored results
                injection_text, tokens_used, source_refs = _compile_from_results(
                    rescored, remaining_budget
                )
            else:
                injection_text, tokens_used, source_refs = "", 0, []
        except Exception as _sem_err:
            logging.warning("Semantic scorer failed, falling back to BM25: %s", _sem_err)
            injection_text, tokens_used, source_refs = vault_idx.compile_injection(
                query, budget=remaining_budget, top_k=INJECT_TOP_K, min_score=INJECT_MIN_SCORE
            )
    else:
        injection_text, tokens_used, source_refs = vault_idx.compile_injection(
            query, budget=remaining_budget, top_k=INJECT_TOP_K, min_score=INJECT_MIN_SCORE
        )
    _t_bm25_ms = (time.perf_counter() - _t3) * 1000

    # Combine glossary + vault injection if both present
    combined_injection = ""
    combined_tokens = 0
    if glossary_injection and injection_text:
        combined_injection = glossary_injection + "\n\n" + injection_text
        combined_tokens = glossary_tokens + tokens_used
    elif glossary_injection:
        combined_injection = glossary_injection
        combined_tokens = glossary_tokens
    elif injection_text:
        combined_injection = injection_text
        combined_tokens = tokens_used

    if not combined_injection:
        return body_bytes, 0, []

    # Apply skeleton extraction to code blocks in injection text (code-body
    # elision when the extractor is available). No fixed savings percentage is
    # asserted here — any such claim must be backed by a committed benchmark.
    _t4 = time.perf_counter()
    if SKELETON_ENABLED:
        combined_injection = _inject_skeleton_into_blocks(combined_injection)
        combined_tokens = count_tokens(combined_injection)
    _t_skeleton_ms = (time.perf_counter() - _t4) * 1000

    _t5 = time.perf_counter()
    try:
        new_body = active_adapter.inject_system_context(body_bytes, combined_injection)
    except Exception:
        return body_bytes, 0, []
    _t_inject_ms = (time.perf_counter() - _t5) * 1000

    _total_ms = (time.perf_counter() - _t) * 1000
    # Store sub-step breakdown in SESSION for /stats and trace enrichment
    SESSION["vault_last_timing_ms"] = {
        "query_signal": round(_t_query_ms, 1),
        "term_resolver": round(_t_resolver_ms, 1),
        "bm25_search": round(_t_bm25_ms, 1),
        "skeleton": round(_t_skeleton_ms, 1),
        "inject_body": round(_t_inject_ms, 1),
        "total": round(_total_ms, 1),
    }

    return new_body, combined_tokens, source_refs
