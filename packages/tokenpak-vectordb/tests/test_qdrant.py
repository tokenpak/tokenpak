"""Tests for QdrantAdapter using mock Qdrant client."""

import pytest
from unittest.mock import MagicMock
from tokenpak_vectordb import QdrantAdapter, VectorBlock


def _make_scored_point(id: str, score: float, payload: dict):
    """Create a mock Qdrant ScoredPoint."""
    sp = MagicMock()
    sp.id = id
    sp.score = score
    sp.payload = payload
    return sp


def _make_client(hits: list):
    """Create a mock Qdrant client."""
    client = MagicMock()
    client.search.return_value = hits
    return client


class TestQdrantAdapter:
    def _adapter(self, hits=None, score_metric="cosine"):
        if hits is None:
            hits = [
                _make_scored_point("1", 0.95, {"text": "TokenPak protocol"}),
                _make_scored_point("2", 0.80, {"text": "Context compression"}),
                _make_scored_point("3", 0.65, {"text": "Vector databases"}),
            ]
        client = _make_client(hits)
        adapter = QdrantAdapter(client, collection_name="docs", score_metric=score_metric)
        return adapter, client

    def test_query_returns_blocks(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks([0.1, 0.2], limit=3)
        assert len(blocks) == 3
        for b in blocks:
            assert isinstance(b, VectorBlock)

    def test_cosine_quality_mapping(self):
        adapter, _ = self._adapter(score_metric="cosine")
        blocks = adapter.query_as_blocks([0.1], limit=3)
        # cosine: score 0.95 → quality = (0.95+1)/2 = 0.975
        assert abs(blocks[0].quality - 0.975) < 0.001
        assert abs(blocks[1].quality - 0.90) < 0.001

    def test_euclid_quality_mapping(self):
        hits = [_make_scored_point("x", 0.5, {"text": "hi"})]
        client = _make_client(hits)
        adapter = QdrantAdapter(client, "docs", score_metric="euclid")
        blocks = adapter.query_as_blocks([0.1])
        # euclid: quality = 1/(1+0.5) ≈ 0.667
        assert abs(blocks[0].quality - 0.667) < 0.01

    def test_dot_quality_mapping(self):
        hits = [_make_scored_point("x", 0.0, {"text": "hi"})]
        client = _make_client(hits)
        adapter = QdrantAdapter(client, "docs", score_metric="dot")
        blocks = adapter.query_as_blocks([0.1])
        # dot: score=0 → sigmoid(0) = 0.5
        assert abs(blocks[0].quality - 0.5) < 0.01

    def test_content_extracted(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks([0.1], limit=3)
        assert blocks[0].content == "TokenPak protocol"

    def test_provenance(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks([0.1])
        prov = blocks[0].provenance
        assert prov["source_type"] == "qdrant"
        assert prov["collection"] == "docs"
        assert "raw_score" in prov

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

    def test_limit_passed_to_client(self):
        adapter, client = self._adapter()
        adapter.query_as_blocks([0.1], limit=7)
        call_kwargs = client.search.call_args[1]
        assert call_kwargs["limit"] == 7

    def test_score_threshold_passed(self):
        adapter, client = self._adapter()
        adapter.query_as_blocks([0.1], score_threshold=0.5)
        call_kwargs = client.search.call_args[1]
        assert call_kwargs["score_threshold"] == 0.5

    def test_filter_passed(self):
        from unittest.mock import sentinel
        adapter, client = self._adapter()
        f = sentinel.filter
        adapter.query_as_blocks([0.1], query_filter=f)
        call_kwargs = client.search.call_args[1]
        assert call_kwargs["query_filter"] is f

    def test_empty_results(self):
        client = _make_client([])
        adapter = QdrantAdapter(client, "docs")
        blocks = adapter.query_as_blocks([0.1])
        assert blocks == []

    def test_dict_style_result(self):
        """Dict-style result (not object attributes)."""
        result = {"id": "doc1", "score": 0.9, "payload": {"text": "Dict result"}}
        client = _make_client([result])
        adapter = QdrantAdapter(client, "docs")
        blocks = adapter.query_as_blocks([0.1])
        assert blocks[0].id == "doc1"
        assert blocks[0].content == "Dict result"

    def test_metadata_stripped_of_content(self):
        hits = [_make_scored_point("x", 0.9, {"text": "Content", "author": "Alice"})]
        adapter, _ = self._adapter(hits)
        blocks = adapter.query_as_blocks([0.1])
        assert "text" not in blocks[0].metadata
        assert blocks[0].metadata.get("author") == "Alice"

    def test_custom_content_field(self):
        hits = [_make_scored_point("x", 0.9, {"chunk": "Chunk text", "other": "x"})]
        client = _make_client(hits)
        adapter = QdrantAdapter(client, "docs", content_field="chunk")
        blocks = adapter.query_as_blocks([0.1])
        assert blocks[0].content == "Chunk text"
