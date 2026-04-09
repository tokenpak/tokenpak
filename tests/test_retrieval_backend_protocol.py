"""
Tests for tokenpak.agent.vault.backend_protocol
================================================

Covers:
- RetrievalBackend protocol conformance (isinstance checks)
- SemanticScorer protocol conformance
- RetrievalBackendBase mixin (compile_injection from search)
- load_custom_backend() error paths and success
- load_custom_scorer() error paths and success
- Built-in backends satisfy the protocol
- Augment mode wiring (SemanticScorer + score_and_sort)
"""

from __future__ import annotations

import importlib
import sys
import types
from typing import Dict, List, Tuple
from unittest.mock import MagicMock, patch

import pytest

from tokenpak.agent.vault.backend_protocol import (
    RetrievalBackend,
    RetrievalBackendBase,
    SemanticScorer,
    load_custom_backend,
    load_custom_scorer,
)


# ===========================================================================
# Fixtures: Mock backends and scorers
# ===========================================================================


class MockBackend:
    """Minimal valid backend satisfying RetrievalBackend protocol."""

    def __init__(self, vault_path: str = ""):
        self._vault_path = vault_path
        self._available = True
        self._blocks = [
            {
                "block_id": "b1",
                "source_path": "docs/readme.md",
                "content": "TokenPak is a smart proxy for LLM API calls.",
                "raw_tokens": 10,
            },
            {
                "block_id": "b2",
                "source_path": "docs/config.md",
                "content": "Configuration is done via environment variables.",
                "raw_tokens": 8,
            },
        ]

    @property
    def available(self) -> bool:
        return self._available

    def maybe_reload(self) -> None:
        pass

    def search(
        self, query: str, top_k: int = 5, min_score: float = 2.0
    ) -> List[Tuple[dict, float]]:
        # Simple substring matching for testing
        results = []
        for block in self._blocks:
            if any(w in block["content"].lower() for w in query.lower().split()):
                results.append((block, 5.0))
        return results[:top_k]

    def compile_injection(
        self, query: str, budget: int = 4000, top_k: int = 5, min_score: float = 2.0
    ) -> Tuple[str, int, List[str]]:
        results = self.search(query, top_k, min_score)
        if not results:
            return "", 0, []
        parts = [f"--- [{b['source_path']}] ---\n{b['content']}" for b, _ in results]
        text = "\n\n## Retrieved Context\n" + "\n\n".join(parts)
        return text, len(text) // 4, [b["source_path"] for b, _ in results]


class MockScorer:
    """Minimal valid scorer satisfying SemanticScorer protocol."""

    def __init__(self):
        pass

    def score(self, query: str, block_ids: List[str]) -> Dict[str, float]:
        # Return a fixed score for all blocks
        return {bid: 0.75 for bid in block_ids}


class IncompleteBackend:
    """Backend missing required methods — should fail protocol check."""

    def __init__(self, vault_path: str = ""):
        pass

    @property
    def available(self) -> bool:
        return True

    # Missing: maybe_reload, search, compile_injection


class IncompleteScorer:
    """Scorer missing the score method."""

    def __init__(self):
        pass

    # Missing: score()


class SearchOnlyBackend(RetrievalBackendBase):
    """Backend using RetrievalBackendBase — only implements search()."""

    def __init__(self, vault_path: str = ""):
        self._vault_path = vault_path

    @property
    def available(self) -> bool:
        return True

    def maybe_reload(self) -> None:
        pass

    def search(
        self, query: str, top_k: int = 5, min_score: float = 2.0
    ) -> List[Tuple[dict, float]]:
        return [
            (
                {
                    "block_id": "test1",
                    "source_path": "test.md",
                    "content": f"This is about {query}",
                    "raw_tokens": 5,
                },
                7.5,
            ),
            (
                {
                    "block_id": "test2",
                    "source_path": "other.md",
                    "content": f"Also relevant to {query}",
                    "raw_tokens": 5,
                },
                4.2,
            ),
        ][:top_k]


# ===========================================================================
# Test: Protocol isinstance checks
# ===========================================================================


class TestRetrievalBackendProtocol:
    """Tests for RetrievalBackend protocol."""

    def test_valid_backend_satisfies_protocol(self):
        backend = MockBackend("/tmp/vault")
        assert isinstance(backend, RetrievalBackend)

    def test_incomplete_backend_fails_protocol(self):
        backend = IncompleteBackend("/tmp/vault")
        assert not isinstance(backend, RetrievalBackend)

    def test_base_class_backend_satisfies_protocol(self):
        backend = SearchOnlyBackend("/tmp/vault")
        assert isinstance(backend, RetrievalBackend)

    def test_protocol_is_runtime_checkable(self):
        # Verify @runtime_checkable decorator works
        assert hasattr(RetrievalBackend, "__protocol_attrs__") or callable(
            getattr(RetrievalBackend, "_is_runtime_protocol", None)
        )


