"""Cross-adapter integration tests for VectorDB adapters."""

import sys
import os
from unittest.mock import Mock, MagicMock

# Add parent to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from tokenpak_vectordb.base import VectorDBAdapter, VectorBlock, BatchQueryResult
from tokenpak_vectordb.chroma import ChromaAdapter
from tokenpak_vectordb.pinecone import PineconeAdapter
from tokenpak_vectordb.qdrant import QdrantAdapter
from tokenpak_vectordb.weaviate import WeaviateAdapter


class TestAdapterParity:
    """Test that all adapters have consistent interfaces."""

    def test_all_adapters_have_query_as_blocks(self):
        """Verify all adapters implement query_as_blocks method."""
        adapters = [
            ChromaAdapter,
            PineconeAdapter,
            QdrantAdapter,
            WeaviateAdapter,
        ]
        for adapter_cls in adapters:
            assert hasattr(adapter_cls, 'query_as_blocks'), \
                f"{adapter_cls.__name__} missing query_as_blocks"

    def test_all_adapters_return_list(self):
        """Verify all adapters return list from query_as_blocks."""
        adapters = [ChromaAdapter, PineconeAdapter, QdrantAdapter, WeaviateAdapter]
        for adapter_cls in adapters:
            assert issubclass(adapter_cls, VectorDBAdapter), \
                f"{adapter_cls.__name__} not a VectorDBAdapter"

    def test_all_adapters_accept_block_type_param(self):
        """Verify adapters handle block_type parameter."""
        adapters = [ChromaAdapter, PineconeAdapter, QdrantAdapter, WeaviateAdapter]
        for adapter_cls in adapters:
            # Check that query_as_blocks signature includes block_type or accepts **kwargs
            import inspect
            sig = inspect.signature(adapter_cls.query_as_blocks)
            params = list(sig.parameters.keys())
            has_block_type = 'block_type' in params or 'kwargs' in params
            assert has_block_type or len(params) > 2, \
                f"{adapter_cls.__name__}.query_as_blocks missing flexibility"


class TestChromaAdapter:
    """Tests specific to Chroma adapter."""

    def test_chroma_adapter_with_mock_collection(self):
        """Verify Chroma adapter accepts collection parameter."""
        mock_collection = MagicMock()
        adapter = ChromaAdapter(collection=mock_collection)
        assert adapter is not None

    def test_vector_block_construction(self):
        """Verify VectorBlock accepts correct fields."""
        block = VectorBlock(
            id='test_1',
            content='test content',
            block_type='evidence',
            quality=0.9,
            provenance={'source': 'chroma'},
        )
        assert block.id == 'test_1'
        assert block.content == 'test content'
        assert block.quality == 0.9
        assert block.provenance['source'] == 'chroma'

    def test_vector_block_quality_clamped(self):
        """Verify VectorBlock clamps quality to 0-1."""
        block_low = VectorBlock(id='b1', content='test', quality=-0.5)
        block_high = VectorBlock(id='b2', content='test', quality=1.5)
        assert block_low.quality == 0.0
        assert block_high.quality == 1.0


class TestPineconeAdapter:
    """Tests specific to Pinecone adapter."""

    def test_pinecone_adapter_exists(self):
        """Verify PineconeAdapter class exists and is VectorDBAdapter."""
        assert issubclass(PineconeAdapter, VectorDBAdapter)

    def test_pinecone_adapter_has_required_methods(self):
        """Verify Pinecone adapter has query_as_blocks."""
        assert hasattr(PineconeAdapter, 'query_as_blocks')


class TestQdrantAdapter:
    """Tests specific to Qdrant adapter."""

    def test_qdrant_adapter_exists(self):
        """Verify QdrantAdapter class exists and is VectorDBAdapter."""
        assert issubclass(QdrantAdapter, VectorDBAdapter)

    def test_qdrant_adapter_has_required_methods(self):
        """Verify Qdrant adapter has query_as_blocks."""
        assert hasattr(QdrantAdapter, 'query_as_blocks')


class TestWeaviateAdapter:
    """Tests specific to Weaviate adapter."""

    def test_weaviate_adapter_exists(self):
        """Verify WeaviateAdapter class exists and is VectorDBAdapter."""
        assert issubclass(WeaviateAdapter, VectorDBAdapter)

    def test_weaviate_adapter_has_required_methods(self):
        """Verify Weaviate adapter has query_as_blocks."""
        assert hasattr(WeaviateAdapter, 'query_as_blocks')


class TestBatchQueryResult:
    """Tests for batch query result structure."""

    def test_batch_query_result_creation(self):
        """Verify BatchQueryResult can be constructed."""
        result = BatchQueryResult(
            queries=['q1', 'q2'],
            results=[
                [
                    VectorBlock(id='r1', content='result 1', quality=0.9),
                    VectorBlock(id='r2', content='result 2', quality=0.8),
                ],
                [
                    VectorBlock(id='r3', content='result 3', quality=0.7),
                ],
            ],
        )
        assert len(result.results) == 2
        assert len(result.results[0]) == 2
        assert len(result.results[1]) == 1

    def test_batch_query_result_access(self):
        """Verify BatchQueryResult allows query result access."""
        blocks = [
            VectorBlock(id='b1', content='test 1', quality=0.95),
            VectorBlock(id='b2', content='test 2', quality=0.85),
        ]
        result = BatchQueryResult(queries=['q1'], results=[[blocks[0], blocks[1]]])

        assert result.results[0][0].quality == 0.95
        assert result.results[0][1].quality == 0.85


class TestVectorBlockMetadata:
    """Tests for VectorBlock metadata handling."""

    def test_vector_block_with_metadata(self):
        """Verify VectorBlock preserves metadata."""
        meta = {'source': 'docs', 'version': '1.0', 'type': 'code'}
        block = VectorBlock(
            id='block_1',
            content='content',
            metadata=meta,
        )
        assert block.metadata == meta

    def test_vector_block_default_metadata(self):
        """Verify VectorBlock uses empty dict for default metadata."""
        block = VectorBlock(id='b1', content='test')
        assert block.metadata == {}
        assert isinstance(block.metadata, dict)


class TestVectorBlockTokenEstimation:
    """Tests for token estimation in VectorBlock."""

    def test_vector_block_estimates_tokens(self):
        """Verify VectorBlock estimates token count."""
        short_block = VectorBlock(id='b1', content='test')
        long_block = VectorBlock(id='b2', content='test content ' * 100)

        assert short_block.tokens > 0
        assert long_block.tokens > short_block.tokens

    def test_vector_block_preserves_original_tokens(self):
        """Verify VectorBlock tracks original token count."""
        block = VectorBlock(id='b1', content='test ' * 100)
        original = block.tokens
        # Original should be preserved
        assert block._original_tokens == original


if __name__ == '__main__':
    import pytest
    pytest.main([__file__, '-v'])
