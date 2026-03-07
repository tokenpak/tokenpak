"""Tests for WeaviateAdapter using mock Weaviate client."""

import pytest
from unittest.mock import MagicMock, patch
from tokenpak_vectordb import WeaviateAdapter, VectorBlock


def _make_v4_client(results: list):
    """Mock Weaviate v4 client."""
    client = MagicMock()
    collection = MagicMock()

    objects = []
    for r in results:
        obj = MagicMock()
        obj.properties = {k: v for k, v in r.items() if not k.startswith("_")}
        obj.uuid = r.get("_id", "uuid-1")
        meta = MagicMock()
        meta.certainty = r.get("_certainty")
        meta.distance = r.get("_distance")
        obj.metadata = meta
        objects.append(obj)

    response = MagicMock()
    response.objects = objects
    collection.query.near_text.return_value = response
    collection.query.near_vector.return_value = response
    client.collections.get.return_value = collection
    return client


def _make_v3_client(results: list, class_name: str = "Document"):
    """Mock Weaviate v3 client."""
    client = MagicMock()
    # v3 client doesn't have .collections attr
    del client.collections

    formatted = []
    for r in results:
        item = {k: v for k, v in r.items() if not k.startswith("_")}
        item["_additional"] = {
            "id": r.get("_id", "id-1"),
            "certainty": r.get("_certainty"),
            "distance": r.get("_distance"),
        }
        formatted.append(item)

    mock_response = {"data": {"Get": {class_name: formatted}}}
    mock_q = MagicMock()
    mock_q.with_limit.return_value = mock_q
    mock_q.with_additional.return_value = mock_q
    mock_q.with_near_text.return_value = mock_q
    mock_q.with_near_vector.return_value = mock_q
    mock_q.do.return_value = mock_response
    client.query.get.return_value = mock_q
    return client


class TestWeaviateAdapterV4:
    def _adapter(self, results=None):
        if results is None:
            results = [
                {"_id": "uuid-1", "text": "TokenPak protocol", "_certainty": 0.92},
                {"_id": "uuid-2", "text": "RAG pipeline", "_certainty": 0.78},
            ]
        client = _make_v4_client(results)
        return WeaviateAdapter(client, collection_name="Document"), client

    def test_query_returns_blocks(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks("What is TokenPak?")
        assert len(blocks) == 2
        for b in blocks:
            assert isinstance(b, VectorBlock)

    def test_quality_from_certainty(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks("test")
        # sorted by quality desc
        assert blocks[0].quality >= blocks[1].quality
        assert abs(blocks[0].quality - 0.92) < 0.01

    def test_content_extracted(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks("test")
        assert "TokenPak" in blocks[0].content or "RAG" in blocks[0].content

    def test_provenance(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks("test")
        for b in blocks:
            assert b.provenance["source_type"] == "weaviate"
            assert b.provenance["collection"] == "Document"

    def test_block_type_default(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks("test")
        for b in blocks:
            assert b.block_type == "evidence"

    def test_block_type_override(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks("test", block_type="knowledge")
        for b in blocks:
            assert b.block_type == "knowledge"

    def test_distance_based_quality(self):
        results = [
            {"_id": "x", "text": "hi", "_certainty": None, "_distance": 0.2},
        ]
        client = _make_v4_client(results)
        adapter = WeaviateAdapter(client, collection_name="Doc")
        blocks = adapter.query_as_blocks("test")
        # distance 0.2 → quality = 1 - 0.2/2 = 0.9
        assert abs(blocks[0].quality - 0.9) < 0.01

    def test_empty_results(self):
        client = _make_v4_client([])
        adapter = WeaviateAdapter(client, collection_name="Doc")
        blocks = adapter.query_as_blocks("test")
        assert blocks == []


class TestWeaviateAdapterV3:
    def _adapter(self, results=None):
        if results is None:
            results = [
                {"_id": "id-1", "text": "TokenPak protocol", "_certainty": 0.88},
                {"_id": "id-2", "text": "Vector databases", "_certainty": 0.75},
            ]
        client = _make_v3_client(results)
        return WeaviateAdapter(client, collection_name="Document"), client

    def test_v3_query_returns_blocks(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks("TokenPak")
        assert len(blocks) == 2

    def test_v3_quality_from_certainty(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks("test")
        assert blocks[0].quality >= blocks[1].quality

    def test_v3_content(self):
        adapter, _ = self._adapter()
        blocks = adapter.query_as_blocks("test")
        contents = [b.content for b in blocks]
        assert any("TokenPak" in c for c in contents)

    def test_v3_fallback_quality(self):
        """When certainty and distance are both None, quality should be 0.5."""
        results = [{"_id": "x", "text": "hi", "_certainty": None, "_distance": None}]
        client = _make_v3_client(results)
        adapter = WeaviateAdapter(client, collection_name="Document")
        blocks = adapter.query_as_blocks("test")
        assert blocks[0].quality == 0.5
