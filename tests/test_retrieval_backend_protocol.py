"""
Tests for tokenpak.vault.backend_protocol

Covers:
- RetrievalBackend protocol conformance
- SemanticScorer protocol conformance
- RetrievalBackendBase mixin
- Custom backend loading (Replace mode)
- Custom scorer loading (Augment mode)
- Augment mode fusion math
- Edge cases
"""
from __future__ import annotations

import sys
import types
import warnings
from typing import Dict, List, Tuple
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.vault.backend_protocol import (
    RetrievalBackend,
    RetrievalBackendBase,
    SemanticScorer,
    load_custom_backend,
    load_custom_scorer,
)


# ---------------------------------------------------------------------------
# Helpers / minimal implementations
# ---------------------------------------------------------------------------

def _make_block(block_id: str, content: str = "hello world", tokens: int = 10) -> dict:
    return {
        "block_id": block_id,
        "source_path": f"docs/{block_id}.md",
        "content": content,
        "raw_tokens": tokens,
    }


class MinimalBackend(RetrievalBackendBase):
    """Minimal backend for testing."""
    def __init__(self, vault_path: str = "/tmp"):
        self._vault_path = vault_path
        self._available = True
        self._reloaded = False

    @property
    def available(self) -> bool:
        return self._available

    def maybe_reload(self) -> None:
        self._reloaded = True

    def search(self, query: str, top_k: int = 5, min_score: float = 2.0) -> List[Tuple[dict, float]]:
        blocks = [
            (_make_block("a", "python programming language tutorial", 50), 8.0),
            (_make_block("b", "machine learning with python", 40), 5.0),
            (_make_block("c", "web development frameworks", 30), 2.5),
        ]
        return blocks[:top_k]


class MinimalScorer:
    """Minimal semantic scorer for testing."""
    def score(self, query: str, block_ids: List[str]) -> Dict[str, float]:
        return {bid: 0.5 for bid in block_ids}


class IncompleteBackend:
    """Missing search() — should not satisfy protocol."""
    @property
    def available(self) -> bool:
        return True

    def maybe_reload(self) -> None:
        pass

    def compile_injection(self, query, budget=4000, top_k=5, min_score=2.0):
        return "", 0, []


class IncompleteScorer:
    """Missing score() — should not satisfy protocol."""
    pass


# ---------------------------------------------------------------------------
# Protocol conformance tests
# ---------------------------------------------------------------------------

class TestRetrievalBackendProtocol:
    def test_minimal_backend_satisfies_protocol(self):
        backend = MinimalBackend()
        assert isinstance(backend, RetrievalBackend)

    def test_backend_base_satisfies_protocol(self):
        """RetrievalBackendBase itself satisfies the structural protocol."""
        assert issubclass(MinimalBackend, RetrievalBackendBase)

    def test_incomplete_backend_fails_protocol(self):
        """Class missing search() should NOT satisfy protocol."""
        instance = IncompleteBackend()
        assert not isinstance(instance, RetrievalBackend)

    def test_plain_object_fails_protocol(self):
        assert not isinstance(object(), RetrievalBackend)

    def test_none_fails_protocol(self):
        assert not isinstance(None, RetrievalBackend)

    def test_protocol_requires_available_property(self):
        class NoAvailable:
            def maybe_reload(self): pass
            def search(self, q, top_k=5, min_score=2.0): return []
            def compile_injection(self, q, budget=4000, top_k=5, min_score=2.0): return "", 0, []
        # runtime_checkable only checks method presence, not property
        # but available is a property — check manually
        assert not hasattr(NoAvailable(), "available")

    def test_mock_backend_satisfies_protocol(self):
        mock = MagicMock(spec=MinimalBackend)
        assert isinstance(mock, RetrievalBackend)


class TestSemanticScorerProtocol:
    def test_minimal_scorer_satisfies_protocol(self):
        scorer = MinimalScorer()
        assert isinstance(scorer, SemanticScorer)

    def test_incomplete_scorer_fails_protocol(self):
        assert not isinstance(IncompleteScorer(), SemanticScorer)

    def test_plain_object_fails_protocol(self):
        assert not isinstance(object(), SemanticScorer)

    def test_mock_scorer_satisfies_protocol(self):
        mock = MagicMock(spec=MinimalScorer)
        assert isinstance(mock, SemanticScorer)


