"""
TokenPak — Pluggable Retrieval Backend Protocol
================================================

Defines formal protocols for vault retrieval backends and semantic scorers,
enabling users to plug in their own search infrastructure (pgvector, Elasticsearch,
Qdrant, Weaviate, SQLite FTS5, etc.) via a single config line.

Three integration modes:

1. **Default** — Pure BM25 via built-in json_blocks or sqlite backend.
2. **Replace** — User's backend handles all retrieval, BM25 bypassed entirely.
   Config: ``TOKENPAK_RETRIEVAL_BACKEND=custom:module.path.ClassName``
3. **Augment** — BM25 runs normally, user's scorer supplies semantic similarity
   scores that fuse via the multi-signal scorer (0.45 BM25 + 0.45 semantic + 0.10 meta).
   Config: ``TOKENPAK_SEMANTIC_BACKEND=custom:module.path.ClassName``

Usage::

    from tokenpak.vault.backend_protocol import (
        RetrievalBackend,
        SemanticScorer,
        RetrievalBackendBase,
        load_custom_backend,
        load_custom_scorer,
    )

    # Verify a backend satisfies the protocol
    assert isinstance(my_backend, RetrievalBackend)

    # Load a custom backend from a config string
    backend = load_custom_backend("custom:mymodule.MyBackend", vault_path="/path")
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable, Mapping
from typing import Dict, List, Protocol, Tuple, cast, runtime_checkable

logger = logging.getLogger("tokenpak.vault.backend_protocol")

RetrievalRecord = Mapping[str, object]
RetrievalResult = tuple[RetrievalRecord, float]

# ---------------------------------------------------------------------------
# RetrievalBackend Protocol (Replace mode)
# ---------------------------------------------------------------------------


@runtime_checkable
class RetrievalBackend(Protocol):
    """Protocol for vault retrieval backends.

    Any class implementing these methods can serve as a vault search backend.
    TokenPak ships with two built-in backends:

    - ``json_blocks`` (default): In-memory BM25 over index.json + blocks/\\*.txt
    - ``sqlite``: SQLite-backed BM25 with incremental updates

    Users can implement this protocol for custom backends:

    - Vector databases (pgvector, Qdrant, Weaviate, Pinecone)
    - Full-text search engines (Elasticsearch, Meilisearch, SQLite FTS5)
    - Hybrid/custom retrieval pipelines

    Minimal implementation requires only ``available``, ``maybe_reload()``, and
    ``search()``. Extend :class:`RetrievalBackendBase` to get a default
    ``compile_injection()`` for free.
    """

    @property
    def available(self) -> bool:
        """Return True if the backend is loaded and ready to search."""
        ...

    def maybe_reload(self) -> None:
        """Check if the underlying data has changed and reload if needed.

        Called periodically by the proxy's reload timer (default: every 5 min).
        Implementations should be idempotent and fast when no changes exist.
        """
        ...

    def search(self, query: str, top_k: int = 5, min_score: float = 2.0) -> List[RetrievalResult]:
        """Search for relevant blocks.

        Args:
            query: Natural language search query.
            top_k: Maximum number of results.
            min_score: Minimum relevance score (backend-specific scale).

        Returns:
            List of (block_dict, score) tuples, sorted by relevance descending.
            block_dict must contain at minimum:

            - ``block_id``: str — unique identifier
            - ``source_path``: str — relative path to source file
            - ``content``: str — block text content
            - ``raw_tokens``: int — approximate token count
        """
        ...

    def compile_injection(
        self, query: str, budget: int = 4000, top_k: int = 5, min_score: float = 2.0
    ) -> Tuple[str, int, List[str]]:
        """Search and compile injection text within a token budget.

        Args:
            query: Natural language search query.
            budget: Maximum tokens for the injection text.
            top_k: Maximum number of results.
            min_score: Minimum relevance score.

        Returns:
            Tuple of (injection_text, tokens_used, source_refs).
            Returns ("", 0, []) if no relevant results found.
        """
        ...


# ---------------------------------------------------------------------------
# SemanticScorer Protocol (Augment mode)
# ---------------------------------------------------------------------------


@runtime_checkable
class SemanticScorer(Protocol):
    """Protocol for semantic scoring backends (Augment mode).

    Called after BM25 retrieval with candidate block IDs. Returns similarity
    scores that fuse with BM25 via the multi-signal scorer.

    In Augment mode:

    1. BM25 runs and returns candidate blocks (as today)
    2. SemanticScorer receives the query + candidate block IDs
    3. It returns similarity scores for those candidates
    4. ``score_and_sort()`` fuses both signals using existing weights

    Example implementation::

        class PgVectorScorer:
            def __init__(self):
                self.conn = psycopg2.connect(os.environ["DATABASE_URL"])

            def score(self, query: str, block_ids: list[str]) -> dict[str, float]:
                embedding = self._embed(query)
                # Query pgvector for similarity scores for these specific block_ids
                ...
                return {bid: similarity for bid, similarity in results}
    """

    def score(self, query: str, block_ids: List[str]) -> Dict[str, float]:
        """Return semantic similarity scores for given blocks.

        Args:
            query: Natural language search query.
            block_ids: Block IDs from BM25 results to score.

        Returns:
            Dict mapping block_id → similarity score in [0.0, 1.0].
            Missing block_ids are treated as 0.0 by the caller.
        """
        ...


# ---------------------------------------------------------------------------
# RetrievalBackendBase — default compile_injection() from search()
# ---------------------------------------------------------------------------


class RetrievalBackendBase:
    """Base class providing default ``compile_injection()`` from ``search()``.

    Subclass this and implement ``search()``, ``available``, and ``maybe_reload()``.
    ``compile_injection()`` is provided for free — it calls ``search()`` and
    formats the results within the token budget.

    This lowers the bar for custom backends to ~20 lines of user code.

    Example::

        from tokenpak.vault.backend_protocol import RetrievalBackendBase

        class MyBackend(RetrievalBackendBase):
            def __init__(self, vault_path: str):
                self._ready = True

            @property
            def available(self) -> bool:
                return self._ready

            def maybe_reload(self) -> None:
                pass  # my backend auto-refreshes

            def search(self, query, top_k=5, min_score=2.0):
                # ... your search logic here ...
                return [(block_dict, score), ...]
    """

    @property
    def available(self) -> bool:
        raise NotImplementedError("Subclasses must implement 'available'")

    def maybe_reload(self) -> None:
        raise NotImplementedError("Subclasses must implement 'maybe_reload()'")

    def search(self, query: str, top_k: int = 5, min_score: float = 2.0) -> List[RetrievalResult]:
        raise NotImplementedError("Subclasses must implement 'search()'")

    def compile_injection(
        self, query: str, budget: int = 4000, top_k: int = 5, min_score: float = 2.0
    ) -> Tuple[str, int, List[str]]:
        """Search and compile injection text within token budget.

        Uses ``self.search()`` to get results, then formats them with source
        path and relevance headers, respecting the token budget.

        Returns:
            Tuple of (injection_text, tokens_used, source_refs).
            Returns ("", 0, []) if no relevant results found.
        """
        results = self.search(query, top_k=top_k, min_score=min_score)
        if not results:
            return "", 0, []

        # Lazy import to avoid circular imports
        count_tokens_fn = _get_count_tokens_fn()

        injection_parts: List[str] = []
        tokens_used = 0
        source_refs: List[str] = []

        for block, score in results:
            content_value = block.get("content", "")
            content = content_value if isinstance(content_value, str) else str(content_value)
            raw_tokens = block.get("raw_tokens", 0)
            block_tokens = (
                raw_tokens
                if isinstance(raw_tokens, int) and raw_tokens > 0
                else count_tokens_fn(content)
            )

            remaining = budget - tokens_used
            if remaining <= 100:
                break

            if block_tokens > remaining:
                # Truncate to fit within budget (char-based approximation)
                char_limit = remaining * 4
                content = content[:char_limit].rsplit("\n", 1)[0]
                block_tokens = count_tokens_fn(content)

            source_value = block.get("source_path", block.get("block_id", "unknown"))
            source_path = source_value if isinstance(source_value, str) else str(source_value)
            injection_parts.append(f"--- [{source_path}] (relevance: {score:.1f}) ---\n{content}")
            tokens_used += block_tokens
            source_refs.append(source_path)

        if not injection_parts:
            return "", 0, []

        header = "\n\n## Retrieved Context\n"
        injection_text = header + "\n\n".join(injection_parts)
        tokens_used = count_tokens_fn(injection_text)

        return injection_text, tokens_used, source_refs


# ---------------------------------------------------------------------------
# Custom backend/scorer loading
# ---------------------------------------------------------------------------


def load_custom_backend(config_value: str, vault_path: str) -> RetrievalBackend:
    """Load a custom retrieval backend from a ``custom:module.path.ClassName`` config string.

    The class is instantiated with ``vault_path`` as the sole argument.
    The resulting instance is validated against the :class:`RetrievalBackend` protocol.

    Args:
        config_value: Config string in ``custom:module.path.ClassName`` format.
        vault_path: Path to the vault index directory, passed to the backend constructor.

    Returns:
        An instance satisfying :class:`RetrievalBackend`.

    Raises:
        ValueError: If the config string format is invalid.
        ImportError: If the module cannot be imported.
        AttributeError: If the class doesn't exist in the module.
        TypeError: If the instantiated class doesn't satisfy the protocol.
    """
    if not config_value.startswith("custom:"):
        raise ValueError(f"Custom backend config must start with 'custom:', got: {config_value!r}")

    dotted_path = config_value[7:]  # strip "custom:"
    if "." not in dotted_path:
        raise ValueError(f"Custom backend must be 'custom:module.ClassName', got: {config_value!r}")

    module_path, class_name = dotted_path.rsplit(".", 1)

    try:
        mod = importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(
            f"Cannot import module '{module_path}' for custom retrieval backend: {e}"
        ) from e

    try:
        cls = getattr(mod, class_name)
    except AttributeError as e:
        raise AttributeError(f"Module '{module_path}' has no class '{class_name}': {e}") from e

    try:
        instance = cls(vault_path)
    except TypeError as e:
        raise TypeError(
            f"Failed to instantiate {class_name}(vault_path={vault_path!r}): {e}. "
            f"Custom backends must accept vault_path as the first positional argument."
        ) from e

    if not isinstance(instance, RetrievalBackend):
        missing = []
        if not hasattr(instance, "available"):
            missing.append("available (property)")
        if not callable(getattr(instance, "maybe_reload", None)):
            missing.append("maybe_reload()")
        if not callable(getattr(instance, "search", None)):
            missing.append("search()")
        if not callable(getattr(instance, "compile_injection", None)):
            missing.append("compile_injection()")
        raise TypeError(
            f"{cls.__name__} does not satisfy the RetrievalBackend protocol. "
            f"Missing or incompatible: {', '.join(missing) if missing else 'signature mismatch'}"
        )

    logger.info("Loaded custom retrieval backend: %s.%s", module_path, class_name)
    return instance


def load_custom_scorer(config_value: str) -> SemanticScorer:
    """Load a custom semantic scorer from a ``custom:module.path.ClassName`` config string.

    The class is instantiated with no arguments.

    Args:
        config_value: Config string in ``custom:module.path.ClassName`` format.

    Returns:
        An instance satisfying :class:`SemanticScorer`.

    Raises:
        ValueError: If the config string format is invalid.
        ImportError: If the module cannot be imported.
        AttributeError: If the class doesn't exist in the module.
        TypeError: If the instantiated class doesn't satisfy the protocol.
    """
    if not config_value.startswith("custom:"):
        raise ValueError(f"Custom scorer config must start with 'custom:', got: {config_value!r}")

    dotted_path = config_value[7:]
    if "." not in dotted_path:
        raise ValueError(f"Custom scorer must be 'custom:module.ClassName', got: {config_value!r}")

    module_path, class_name = dotted_path.rsplit(".", 1)

    try:
        mod = importlib.import_module(module_path)
    except ImportError as e:
        raise ImportError(
            f"Cannot import module '{module_path}' for custom semantic scorer: {e}"
        ) from e

    try:
        cls = getattr(mod, class_name)
    except AttributeError as e:
        raise AttributeError(f"Module '{module_path}' has no class '{class_name}': {e}") from e

    try:
        instance = cls()
    except TypeError as e:
        raise TypeError(
            f"Failed to instantiate {class_name}(): {e}. Custom scorers must accept no arguments."
        ) from e

    if not isinstance(instance, SemanticScorer):
        raise TypeError(
            f"{cls.__name__} does not satisfy the SemanticScorer protocol. "
            f"It must implement: score(query: str, block_ids: List[str]) -> Dict[str, float]"
        )

    logger.info("Loaded custom semantic scorer: %s.%s", module_path, class_name)
    return instance


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _get_count_tokens_fn() -> Callable[[str], int]:
    """Lazily import the token counting function."""
    try:
        from tokenpak.tokens import count_tokens

        return cast(Callable[[str], int], count_tokens)
    except ImportError:
        # Rough fallback: 4 chars ≈ 1 token
        def _fallback_count(text: str) -> int:
            return max(1, len(text) // 4)

        return _fallback_count