class TestSemanticScorerProtocol:
    """Tests for SemanticScorer protocol."""

    def test_valid_scorer_satisfies_protocol(self):
        scorer = MockScorer()
        assert isinstance(scorer, SemanticScorer)

    def test_incomplete_scorer_fails_protocol(self):
        scorer = IncompleteScorer()
        assert not isinstance(scorer, SemanticScorer)

    def test_protocol_is_runtime_checkable(self):
        assert hasattr(SemanticScorer, "__protocol_attrs__") or callable(
            getattr(SemanticScorer, "_is_runtime_protocol", None)
        )


# ===========================================================================
# Test: RetrievalBackendBase mixin
# ===========================================================================


class TestRetrievalBackendBase:
    """Tests for the default compile_injection provided by RetrievalBackendBase."""

    def test_compile_injection_uses_search_results(self):
        backend = SearchOnlyBackend("/tmp/vault")
        text, tokens, refs = backend.compile_injection("test query")
        assert text  # should have content
        assert tokens > 0
        assert "test.md" in refs
        assert "other.md" in refs
        assert "## Retrieved Context" in text

    def test_compile_injection_empty_results(self):
        class EmptyBackend(RetrievalBackendBase):
            @property
            def available(self):
                return True

            def maybe_reload(self):
                pass

            def search(self, query, top_k=5, min_score=2.0):
                return []

        backend = EmptyBackend()
        text, tokens, refs = backend.compile_injection("nothing")
        assert text == ""
        assert tokens == 0
        assert refs == []

    def test_compile_injection_respects_budget(self):
        class LargeBackend(RetrievalBackendBase):
            @property
            def available(self):
                return True

            def maybe_reload(self):
                pass

            def search(self, query, top_k=5, min_score=2.0):
                # Return blocks that would exceed a small budget
                return [
                    (
                        {
                            "block_id": f"b{i}",
                            "source_path": f"file{i}.md",
                            "content": "x" * 2000,
                            "raw_tokens": 500,
                        },
                        5.0 - i * 0.5,
                    )
                    for i in range(10)
                ]

        backend = LargeBackend()
        text, tokens, refs = backend.compile_injection("test", budget=600)
        # Should not include all 10 blocks due to budget
        assert len(refs) < 10
        assert tokens <= 600 or tokens <= 700  # allow slight overshoot from final recount

    def test_compile_injection_top_k(self):
        backend = SearchOnlyBackend("/tmp/vault")
        text, tokens, refs = backend.compile_injection("test query", top_k=1)
        assert len(refs) <= 1

    def test_not_implemented_errors_on_abstract_methods(self):
        base = RetrievalBackendBase()
        with pytest.raises(NotImplementedError):
            _ = base.available
        with pytest.raises(NotImplementedError):
            base.maybe_reload()
        with pytest.raises(NotImplementedError):
            base.search("test")

    def test_compile_injection_preserves_relevance_in_output(self):
        backend = SearchOnlyBackend("/tmp/vault")
        text, _, _ = backend.compile_injection("test query")
        assert "(relevance: 7.5)" in text
        assert "(relevance: 4.2)" in text


# ===========================================================================
# Test: load_custom_backend()
# ===========================================================================


