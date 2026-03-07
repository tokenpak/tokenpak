"""Tests for ChromaAdapter using mock Chroma collection."""

import pytest
from unittest.mock import MagicMock
from tokenpak_vectordb import ChromaAdapter, VectorBlock


def _make_collection(ids, documents, distances, metadatas=None, name="test"):
    """Create a mock Chroma collection."""
    collection = MagicMock()
    collection.name = name
    collection.query.return_value = {
        "ids": [ids],
        "documents": [documents],
        "distances": [distances],
        "metadatas": [metadatas or [{} for _ in ids]],
    }
    return collection


class TestChromaAdapter:
    def _adapter(self, distance_metric="l2"):
        collection = _make_collection(
            ids=["doc1", "doc2", "doc3"],
            documents=["TokenPak protocol", "RAG pipelines", "Vector DBs"],
            distances=[0.1, 0.5, 1.2],
        )
        adapter = ChromaAdapter(collection, distance_metric=distance_metric)
        return adapter, collection

    def test_query_returns_blocks(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks([0.1, 0.2], limit=3)
        assert len(blocks) == 3
        for b in blocks:
            assert isinstance(b, VectorBlock)

    def test_content_from_documents(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks([0.1], limit=3)
        assert blocks[0].content == "TokenPak protocol"

    def test_l2_quality_mapping(self):
        adapter, _ = self._adapter(distance_metric="l2")
        blocks = adapter.query_as_blocks([0.1], limit=3)
        # L2 distance 0.1 → quality = 1/(1+0.1) ≈ 0.909
        assert abs(blocks[0].quality - 0.909) < 0.01
        # L2 distance 0.5 → quality = 1/(1+0.5) ≈ 0.667
        assert abs(blocks[1].quality - 0.667) < 0.01

    def test_cosine_quality_mapping(self):
        collection = _make_collection(
            ids=["a", "b"],
            documents=["doc a", "doc b"],
            distances=[0.2, 0.8],
        )
        adapter = ChromaAdapter(collection, distance_metric="cosine")
        blocks = adapter.query_as_blocks([0.1])
        # cosine: quality = 1 - distance/2
        assert abs(blocks[0].quality - 0.9) < 0.01  # 1 - 0.2/2
        assert abs(blocks[1].quality - 0.6) < 0.01  # 1 - 0.8/2

    def test_ip_quality_mapping(self):
        collection = _make_collection(
            ids=["a"],
            documents=["doc a"],
            distances=[0.0],  # ip score=0 → sigmoid(0)=0.5 quality
        )
        adapter = ChromaAdapter(collection, distance_metric="ip")
        blocks = adapter.query_as_blocks([0.1])
        assert abs(blocks[0].quality - 0.5) < 0.01

    def test_block_type_default(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks([0.1])
        for b in blocks:
            assert b.block_type == "evidence"

    def test_block_type_override(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks([0.1], block_type="knowledge")
        for b in blocks:
            assert b.block_type == "knowledge"

    def test_provenance(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks([0.1])
        prov = blocks[0].provenance
        assert prov["source_type"] == "chroma"
        assert prov["collection"] == "test"
        assert "raw_distance" in prov

    def test_text_query(self):
        """Text queries should use query_texts param."""
        adapter, collection = self._adapter()
        adapter.query_as_blocks("What is TokenPak?", limit=3)
        call_kwargs = collection.query.call_args[1]
        assert "query_texts" in call_kwargs
        assert call_kwargs["query_texts"] == ["What is TokenPak?"]

    def test_vector_query(self):
        """Vector queries should use query_embeddings param."""
        adapter, collection = self._adapter()
        adapter.query_as_blocks([0.1, 0.2, 0.3], limit=3)
        call_kwargs = collection.query.call_args[1]
        assert "query_embeddings" in call_kwargs

    def test_limit_passed(self):
        adapter, collection = self._adapter()
        adapter.query_as_blocks([0.1], limit=5)
        call_kwargs = collection.query.call_args[1]
        assert call_kwargs["n_results"] == 5

    def test_where_filter_passed(self):
        adapter, collection = self._adapter()
        where = {"category": "docs"}
        adapter.query_as_blocks([0.1], where=where)
        call_kwargs = collection.query.call_args[1]
        assert call_kwargs["where"] == where

    def test_metadata_in_block(self):
        collection = _make_collection(
            ids=["x"],
            documents=["Content"],
            distances=[0.1],
            metadatas=[{"author": "Alice", "year": 2024}],
        )
        adapter = ChromaAdapter(collection)
        blocks = adapter.query_as_blocks([0.1])
        assert blocks[0].metadata.get("author") == "Alice"

    def test_empty_results(self):
        collection = _make_collection([], [], [])
        adapter = ChromaAdapter(collection)
        blocks = adapter.query_as_blocks([0.1])
        assert blocks == []

    def test_collection_name_in_provenance(self):
        collection = _make_collection(["x"], ["hi"], [0.1], name="my-collection")
        adapter = ChromaAdapter(collection)
        blocks = adapter.query_as_blocks([0.1])
        assert blocks[0].provenance["collection"] == "my-collection"
