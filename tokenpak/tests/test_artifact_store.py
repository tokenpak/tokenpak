# SPDX-License-Identifier: MIT
"""Unit tests for artifact_store.py module."""

import json
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from tokenpak.artifact_store import ArtifactStore
from tokenpak.schemas.artifact import ArtifactSchema
from tokenpak.schemas.chunk import ChunkSchema
from tokenpak.schemas.retrieval_cache import RetrievalCacheSchema
from tokenpak.schemas.source_map import SourceMapSchema


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        yield db_path


@pytest.fixture
def store(temp_db):
    """Create an ArtifactStore instance with temporary database."""
    return ArtifactStore(db_path=temp_db)


class TestArtifactStore:
    """Test suite for ArtifactStore class."""

    def test_init_default_db_path(self, temp_db):
        """Test ArtifactStore initialization with custom db_path."""
        store = ArtifactStore(db_path=temp_db)
        assert store.db_path == temp_db
        assert Path(temp_db).parent.exists()

    def test_init_creates_parent_directory(self):
        """Test that ArtifactStore creates parent directories."""
        with tempfile.TemporaryDirectory() as tmpdir:
            nested_path = str(Path(tmpdir) / "a" / "b" / "c" / "test.db")
            store = ArtifactStore(db_path=nested_path)
            assert Path(nested_path).parent.exists()

    def test_init_db_tables_created(self, store):
        """Test that _init_db creates all required tables."""
        conn = sqlite3.connect(store.db_path)
        cursor = conn.cursor()

        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        tables = {row[0] for row in cursor.fetchall()}
        conn.close()

        expected_tables = {"artifacts", "chunks", "retrieval_cache", "source_maps"}
        assert expected_tables.issubset(tables)

    def test_store_artifact_success(self, store):
        """Test storing an artifact successfully."""
        artifact = ArtifactSchema(
            id="test-artifact-1",
            session_id="session-123",
            origin="code_dump",
            kind="code",
            content_ref="/path/to/file.py",
            repo_binding="repo/file.py",
            size_bytes=1024,
            token_estimate=256,
            labels={"language": "python", "framework": "django"},
            stats={"retrieval_count": 5},
        )

        artifact_id = store.store_artifact(artifact)

        assert artifact_id == "test-artifact-1"

        # Verify it was stored
        retrieved = store.retrieve_artifact("test-artifact-1")
        assert retrieved is not None
        assert retrieved.id == "test-artifact-1"
        assert retrieved.session_id == "session-123"
        assert retrieved.origin == "code_dump"

    def test_retrieve_artifact_not_found(self, store):
        """Test retrieving non-existent artifact returns None."""
        result = store.retrieve_artifact("nonexistent-id")
        assert result is None

    def test_artifact_roundtrip(self, store):
        """Test artifact data persists through store/retrieve cycle."""
        now = datetime.now(timezone.utc)
        artifact = ArtifactSchema(
            id="roundtrip-test",
            session_id="session-456",
            origin="tool_output",
            kind="json",
            content_ref="https://example.com/data.json",
            size_bytes=2048,
            token_estimate=512,
            labels={"type": "api_response", "format": "json"},
            created_at=now,
            accessed_at=now,
            stats={"cache_hits": 10, "last_retrieval": "2026-03-27"},
        )

        store.store_artifact(artifact)
        retrieved = store.retrieve_artifact("roundtrip-test")

        assert retrieved.id == artifact.id
        assert retrieved.session_id == artifact.session_id
        assert retrieved.origin == artifact.origin
        assert retrieved.kind == artifact.kind
        assert retrieved.size_bytes == artifact.size_bytes
        assert retrieved.token_estimate == artifact.token_estimate
        assert retrieved.labels == artifact.labels
        assert retrieved.stats == artifact.stats

    def test_store_chunk_success(self, store):
        """Test storing a chunk successfully."""
        chunk = ChunkSchema(
            id="chunk-1",
            source="file.py",
            content="def hello():\n    return 'world'",
            token_estimate=20,
            symbols=["hello"],
            embedding_ref="embed-ref-1",
            neighbors=["chunk-2", "chunk-3"],
            metadata={"language": "python", "line_range": [1, 4]},
            stats={"retrieval_freq": 8},
        )

        chunk_id = store.store_chunk(chunk)

        assert chunk_id == "chunk-1"

        retrieved = store.retrieve_chunk("chunk-1")
        assert retrieved is not None
        assert retrieved.content == "def hello():\n    return 'world'"
        assert retrieved.symbols == ["hello"]

    def test_retrieve_chunk_not_found(self, store):
        """Test retrieving non-existent chunk returns None."""
        result = store.retrieve_chunk("nonexistent-chunk")
        assert result is None

    def test_get_chunk_neighbors_success(self, store):
        """Test retrieving neighboring chunks."""
        # Store parent chunk
        parent = ChunkSchema(
            id="parent",
            source="file.py",
            content="parent content",
            token_estimate=10,
            neighbors=["neighbor-1", "neighbor-2"],
        )
        store.store_chunk(parent)

        # Store neighbor chunks
        neighbor1 = ChunkSchema(
            id="neighbor-1",
            source="file.py",
            content="neighbor 1 content",
            token_estimate=10,
            neighbors=[],
        )
        store.store_chunk(neighbor1)

        neighbor2 = ChunkSchema(
            id="neighbor-2",
            source="file.py",
            content="neighbor 2 content",
            token_estimate=10,
            neighbors=[],
        )
        store.store_chunk(neighbor2)

        # Retrieve neighbors
        neighbors = store.get_chunk_neighbors("parent")

        assert len(neighbors) == 2
        assert neighbors[0].id == "neighbor-1"
        assert neighbors[1].id == "neighbor-2"

    def test_get_chunk_neighbors_empty(self, store):
        """Test retrieving neighbors when chunk has none."""
        chunk = ChunkSchema(
            id="lonely-chunk",
            source="file.py",
            content="content",
            token_estimate=10,
            neighbors=[],
        )
        store.store_chunk(chunk)

        neighbors = store.get_chunk_neighbors("lonely-chunk")

        assert neighbors == []

    def test_get_chunk_neighbors_nonexistent_parent(self, store):
        """Test retrieving neighbors for non-existent chunk."""
        neighbors = store.get_chunk_neighbors("nonexistent")
        assert neighbors == []

    def test_cache_retrieval_results_success(self, store):
        """Test caching retrieval results."""
        cache_entry = RetrievalCacheSchema(
            query_fingerprint="fp-123",
            session_id="session-789",
            repo_id="repo-456",
            intent="search",
            results=[{"id": "chunk-1", "score": 0.95}],
            coverage_score=0.85,
            ttl_minutes=30,
            use_count=3,
        )

        store.cache_retrieval_results(cache_entry)

        retrieved = store.get_cached_results("fp-123")
        assert retrieved is not None
        assert retrieved.query_fingerprint == "fp-123"
        assert retrieved.intent == "search"
        assert retrieved.coverage_score == 0.85

    def test_get_cached_results_not_found(self, store):
        """Test retrieving non-existent cache entry."""
        result = store.get_cached_results("nonexistent-fp")
        assert result is None

    def test_invalidate_cache_entry(self, store):
        """Test invalidating a single cache entry."""
        cache_entry = RetrievalCacheSchema(
            query_fingerprint="fp-delete",
            session_id="session-delete",
            repo_id="repo-delete",
            intent="debug",
        )

        store.cache_retrieval_results(cache_entry)
        assert store.get_cached_results("fp-delete") is not None

        store.invalidate_cache_entry("fp-delete")
        assert store.get_cached_results("fp-delete") is None

    def test_invalidate_cache_by_repo(self, store):
        """Test invalidating all cache entries for a repo."""
        # Store multiple cache entries for same repo
        for i in range(3):
            cache_entry = RetrievalCacheSchema(
                query_fingerprint=f"fp-repo-{i}",
                session_id=f"session-{i}",
                repo_id="repo-to-invalidate",
                intent="search",
            )
            store.cache_retrieval_results(cache_entry)

        # Store one for different repo
        other_cache = RetrievalCacheSchema(
            query_fingerprint="fp-other-repo",
            session_id="session-other",
            repo_id="other-repo",
            intent="search",
        )
        store.cache_retrieval_results(other_cache)

        # Invalidate repo
        store.invalidate_cache_by_repo("repo-to-invalidate")

        # Check invalidation
        assert store.get_cached_results("fp-repo-0") is None
        assert store.get_cached_results("fp-repo-1") is None
        assert store.get_cached_results("fp-repo-2") is None
        assert store.get_cached_results("fp-other-repo") is not None

    def test_store_source_map_success(self, store):
        """Test storing a source map."""
        source_map = SourceMapSchema(
            repo_id="repo-1",
            session_id="session-1",
            truth_preference="git",
            bindings={"main": "/path/to/main.py"},
            conflicts={"duplicate": ["/path/a", "/path/b"]},
            metadata={"timestamp": "2026-03-27"},
        )

        store.store_source_map(source_map)

        retrieved = store.get_source_map("repo-1", "session-1")
        assert retrieved is not None
        assert retrieved.repo_id == "repo-1"
        assert retrieved.truth_preference == "git"

    def test_get_source_map_not_found(self, store):
        """Test retrieving non-existent source map."""
        result = store.get_source_map("nonexistent-repo", "nonexistent-session")
        assert result is None

    def test_close_method(self, store):
        """Test close method (no-op for per-call connections)."""
        store.close()  # Should not raise
        # Verify store still works after close
        artifact = ArtifactSchema(
            id="post-close",
            session_id="session-1",
            origin="test",
            kind="code",
            content_ref="/test",
        )
        store.store_artifact(artifact)
        assert store.retrieve_artifact("post-close") is not None

    def test_compute_hash(self, store):
        """Test SHA256 hash computation."""
        test_content = "test content"
        hash_result = store._compute_hash(test_content)

        # Hash should be consistent
        assert hash_result == store._compute_hash(test_content)

        # Hash should be 64 characters (SHA256 hex)
        assert len(hash_result) == 64

        # Different content should produce different hash
        other_hash = store._compute_hash("different content")
        assert hash_result != other_hash

    def test_store_artifact_with_optional_fields_none(self, store):
        """Test storing artifact with None optional fields."""
        artifact = ArtifactSchema(
            id="minimal",
            session_id="session",
            origin="test",
            kind="text",
            content_ref="ref",
            repo_binding=None,
        )

        store.store_artifact(artifact)
        retrieved = store.retrieve_artifact("minimal")

        assert retrieved.repo_binding is None

    def test_store_chunk_with_empty_collections(self, store):
        """Test storing chunk with empty symbols/neighbors."""
        chunk = ChunkSchema(
            id="empty-chunk",
            source="file.py",
            content="content",
            token_estimate=10,
            symbols=[],
            neighbors=[],
        )

        store.store_chunk(chunk)
        retrieved = store.retrieve_chunk("empty-chunk")

        assert retrieved.symbols == []
        assert retrieved.neighbors == []

    def test_multiple_artifacts_isolation(self, store):
        """Test that multiple artifacts don't interfere with each other."""
        artifacts = [
            ArtifactSchema(
                id=f"artifact-{i}",
                session_id=f"session-{i}",
                origin="test",
                kind="code",
                content_ref=f"/path/{i}",
                size_bytes=i * 100,
            )
            for i in range(5)
        ]

        for artifact in artifacts:
            store.store_artifact(artifact)

        for i, artifact in enumerate(artifacts):
            retrieved = store.retrieve_artifact(f"artifact-{i}")
            assert retrieved.session_id == f"session-{i}"
            assert retrieved.size_bytes == i * 100

    def test_cache_expiry_basic(self, store):
        """Test that expired cache entries return None."""
        # Create cache with minimal TTL
        cache_entry = RetrievalCacheSchema(
            query_fingerprint="fp-expire",
            session_id="session",
            repo_id="repo",
            intent="test",
            ttl_minutes=0,  # Expired immediately
        )

        store.cache_retrieval_results(cache_entry)
        
        # Entry should be expired and return None
        retrieved = store.get_cached_results("fp-expire")
        assert retrieved is None