class TestLoadCustomBackend:
    """Tests for the custom backend loader."""

    def test_rejects_non_custom_prefix(self):
        with pytest.raises(ValueError, match="must start with 'custom:'"):
            load_custom_backend("sqlite", "/tmp/vault")

    def test_rejects_no_dot_path(self):
        with pytest.raises(ValueError, match="custom:module.ClassName"):
            load_custom_backend("custom:JustAClass", "/tmp/vault")

    def test_rejects_missing_module(self):
        with pytest.raises(ImportError, match="Cannot import module"):
            load_custom_backend("custom:nonexistent_module_xyz.MyClass", "/tmp/vault")

    def test_rejects_missing_class(self):
        with pytest.raises(AttributeError, match="has no class"):
            load_custom_backend("custom:os.NonExistentClass123", "/tmp/vault")

    def test_loads_valid_backend(self):
        # Register our MockBackend in a temporary module
        mod = types.ModuleType("_test_mock_backend")
        mod.MockBackend = MockBackend
        sys.modules["_test_mock_backend"] = mod
        try:
            backend = load_custom_backend("custom:_test_mock_backend.MockBackend", "/tmp/vault")
            assert isinstance(backend, RetrievalBackend)
            assert backend.available
        finally:
            del sys.modules["_test_mock_backend"]

    def test_rejects_non_protocol_class(self):
        mod = types.ModuleType("_test_bad_backend")
        mod.BadBackend = IncompleteBackend
        sys.modules["_test_bad_backend"] = mod
        try:
            with pytest.raises(TypeError, match="does not satisfy"):
                load_custom_backend("custom:_test_bad_backend.BadBackend", "/tmp/vault")
        finally:
            del sys.modules["_test_bad_backend"]

    def test_passes_vault_path_to_constructor(self):
        received_paths = []

        class PathCapture:
            def __init__(self, vault_path):
                received_paths.append(vault_path)

            @property
            def available(self):
                return True

            def maybe_reload(self):
                pass

            def search(self, query, top_k=5, min_score=2.0):
                return []

            def compile_injection(self, query, budget=4000, top_k=5, min_score=2.0):
                return "", 0, []

        mod = types.ModuleType("_test_path_capture")
        mod.PathCapture = PathCapture
        sys.modules["_test_path_capture"] = mod
        try:
            load_custom_backend("custom:_test_path_capture.PathCapture", "/my/vault")
            assert received_paths == ["/my/vault"]
        finally:
            del sys.modules["_test_path_capture"]


# ===========================================================================
# Test: load_custom_scorer()
# ===========================================================================


class TestLoadCustomScorer:
    """Tests for the custom scorer loader."""

    def test_rejects_non_custom_prefix(self):
        with pytest.raises(ValueError, match="must start with 'custom:'"):
            load_custom_scorer("builtin_scorer")

    def test_rejects_no_dot_path(self):
        with pytest.raises(ValueError, match="custom:module.ClassName"):
            load_custom_scorer("custom:NoModule")

    def test_rejects_missing_module(self):
        with pytest.raises(ImportError, match="Cannot import module"):
            load_custom_scorer("custom:nonexistent_scorer_xyz.MyScorer")

    def test_loads_valid_scorer(self):
        mod = types.ModuleType("_test_mock_scorer")
        mod.MockScorer = MockScorer
        sys.modules["_test_mock_scorer"] = mod
        try:
            scorer = load_custom_scorer("custom:_test_mock_scorer.MockScorer")
            assert isinstance(scorer, SemanticScorer)
            result = scorer.score("test query", ["b1", "b2"])
            assert "b1" in result
            assert "b2" in result
        finally:
            del sys.modules["_test_mock_scorer"]

    def test_rejects_non_protocol_scorer(self):
        mod = types.ModuleType("_test_bad_scorer")
        mod.BadScorer = IncompleteScorer
        sys.modules["_test_bad_scorer"] = mod
        try:
            with pytest.raises(TypeError, match="does not satisfy"):
                load_custom_scorer("custom:_test_bad_scorer.BadScorer")
        finally:
            del sys.modules["_test_bad_scorer"]

    def test_scorer_instantiated_with_no_args(self):
        """Scorers should be instantiated with no arguments."""
        call_count = []

        class NoArgScorer:
            def __init__(self):
                call_count.append(1)

            def score(self, query, block_ids):
                return {}

        mod = types.ModuleType("_test_noarg_scorer")
        mod.NoArgScorer = NoArgScorer
        sys.modules["_test_noarg_scorer"] = mod
        try:
            load_custom_scorer("custom:_test_noarg_scorer.NoArgScorer")
            assert len(call_count) == 1
        finally:
            del sys.modules["_test_noarg_scorer"]


# ===========================================================================
# Test: Built-in backends satisfy protocol
# ===========================================================================


class TestBuiltInBackends:
    """Verify both built-in backends satisfy RetrievalBackend protocol."""

    def test_sqlite_backend_satisfies_protocol(self):
        from tokenpak.agent.vault.sqlite_backend import SQLiteRetrievalBackend

        backend = SQLiteRetrievalBackend("/tmp/test_vault")
        assert isinstance(backend, RetrievalBackend)

    def test_sqlite_backend_has_all_methods(self):
        from tokenpak.agent.vault.sqlite_backend import SQLiteRetrievalBackend

        backend = SQLiteRetrievalBackend("/tmp/test_vault")
        assert hasattr(backend, "available")
        assert callable(getattr(backend, "maybe_reload", None))
        assert callable(getattr(backend, "search", None))
        assert callable(getattr(backend, "compile_injection", None))

    def test_vault_index_like_class_satisfies_protocol(self):
        """Test that a class matching VaultIndex's interface satisfies the protocol."""

        class VaultIndexMock:
            @property
            def available(self):
                return True

            def maybe_reload(self):
                pass

            def search(self, query, top_k=5, min_score=2.0):
                return []

            def compile_injection(self, query, budget=4000, top_k=5, min_score=2.0):
                return "", 0, []

        mock = VaultIndexMock()
        assert isinstance(mock, RetrievalBackend)


