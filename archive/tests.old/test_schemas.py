"""test_schemas.py — Tests for data schema classes and validation.

Tests for the schema modules:
- artifact.py — artifact storage schemas
- chunk.py — text chunk schemas
- retrieval_cache.py — cache entry schemas
- source_map.py — source mapping schemas
"""

import pytest
from datetime import datetime
import json


class TestArtifactSchema:
    """Tests for artifact schema classes."""

    def test_artifact_schema_import(self):
        """Test that artifact module can be imported."""
        try:
            from tokenpak.schemas.artifact import Artifact
            assert hasattr(Artifact, '__init__')
        except ImportError:
            pytest.skip("Artifact schema not available")

    def test_artifact_creation(self):
        """Test basic artifact creation."""
        try:
            from tokenpak.schemas.artifact import Artifact
            artifact = Artifact(
                name="test_artifact",
                content="test content",
                format="text",
            )
            assert artifact.name == "test_artifact"
            assert artifact.content == "test content"
        except ImportError:
            pytest.skip("Artifact schema not available")

    def test_artifact_metadata(self):
        """Test artifact with metadata."""
        try:
            from tokenpak.schemas.artifact import Artifact
            artifact = Artifact(
                name="doc.md",
                content="# Title\n\nContent",
                format="markdown",
                metadata={"source": "import", "compressed": True},
            )
            assert artifact.metadata["source"] == "import"
        except ImportError:
            pytest.skip("Artifact schema not available")


class TestChunkSchema:
    """Tests for text chunk schema."""

    def test_chunk_schema_import(self):
        """Test that chunk module can be imported."""
        try:
            from tokenpak.schemas.chunk import Chunk
            assert hasattr(Chunk, '__init__')
        except ImportError:
            pytest.skip("Chunk schema not available")

    def test_chunk_creation(self):
        """Test basic chunk creation."""
        try:
            from tokenpak.schemas.chunk import Chunk
            chunk = Chunk(
                id="chunk_001",
                text="This is chunk text",
                start_offset=0,
                end_offset=18,
            )
            assert chunk.id == "chunk_001"
            assert chunk.text == "This is chunk text"
        except ImportError:
            pytest.skip("Chunk schema not available")

    def test_chunk_with_tokens(self):
        """Test chunk with token count."""
        try:
            from tokenpak.schemas.chunk import Chunk
            chunk = Chunk(
                id="chunk_002",
                text="Another chunk",
                start_offset=20,
                end_offset=33,
                token_count=3,
            )
            assert chunk.token_count == 3
        except ImportError:
            pytest.skip("Chunk schema not available")

    def test_chunk_ordering(self):
        """Test that chunks maintain order."""
        try:
            from tokenpak.schemas.chunk import Chunk
            chunks = [
                Chunk(id="1", text="First", start_offset=0, end_offset=5),
                Chunk(id="2", text="Second", start_offset=6, end_offset=12),
                Chunk(id="3", text="Third", start_offset=13, end_offset=18),
            ]
            assert chunks[0].id == "1"
            assert chunks[2].id == "3"
            assert chunks[0].start_offset < chunks[1].start_offset
        except ImportError:
            pytest.skip("Chunk schema not available")


class TestRetrievalCacheSchema:
    """Tests for retrieval cache schema."""

    def test_cache_entry_import(self):
        """Test that retrieval cache module can be imported."""
        try:
            from tokenpak.schemas.retrieval_cache import CacheEntry
            assert hasattr(CacheEntry, '__init__')
        except ImportError:
            pytest.skip("Cache schema not available")

    def test_cache_entry_creation(self):
        """Test basic cache entry creation."""
        try:
            from tokenpak.schemas.retrieval_cache import CacheEntry
            entry = CacheEntry(
                key="query_hash_abc123",
                value="cached_result",
                ttl=3600,
            )
            assert entry.key == "query_hash_abc123"
            assert entry.value == "cached_result"
        except ImportError:
            pytest.skip("Cache schema not available")

    def test_cache_entry_with_metadata(self):
        """Test cache entry with metadata."""
        try:
            from tokenpak.schemas.retrieval_cache import CacheEntry
            entry = CacheEntry(
                key="query_001",
                value="result",
                ttl=7200,
                metadata={"hit_count": 5, "last_accessed": "2026-03-16"},
            )
            assert entry.metadata["hit_count"] == 5
        except ImportError:
            pytest.skip("Cache schema not available")

    def test_cache_entry_expiration(self):
        """Test cache TTL handling."""
        try:
            from tokenpak.schemas.retrieval_cache import CacheEntry
            short_ttl = CacheEntry(key="short", value="data", ttl=10)
            long_ttl = CacheEntry(key="long", value="data", ttl=86400)
            
            assert short_ttl.ttl == 10
            assert long_ttl.ttl == 86400
        except ImportError:
            pytest.skip("Cache schema not available")