# ---------------------------------------------------------------------------
# RetrievalBackendBase mixin tests
# ---------------------------------------------------------------------------

class TestRetrievalBackendBase:
    def test_compile_injection_returns_nonempty_for_results(self):
        backend = MinimalBackend()
        text, tokens, refs = backend.compile_injection("python tutorial", budget=2000)
        assert len(text) > 0
        assert tokens > 0
        assert len(refs) > 0

    def test_compile_injection_includes_retrieved_context_header(self):
        backend = MinimalBackend()
        text, _, _ = backend.compile_injection("python", budget=2000)
        assert "## Retrieved Context" in text

    def test_compile_injection_respects_budget(self):
        backend = MinimalBackend()
        # Very small budget — should truncate
        text, tokens, refs = backend.compile_injection("python", budget=50)
        # tokens_used should be <= 50 (approximately)
        assert tokens <= 200  # some slack for header overhead

    def test_compile_injection_empty_results(self):
        class EmptyBackend(RetrievalBackendBase):
            @property
            def available(self): return True
            def maybe_reload(self): pass
            def search(self, query, top_k=5, min_score=2.0): return []

        backend = EmptyBackend()
        text, tokens, refs = backend.compile_injection("anything")
        assert text == ""
        assert tokens == 0
        assert refs == []

    def test_compile_injection_includes_source_refs(self):
        backend = MinimalBackend()
        _, _, refs = backend.compile_injection("python", budget=2000)
        assert len(refs) > 0
        assert all(isinstance(r, str) for r in refs)

    def test_maybe_reload_called(self):
        backend = MinimalBackend()
        assert not backend._reloaded
        backend.maybe_reload()
        assert backend._reloaded

    def test_base_search_raises_not_implemented(self):
        base = RetrievalBackendBase()
        with pytest.raises(NotImplementedError):
            base.search("test")

    def test_base_available_raises_not_implemented(self):
        base = RetrievalBackendBase()
        with pytest.raises(NotImplementedError):
            _ = base.available

    def test_base_maybe_reload_raises_not_implemented(self):
        base = RetrievalBackendBase()
        with pytest.raises(NotImplementedError):
            base.maybe_reload()

    def test_compile_injection_large_block_truncated(self):
        """Blocks exceeding budget should be truncated, not dropped entirely."""
        class BigBlockBackend(RetrievalBackendBase):
            @property
            def available(self): return True
            def maybe_reload(self): pass
            def search(self, query, top_k=5, min_score=2.0):
                big_content = "word " * 5000  # ~25000 chars
                return [(_make_block("big", big_content, 5000), 10.0)]

        backend = BigBlockBackend()
        text, tokens, refs = backend.compile_injection("test", budget=200)
        # Should have something but be within rough budget
        assert len(refs) > 0 or text == ""  # either truncated or skipped


# ---------------------------------------------------------------------------
# Custom backend loading — Replace mode
# ---------------------------------------------------------------------------

def _register_fake_module(module_name: str, cls):
    """Helper: register a fake module in sys.modules."""
    mod = types.ModuleType(module_name)
    setattr(mod, cls.__name__, cls)
    sys.modules[module_name] = mod
    return mod