# ===========================================================================
# Test: Augment mode (SemanticScorer + score_and_sort)
# ===========================================================================


class TestAugmentMode:
    """Tests for Augment mode: BM25 + SemanticScorer fusion."""

    def test_semantic_scores_fed_to_score_and_sort(self):
        from tokenpak.agent.vault.search import score_and_sort

        # Create BM25 results
        bm25_results = [
            (
                {
                    "block_id": "b1",
                    "source_path": "file1.md",
                    "content": "Database configuration guide",
                    "raw_tokens": 5,
                },
                3.0,
            ),
            (
                {
                    "block_id": "b2",
                    "source_path": "file2.md",
                    "content": "Authentication setup instructions",
                    "raw_tokens": 5,
                },
                5.0,
            ),
        ]

        # Semantic scorer gives b1 a much higher score
        semantic_scores = {"b1": 0.95, "b2": 0.1}

        # Without semantic scores
        results_no_sem = score_and_sort(bm25_results, query="db config")
        # b2 should be first (higher BM25 score with no semantic)

        # With semantic scores
        results_with_sem = score_and_sort(
            bm25_results, query="db config", semantic_scores=semantic_scores
        )

        # The semantic scores should influence the ordering
        # b1 has sem=0.95 which should boost it significantly
        ids_no_sem = [b["block_id"] for b, _ in results_no_sem]
        ids_with_sem = [b["block_id"] for b, _ in results_with_sem]

        # With high semantic score for b1, it should rank higher
        # (or at least the scores should change)
        scores_no_sem = {b["block_id"]: s for b, s in results_no_sem}
        scores_with_sem = {b["block_id"]: s for b, s in results_with_sem}

        # b1's score should increase with semantic scores
        assert scores_with_sem["b1"] > scores_no_sem["b1"]

    def test_scorer_returns_partial_results(self):
        """Scorer may return scores for only some block_ids."""
        from tokenpak.agent.vault.search import score_and_sort

        bm25_results = [
            (
                {
                    "block_id": "b1",
                    "source_path": "file1.md",
                    "content": "Hello world",
                    "raw_tokens": 2,
                },
                3.0,
            ),
            (
                {
                    "block_id": "b2",
                    "source_path": "file2.md",
                    "content": "Goodbye world",
                    "raw_tokens": 2,
                },
                3.0,
            ),
        ]

        # Scorer only returns score for b1
        partial_scores = {"b1": 0.9}

        results = score_and_sort(bm25_results, query="world", semantic_scores=partial_scores)
        assert len(results) == 2  # both blocks should still be in results
        # b1 should score higher because it has a semantic score
        scores = {b["block_id"]: s for b, s in results}
        assert scores["b1"] > scores["b2"]

    def test_scorer_empty_dict(self):
        """Empty semantic scores should be equivalent to no scorer."""
        from tokenpak.agent.vault.search import score_and_sort

        bm25_results = [
            (
                {
                    "block_id": "b1",
                    "source_path": "file1.md",
                    "content": "Test content",
                    "raw_tokens": 2,
                },
                3.0,
            ),
        ]

        results_none = score_and_sort(bm25_results, query="test")
        results_empty = score_and_sort(bm25_results, query="test", semantic_scores={})

        # Scores should be identical
        assert len(results_none) == len(results_empty)
        assert results_none[0][1] == results_empty[0][1]


# ===========================================================================
# Test: __init__.py exports
# ===========================================================================


class TestModuleExports:
    """Verify the module exports are correct."""

    def test_backend_protocol_exports(self):
        from tokenpak.agent.vault import (
            RetrievalBackend,
            RetrievalBackendBase,
            SemanticScorer,
            load_custom_backend,
            load_custom_scorer,
        )

        assert RetrievalBackend is not None
        assert SemanticScorer is not None
        assert RetrievalBackendBase is not None
        assert callable(load_custom_backend)
        assert callable(load_custom_scorer)

    def test_all_exports_listed(self):
        from tokenpak.agent.vault import __all__

        assert "RetrievalBackend" in __all__
        assert "SemanticScorer" in __all__
        assert "RetrievalBackendBase" in __all__
        assert "load_custom_backend" in __all__
        assert "load_custom_scorer" in __all__
