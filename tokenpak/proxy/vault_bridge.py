"""
tokenpak.proxy.vault_bridge — VaultIndex (BM25 search), global singletons,
and startup initialization for vault retrieval, term resolver, and capsule builder.

Extracted from runtime/proxy.py (L1177-1498) as part of TPK-RESTRUCTURE-004.
"""
import json
import logging
import math
import re
import threading
import time
from functools import lru_cache
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import (
    _cfg,
    VAULT_INDEX_PATH,
    VAULT_INDEX_RELOAD_INTERVAL,
    VAULT_AUTO_REINDEX_INTERVAL,
    RETRIEVAL_BACKEND,
    INJECT_BUDGET,
    INJECT_TOP_K,
    INJECT_MIN_SCORE,
    SKELETON_ENABLED,
)
from .token_cache import count_tokens

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

# Capsule builder feature flags
ENABLE_CAPSULE_BUILDER: bool = _cfg(
    "features.capsule_builder", False, "TOKENPAK_CAPSULE_BUILDER_ENABLED", bool
)
CAPSULE_MIN_CHARS: int = _cfg(
    "features.capsule_min_chars", 400, "TOKENPAK_CAPSULE_MIN_CHARS", int
)
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
        tokenize as _qe_tokenize,
    )
    _QE_AVAILABLE = True
except ImportError:
    _QE_AVAILABLE = False

# ---------------------------------------------------------------------------
# VaultIndex — BM25-searchable read-only index
# ---------------------------------------------------------------------------


class VaultIndex:
    """
    Read-only BM25-searchable index loaded from .tokenpak/index.json + blocks/.
    Reloads periodically to pick up git-pulled changes.
    """

    def __init__(self, tokenpak_dir: str):
        self.tokenpak_dir = Path(tokenpak_dir)
        self.blocks: Dict[str, dict] = {}  # block_id -> {meta + content}
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

    @property
    def available(self) -> bool:
        return len(self.blocks) > 0

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
            if mtime == self._last_mtime and self.blocks:
                self._last_loaded = now
                return
        except OSError:
            return

        self._load(index_path, mtime)
        self._last_loaded = now

    def _load(self, index_path: Path, mtime: float):
        """Load index + block contents, precompute BM25 stats."""
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

        for bid, bdata in items:
            content = ""
            content_file = blocks_dir / f"{bid}.txt"
            if content_file.exists():
                try:
                    content = content_file.read_text(errors="replace")
                except OSError:
                    continue

            new_blocks[bid] = {
                "block_id": bid,
                "source_path": bdata.get("source_path", bid),
                "risk_class": bdata.get("risk_class", "narrative"),
                "must_keep": bdata.get("must_keep", False),
                "raw_tokens": bdata.get("raw_tokens", 0),
                "content": content,
            }

        # Precompute BM25
        df: Dict[str, int] = {}
        block_tfs: Dict[str, Dict[str, int]] = {}
        block_dl: Dict[str, int] = {}  # precomputed doc lengths
        total_dl = 0

        for bid, block in new_blocks.items():
            terms = _bm25_tokenize(block["content"])
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

        print(
            f"  📚 Vault index loaded: {doc_count} blocks, {sum(b['raw_tokens'] for b in new_blocks.values()):,} tokens"
        )

    def search(
        self, query: str, top_k: int = 5, min_score: float = 2.0
    ) -> List[Tuple[dict, float]]:
        """BM25 search across vault blocks. Returns [(block_dict, score), ...]."""
        query_terms = _bm25_tokenize_query(query)
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
            content = block["content"]
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


# ---------------------------------------------------------------------------
# BM25 tokenizer — lru_cache gives 50x speedup on repeated queries
# ---------------------------------------------------------------------------


@lru_cache(maxsize=512)
def _bm25_tokenize(text: str) -> List[str]:
    return re.findall(r"[a-z0-9_]+", text.lower())


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
            from tokenpak.agent.semantic import TermResolver, TermResolverConfig  # type: ignore[assignment]

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
            from tokenpak.companion.capsules.builder import CapsuleBuilder as _CapsuleBuilder  # type: ignore[assignment]

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
    body_bytes: bytes, adapter=None
) -> "tuple[bytes, int, list[str]]":
    """
    Search vault index for relevant context and inject into the system prompt.
    Optionally resolves glossary terms and injects term cards.
    Returns (new_body_bytes, injected_tokens, source_refs).
    """
    # Lazy imports for proxy-layer dependencies (transferred to subpackages in A2c)
    from tokenpak.proxy.adapters.utils import _detect_adapter, extract_query_signal
    try:
        from tokenpak.core.runtime.proxy import SESSION  # type: ignore[import]
    except ImportError:
        SESSION = {}  # type: ignore[assignment]
    from tokenpak.vault.search import _compile_from_results, score_and_sort  # type: ignore[import]
    from tokenpak.vault.chunk_shaping import _inject_skeleton_into_blocks  # type: ignore[import]

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
        from tokenpak.core.runtime.proxy import SEMANTIC_SCORER as semantic_scorer  # type: ignore[import]
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

    # Apply skeleton extraction to code blocks in injection text (70-90% reduction on code)
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
