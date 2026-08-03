"""Tests for PineconeAdapter using mock Pinecone index."""

import pytest
from unittest.mock import MagicMock, patch
from tokenpak_vectordb import PineconeAdapter, VectorBlock


def _make_match(id: str, score: float, metadata: dict):
    """Create a mock Pinecone ScoredVector (object-style)."""
    m = MagicMock()
    m.id = id
    m.score = score
    m.metadata = metadata
    return m


def _make_index(matches):
    """Create a mock Pinecone Index."""
    index = MagicMock()
    response = MagicMock()
    response.matches = matches
    index.query.return_value = response
    index.name = "test-index"
    return index


class TestPineconeAdapter:
    def _adapter(self, matches=None):
        if matches is None:
            matches = [
                _make_match("doc1", 0.95, {"text": "TokenPak is a protocol."}),
                _make_match("doc2", 0.82, {"text": "RAG pipelines use vector DBs."}),
                _make_match("doc3", 0.71, {"text": "Context compression matters."}),
            ]
        index = _make_index(matches)
        return PineconeAdapter(index), index

    def test_query_as_blocks_returns_list(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks([0.1, 0.2, 0.3], limit=3)
        assert isinstance(blocks, list)
        assert len(blocks) == 3

    def test_block_types(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks([0.1], limit=3)
        for block in blocks:
            assert isinstance(block, VectorBlock)

    def test_quality_mapping(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks([0.1], limit=3)
        assert abs(blocks[0].quality - 0.95) < 0.001
        assert abs(blocks[1].quality - 0.82) < 0.001

    def test_content_extracted(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks([0.1], limit=3)
        assert blocks[0].content == "TokenPak is a protocol."

    def test_provenance_set(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks([0.1], limit=3)
        prov = blocks[0].provenance
        assert prov["source_type"] == "pinecone"
        assert prov["source_id"] == "doc1"
        assert "retrieved_at" in prov
        assert prov["raw_score"] == 0.95

    def test_block_type_default(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks([0.1], limit=3)
        for block in blocks:
            assert block.block_type == "evidence"

    def test_block_type_override(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks([0.1], limit=3, block_type="knowledge")
        for block in blocks:
            assert block.block_type == "knowledge"

    def test_query_passes_top_k(self):
        adapter, index = self._adapter()
        adapter.query_as_blocks([0.1], limit=5)
        call_kwargs = index.query.call_args[1]
        assert call_kwargs["top_k"] == 5

    def test_namespace_passed(self):
        adapter, index = self._adapter()
        adapter.query_as_blocks([0.1], namespace="prod")
        call_kwargs = index.query.call_args[1]
        assert call_kwargs["namespace"] == "prod"

    def test_no_namespace_by_default(self):
        adapter, index = self._adapter()
        adapter.query_as_blocks([0.1])
        call_kwargs = index.query.call_args[1]
        assert "namespace" not in call_kwargs

    def test_filter_passed(self):
        adapter, index = self._adapter()
        f = {"category": {"$eq": "docs"}}
        adapter.query_as_blocks([0.1], filter=f)
        call_kwargs = index.query.call_args[1]
        assert call_kwargs["filter"] == f

    def test_empty_results(self):
        index = _make_index([])
        adapter = PineconeAdapter(index)
        blocks = adapter.query_as_blocks([0.1])
        assert blocks == []

    def test_quality_clamped_at_1(self):
        matches = [_make_match("x", 1.5, {"text": "hi"})]
        adapter, _ = self._adapter(matches)
        blocks = adapter.query_as_blocks([0.1], limit=1)
        assert blocks[0].quality == 1.0

    def test_quality_clamped_at_0(self):
        matches = [_make_match("x", -0.5, {"text": "hi"})]
        adapter, _ = self._adapter(matches)
        blocks = adapter.query_as_blocks([0.1], limit=1)
        assert blocks[0].quality == 0.0

    def test_dict_style_result(self):
        """Test parsing dict-style results (not object attributes)."""
        index = MagicMock()
        response = MagicMock()
        # Dict-style match (no .id attribute)
        match = {"id": "doc1", "score": 0.88, "metadata": {"text": "Dict result"}}
        response.matches = [match]
        index.query.return_value = response
        adapter = PineconeAdapter(index)
        blocks = adapter.query_as_blocks([0.1])
        assert len(blocks) == 1
        assert blocks[0].id == "doc1"
        assert blocks[0].content == "Dict result"

    def test_content_field_custom(self):
        matches = [_make_match("x", 0.9, {"chunk": "Chunk text", "other": "val"})]
        index = _make_index(matches)
        adapter = PineconeAdapter(index, content_field="chunk")
        blocks = adapter.query_as_blocks([0.1])
        assert blocks[0].content == "Chunk text"

    def test_metadata_stripped_of_content(self):
        """Text field should not appear in metadata."""
        matches = [_make_match("x", 0.9, {"text": "Hello", "author": "Alice"})]
        adapter, _ = self._adapter(matches)
        blocks = adapter.query_as_blocks([0.1])
        assert "text" not in blocks[0].metadata
        assert blocks[0].metadata.get("author") == "Alice"

    def test_tokens_estimated(self):
        matches = [_make_match("x", 0.9, {"text": "a" * 100})]
        adapter, _ = self._adapter(matches)
        blocks = adapter.query_as_blocks([0.1])
        assert blocks[0].tokens == 25  # 100 chars / 4
