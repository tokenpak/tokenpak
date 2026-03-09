"""
Cross-adapter integration tests for TokenPak VectorDB.

Tests interface parity across all 4 adapters (Chroma, Pinecone, Qdrant, Weaviate)
plus adapter-specific behavior. All external DB connections are mocked.
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from tokenpak_vectordb import (
    ChromaAdapter,
    PineconeAdapter,
    QdrantAdapter,
    WeaviateAdapter,
    VectorBlock,
)
from tokenpak_vectordb.base import BatchQueryResult


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

SAMPLE_VECTOR = [0.1, 0.2, 0.3, 0.4, 0.5]


def _make_chroma_adapter(distance_metric="l2"):
    collection = MagicMock()
    collection.name = "integration-test"
    collection.query.return_value = {
        "ids": [["doc-a", "doc-b", "doc-c"]],
        "documents": [["Alpha content", "Beta content", "Gamma content"]],
        "distances": [[0.1, 0.4, 0.9]],
        "metadatas": [[{"source": "wiki"}, {"source": "book"}, {"source": "web"}]],
    }
    return ChromaAdapter(collection, distance_metric=distance_metric)


def _make_pinecone_adapter():
    index = MagicMock()
    # Pinecone returns object-style matches
    def _make_match(id_, score, text):
        m = MagicMock()
        m.id = id_
        m.score = score
        m.metadata = {"text": text, "category": "test"}
        return m

    index.query.return_value = MagicMock(
        matches=[
            _make_match("pin-1", 0.95, "Pinecone result one"),
            _make_match("pin-2", 0.80, "Pinecone result two"),
        ]
    )
    return PineconeAdapter(index, namespace="test-ns")


def _make_qdrant_adapter():
    client = MagicMock()

    def _make_hit(id_, score, text):
        h = MagicMock()
        h.id = id_
        h.score = score
        h.payload = {"text": text, "tag": "integration"}
        return h

    client.search.return_value = [
        _make_hit("q-1", 0.92, "Qdrant cosine hit one"),
        _make_hit("q-2", 0.75, "Qdrant cosine hit two"),
        _make_hit("q-3", 0.60, "Qdrant cosine hit three"),
    ]
    return QdrantAdapter(client, collection_name="test-col", score_metric="cosine")


def _make_weaviate_adapter():
    client = MagicMock()
    # Simulate v3 client (no 'collections' attr)
    del client.collections  # remove mock auto-attribute
    client.query = MagicMock()

    q_chain = (
        client.query
        .get.return_value
        .with_limit.return_value
        .with_additional.return_value
    )
    q_chain.with_near_text.return_value.do.return_value = {
        "data": {
            "Get": {
                "Document": [
                    {
                        "text": "Weaviate doc one",
                        "category": "info",
                        "_additional": {"id": "w-1", "certainty": 0.88, "distance": None},
                    },
                    {
                        "text": "Weaviate doc two",
                        "category": "guide",
                        "_additional": {"id": "w-2", "certainty": 0.72, "distance": None},
                    },
                ]
            }
        }
    }
    return WeaviateAdapter(client, collection_name="Document")


# All 4 adapters for parity tests
ALL_ADAPTERS = [
    _make_chroma_adapter,
    _make_pinecone_adapter,
    _make_qdrant_adapter,
    _make_weaviate_adapter,
]


# ---------------------------------------------------------------------------
# TestAdapterParity — interface parity across all 4 adapters
# ---------------------------------------------------------------------------

class TestAdapterParity:
    """Verify all 4 adapters expose the same public interface."""

    def test_all_adapters_have_query_as_blocks(self):
        """Every adapter must implement query_as_blocks()."""
        for factory in ALL_ADAPTERS:
            adapter = factory()
            assert hasattr(adapter, "query_as_blocks"), (
                f"{type(adapter).__name__} missing query_as_blocks"
            )
            assert callable(adapter.query_as_blocks), (
                f"{type(adapter).__name__}.query_as_blocks is not callable"
            )

    def test_all_adapters_return_list(self):
        """query_as_blocks() must return a list for every adapter."""
        for factory in ALL_ADAPTERS:
            adapter = factory()
            result = adapter.query_as_blocks(SAMPLE_VECTOR, limit=3)
            assert isinstance(result, list), (
                f"{type(adapter).__name__} returned {type(result)}, expected list"
            )

    def test_all_adapters_accept_block_type_param(self):
        """All adapters must accept a block_type kwarg and honour it."""
        for factory in ALL_ADAPTERS:
            adapter = factory()
            blocks = adapter.query_as_blocks(
                SAMPLE_VECTOR, limit=2, block_type="knowledge"
            )
            for b in blocks:
                assert isinstance(b, VectorBlock), (
                    f"{type(adapter).__name__} returned non-VectorBlock item"
                )
                assert b.block_type == "knowledge", (
                    f"{type(adapter).__name__}: expected block_type='knowledge', got {b.block_type!r}"
                )


# ---------------------------------------------------------------------------
# TestChromaAdapter — Chroma-specific tests
# ---------------------------------------------------------------------------

class TestChromaAdapter:

    def test_chroma_query_as_blocks_returns_vector_blocks(self):
        """ChromaAdapter.query_as_blocks() returns VectorBlock instances."""
        adapter = _make_chroma_adapter()
        blocks = adapter.query_as_blocks(SAMPLE_VECTOR, limit=3)
        assert len(blocks) == 3
        for b in blocks:
            assert isinstance(b, VectorBlock)
            assert b.content  # non-empty
            assert 0.0 <= b.quality <= 1.0

    def test_chroma_handles_empty_results(self):
        """ChromaAdapter gracefully handles an empty result set."""
        collection = MagicMock()
        collection.name = "empty-col"
        collection.query.return_value = {
            "ids": [[]],
            "documents": [[]],
            "distances": [[]],
            "metadatas": [[]],
        }
        adapter = ChromaAdapter(collection)
        blocks = adapter.query_as_blocks(SAMPLE_VECTOR, limit=5)
        assert blocks == []


# ---------------------------------------------------------------------------
# TestPineconeAdapter — Pinecone-specific tests
# ---------------------------------------------------------------------------

class TestPineconeAdapter:

    def test_pinecone_score_mapping_to_quality(self):
        """Pinecone cosine score (0-1) maps directly to block quality."""
        adapter = _make_pinecone_adapter()
        blocks = adapter.query_as_blocks(SAMPLE_VECTOR, limit=2)
        assert len(blocks) == 2
        # Scores were 0.95 and 0.80; quality should match (cosine already 0-1)
        qualities = [b.quality for b in blocks]
        assert abs(qualities[0] - 0.95) < 1e-6, f"Expected ~0.95, got {qualities[0]}"
        assert abs(qualities[1] - 0.80) < 1e-6, f"Expected ~0.80, got {qualities[1]}"

    def test_pinecone_metadata_to_provenance(self):
        """Pinecone adapter stores namespace and index in provenance."""
        adapter = _make_pinecone_adapter()
        blocks = adapter.query_as_blocks(SAMPLE_VECTOR, limit=1)
        assert len(blocks) >= 1
        prov = blocks[0].provenance
        assert "namespace" in prov, "provenance missing namespace"
        assert prov["namespace"] == "test-ns"
        assert "raw_score" in prov, "provenance missing raw_score"


# ---------------------------------------------------------------------------
# TestQdrantAdapter — Qdrant-specific tests
# ---------------------------------------------------------------------------

class TestQdrantAdapter:

    def test_qdrant_cosine_quality_normalization(self):
        """
        Qdrant cosine scores (-1 to 1) are mapped to 0-1 quality.
        Formula: quality = (score + 1) / 2
        """
        client = MagicMock()
        h = MagicMock()
        h.id = "q-norm"
        h.score = 0.6   # cosine raw → quality = (0.6+1)/2 = 0.8
        h.payload = {"text": "normalized hit"}
        client.search.return_value = [h]

        adapter = QdrantAdapter(client, collection_name="norm-col", score_metric="cosine")
        blocks = adapter.query_as_blocks(SAMPLE_VECTOR, limit=1)
        assert len(blocks) == 1
        expected = (0.6 + 1.0) / 2.0   # = 0.8
        assert abs(blocks[0].quality - expected) < 1e-6, (
            f"Expected quality={expected}, got {blocks[0].quality}"
        )

    def test_qdrant_handles_filter(self):
        """QdrantAdapter passes query_filter through to client.search()."""
        adapter = _make_qdrant_adapter()
        my_filter = MagicMock()  # Simulate qdrant_client.models.Filter
        adapter.query_as_blocks(SAMPLE_VECTOR, limit=3, query_filter=my_filter)
        call_kwargs = adapter._client.search.call_args[1]
        assert "query_filter" in call_kwargs, "query_filter not forwarded to client.search"
        assert call_kwargs["query_filter"] is my_filter


# ---------------------------------------------------------------------------
# TestBatchQuery — batch interface across adapters
# ---------------------------------------------------------------------------

class TestBatchQuery:

    def test_batch_query_returns_batch_result(self):
        """batch_query_as_blocks() returns a BatchQueryResult with one entry per query."""
        adapter = _make_chroma_adapter()
        queries = [SAMPLE_VECTOR, SAMPLE_VECTOR]
        result = adapter.batch_query_as_blocks(queries, limit=2)
        assert isinstance(result, BatchQueryResult), (
            f"Expected BatchQueryResult, got {type(result)}"
        )
        assert len(result) == 2, f"Expected 2 result sets, got {len(result)}"
        assert result.elapsed_ms >= 0.0
        # flat_blocks should aggregate all results
        assert isinstance(result.flat_blocks, list)

    def test_batch_query_pinecone_multiple_queries(self):
        """Pinecone batch query returns per-query result lists."""
        adapter = _make_pinecone_adapter()
        result = adapter.batch_query_as_blocks(
            [SAMPLE_VECTOR, SAMPLE_VECTOR, SAMPLE_VECTOR], limit=1
        )
        assert isinstance(result, BatchQueryResult)
        assert len(result) == 3
        for blocks in result.results:
            assert isinstance(blocks, list)

    def test_batch_query_qdrant_flat_blocks(self):
        """BatchQueryResult.flat_blocks aggregates all results correctly."""
        adapter = _make_qdrant_adapter()
        result = adapter.batch_query_as_blocks([SAMPLE_VECTOR, SAMPLE_VECTOR], limit=3)
        total_blocks = sum(len(b) for b in result.results)
        assert len(result.flat_blocks) == total_blocks