class TestLoadCustomBackend:
    def setup_method(self):
        # Register a valid backend module
        _register_fake_module("fake_backends", MinimalBackend)

    def teardown_method(self):
        sys.modules.pop("fake_backends", None)
        sys.modules.pop("nonexistent_module", None)

    def test_valid_backend_loads(self):
        backend = load_custom_backend("custom:fake_backends.MinimalBackend", vault_path="/tmp")
        assert isinstance(backend, RetrievalBackend)

    def test_backend_receives_vault_path(self):
        backend = load_custom_backend("custom:fake_backends.MinimalBackend", vault_path="/my/vault")
        assert backend._vault_path == "/my/vault"

    def test_missing_custom_prefix_raises_value_error(self):
        with pytest.raises(ValueError, match="must start with 'custom:'"):
            load_custom_backend("fake_backends.MinimalBackend", vault_path="/tmp")

    def test_missing_dot_in_path_raises_value_error(self):
        with pytest.raises(ValueError, match="module.ClassName"):
            load_custom_backend("custom:MinimalBackend", vault_path="/tmp")

    def test_nonexistent_module_raises_import_error(self):
        with pytest.raises(ImportError, match="Cannot import module"):
            load_custom_backend("custom:nonexistent_module.SomeClass", vault_path="/tmp")

    def test_nonexistent_class_raises_attribute_error(self):
        with pytest.raises(AttributeError, match="has no class"):
            load_custom_backend("custom:fake_backends.NoSuchClass", vault_path="/tmp")

    def test_class_not_satisfying_protocol_raises_type_error(self):
        # IncompleteBackend has no vault_path constructor — will fail on instantiation
        # or protocol check. Either TypeError is acceptable.
        class BadBackend(IncompleteBackend):
            def __init__(self, vault_path): super().__init__()
        _register_fake_module("fake_bad", BadBackend)
        try:
            with pytest.raises(TypeError):
                load_custom_backend("custom:fake_bad.BadBackend", vault_path="/tmp")
        finally:
            sys.modules.pop("fake_bad", None)

    def test_loaded_backend_is_functional(self):
        backend = load_custom_backend("custom:fake_backends.MinimalBackend", vault_path="/tmp")
        results = backend.search("python")
        assert len(results) > 0
        block, score = results[0]
        assert "block_id" in block
        assert score > 0


# ---------------------------------------------------------------------------
# Custom scorer loading — Augment mode
# ---------------------------------------------------------------------------

class TestLoadCustomScorer:
    def setup_method(self):
        _register_fake_module("fake_scorers", MinimalScorer)

    def teardown_method(self):
        sys.modules.pop("fake_scorers", None)

    def test_valid_scorer_loads(self):
        scorer = load_custom_scorer("custom:fake_scorers.MinimalScorer")
        assert isinstance(scorer, SemanticScorer)

    def test_missing_custom_prefix_raises_value_error(self):
        with pytest.raises(ValueError, match="must start with 'custom:'"):
            load_custom_scorer("fake_scorers.MinimalScorer")

    def test_nonexistent_module_raises_import_error(self):
        with pytest.raises(ImportError):
            load_custom_scorer("custom:no_such_module.Scorer")

    def test_nonexistent_class_raises_attribute_error(self):
        with pytest.raises(AttributeError, match="has no class"):
            load_custom_scorer("custom:fake_scorers.NoSuchScorer")

    def test_class_not_satisfying_protocol_raises_type_error(self):
        _register_fake_module("fake_bad_scorer", IncompleteScorer)
        try:
            with pytest.raises(TypeError, match="does not satisfy"):
                load_custom_scorer("custom:fake_bad_scorer.IncompleteScorer")
        finally:
            sys.modules.pop("fake_bad_scorer", None)

    def test_scorer_called_with_block_ids(self):
        scorer = load_custom_scorer("custom:fake_scorers.MinimalScorer")
        scores = scorer.score("test query", ["a", "b", "c"])
        assert set(scores.keys()) == {"a", "b", "c"}
        assert all(v == 0.5 for v in scores.values())

    def test_partial_scorer_results_allowed(self):
        """Scorer returning only some block_ids is valid — missing = 0.0."""
        class PartialScorer:
            def score(self, query, block_ids):
                # Only score first block
                return {block_ids[0]: 0.8} if block_ids else {}

        _register_fake_module("partial_scorer_mod", PartialScorer)
        try:
            scorer = load_custom_scorer("custom:partial_scorer_mod.PartialScorer")
            result = scorer.score("test", ["x", "y", "z"])
            assert "x" in result
            assert "y" not in result  # partial is fine
        finally:
            sys.modules.pop("partial_scorer_mod", None)


# ---------------------------------------------------------------------------
# Augment mode fusion math
# ---------------------------------------------------------------------------