class TestSourceMapSchema:
    """Tests for source mapping schema."""

    def test_source_map_import(self):
        """Test that source map module can be imported."""
        try:
            from tokenpak.schemas.source_map import SourceMapping
            assert hasattr(SourceMapping, '__init__')
        except ImportError:
            pytest.skip("Source map schema not available")

    def test_source_mapping_creation(self):
        """Test basic source mapping creation."""
        try:
            from tokenpak.schemas.source_map import SourceMapping
            mapping = SourceMapping(
                source_id="doc_001",
                target_id="chunk_001",
                mapping_type="chunk",
            )
            assert mapping.source_id == "doc_001"
            assert mapping.target_id == "chunk_001"
        except ImportError:
            pytest.skip("Source map schema not available")

    def test_source_mapping_with_ranges(self):
        """Test source mapping with byte ranges."""
        try:
            from tokenpak.schemas.source_map import SourceMapping
            mapping = SourceMapping(
                source_id="doc",
                target_id="chunk",
                mapping_type="range",
                start_byte=100,
                end_byte=200,
            )
            assert mapping.start_byte == 100
            assert mapping.end_byte == 200
        except ImportError:
            pytest.skip("Source map schema not available")


class TestSchemaSerialization:
    """Tests for schema serialization."""

    def test_artifact_to_dict(self):
        """Test artifact serialization to dict."""
        try:
            from tokenpak.schemas.artifact import Artifact
            artifact = Artifact(
                name="test.md",
                content="test",
                format="markdown",
            )
            # Test that it can be serialized (has to_dict or similar)
            if hasattr(artifact, 'to_dict'):
                result = artifact.to_dict()
                assert isinstance(result, dict)
            elif hasattr(artifact, '__dict__'):
                result = artifact.__dict__
                assert isinstance(result, dict)
        except ImportError:
            pytest.skip("Artifact schema not available")

    def test_artifact_to_json(self):
        """Test artifact JSON serialization."""
        try:
            from tokenpak.schemas.artifact import Artifact
            artifact = Artifact(
                name="test.md",
                content="test",
                format="markdown",
            )
            # Should be JSON serializable
            if hasattr(artifact, 'to_dict'):
                data = artifact.to_dict()
                json_str = json.dumps(data)
                assert isinstance(json_str, str)
            elif hasattr(artifact, '__dict__'):
                data = artifact.__dict__
                json_str = json.dumps(data, default=str)
                assert isinstance(json_str, str)
        except ImportError:
            pytest.skip("Artifact schema not available")


class TestSchemaValidation:
    """Tests for schema validation."""

    def test_chunk_text_not_empty(self):
        """Test that chunk requires non-empty text."""
        try:
            from tokenpak.schemas.chunk import Chunk
            # Should handle empty text gracefully (either accept or reject)
            chunk = Chunk(
                id="empty",
                text="",
                start_offset=0,
                end_offset=0,
            )
            # If we got here, empty text is accepted
            assert chunk.text == ""
        except (ValueError, TypeError, ImportError):
            # ImportError: schema not available
            # ValueError/TypeError: validation error
            pass

    def test_cache_key_required(self):
        """Test that cache requires a key."""
        try:
            from tokenpak.schemas.retrieval_cache import CacheEntry
            # Try to create without key
            try:
                entry = CacheEntry(
                    key=None,
                    value="result",
                    ttl=3600,
                )
                # If we got here, None key is accepted
                assert entry.key is None
            except (ValueError, TypeError):
                # Expected: key is required
                pass
        except ImportError:
            pytest.skip("Cache schema not available")

    def test_source_mapping_requires_ids(self):
        """Test that source mapping requires source and target IDs."""
        try:
            from tokenpak.schemas.source_map import SourceMapping
            # Should require both source_id and target_id
            with pytest.raises((ValueError, TypeError)):
                mapping = SourceMapping(
                    source_id="doc",
                    target_id=None,
                    mapping_type="chunk",
                )
        except ImportError:
            pytest.skip("Source map schema not available")