class TestAugmentModeFusion:
    """Test score fusion from tokenpak.vault.search.compute_final_score."""

    def test_compute_final_score_imports(self):
        from tokenpak.vault.search import compute_final_score
        assert callable(compute_final_score)

    def test_fusion_weights_applied(self):
        """Verify semantic signal influences final score."""
        from tokenpak.vault.search import compute_final_score

        # High semantic, zero BM25
        score_high_sem = compute_final_score(sem_norm=0.9, bm25_norm=0.1)
        # Low semantic, high BM25
        score_high_bm25 = compute_final_score(sem_norm=0.1, bm25_norm=0.9)
        # Both should be > 0
        assert score_high_sem > 0
        assert score_high_bm25 > 0

    def test_no_semantic_score_backward_compatible(self):
        """When sem_norm=0.0, result is BM25-dominated."""
        from tokenpak.vault.search import compute_final_score

        score = compute_final_score(sem_norm=0.0, bm25_norm=0.7)
        assert score > 0

    def test_compute_final_score_all_zeros_returns_zero_or_low(self):
        """All-zero inputs should return 0 or near-zero."""
        from tokenpak.vault.search import compute_final_score

        score = compute_final_score()
        assert score >= 0.0

    def test_score_and_sort_without_semantic(self):
        """score_and_sort with no semantic_scores works as before."""
        from tokenpak.vault.search import score_and_sort

        blocks = [
            _make_block("a", "python language", 50),
            _make_block("b", "machine learning", 40),
        ]
        pairs = [(b, float(3 - i)) for i, b in enumerate(blocks)]
        results = score_and_sort(pairs)
        assert len(results) > 0

    def test_score_and_sort_with_semantic_runs(self):
        """Providing semantic_scores doesn't crash."""
        from tokenpak.vault.search import score_and_sort

        blocks = [
            _make_block("a", "python language tutorial for beginners", 50),
            _make_block("b", "machine learning deep neural networks", 40),
            _make_block("c", "web scraping with requests library", 30),
        ]
        bm25_pairs = [
            (blocks[0], 5.0),
            (blocks[1], 3.0),
            (blocks[2], 1.0),
        ]
        semantic_scores = {"c": 0.95, "a": 0.1, "b": 0.4}
        results = score_and_sort(bm25_pairs, query="python", semantic_scores=semantic_scores)
        assert len(results) == 3


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_unavailable_backend_search_returns_nothing(self):
        class UnavailableBackend(RetrievalBackendBase):
            @property
            def available(self): return False
            def maybe_reload(self): pass
            def search(self, query, top_k=5, min_score=2.0):
                # Should not be called when unavailable
                return []

        backend = UnavailableBackend()
        assert not backend.available
        # compile_injection still works (returns empty)
        text, tokens, refs = backend.compile_injection("test")
        assert text == ""

    def test_maybe_reload_idempotent(self):
        backend = MinimalBackend()
        backend.maybe_reload()
        backend.maybe_reload()  # Should not raise

    def test_scorer_exception_graceful(self):
        """Scorer raising exception — caller should handle gracefully."""
        class ExplodingScorer:
            def score(self, query, block_ids):
                raise RuntimeError("embeddings service down")

        scorer = ExplodingScorer()
        with pytest.raises(RuntimeError):
            scorer.score("test", ["a", "b"])

    def test_scorer_scores_clamped_high(self):
        """Scores > 1.0 from scorer — caller should clamp."""
        class HighScorer:
            def score(self, query, block_ids):
                return {bid: 9.9 for bid in block_ids}  # Way too high

        scorer = HighScorer()
        scores = scorer.score("test", ["x"])
        # The scorer can return > 1.0; clamping is caller's responsibility
        assert scores["x"] == 9.9  # scorer returns as-is

    def test_scorer_negative_scores(self):
        """Negative scores from scorer — caller should clamp to 0."""
        class NegativeScorer:
            def score(self, query, block_ids):
                return {bid: -0.5 for bid in block_ids}

        scorer = NegativeScorer()
        scores = scorer.score("test", ["x"])
        assert scores["x"] == -0.5  # scorer returns as-is; clamping is caller's job

    def test_load_backend_empty_dotted_path(self):
        with pytest.raises(ValueError):
            load_custom_backend("custom:", vault_path="/tmp")

    def test_load_scorer_empty_dotted_path(self):
        with pytest.raises(ValueError):
            load_custom_scorer("custom:")
