"""Tests for artifact store and schemas."""

import pytest

pytest.importorskip("tokenpak.artifact_store", reason="module not available in current build")
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from tokenpak.artifact_store import ArtifactStore
from tokenpak.schemas.artifact import ArtifactSchema
from tokenpak.schemas.chunk import ChunkSchema
from tokenpak.schemas.retrieval_cache import RetrievalCacheSchema
from tokenpak.schemas.source_map import SourceMapSchema


@pytest.fixture
def temp_db():
    """Create temporary database for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = str(Path(tmpdir) / "test.db")
        yield db_path


@pytest.fixture
def store(temp_db):
    """Create artifact store instance."""
    return ArtifactStore(db_path=temp_db)


class TestArtifactSchema:
    """Test artifact schema."""

    def test_artifact_creation(self):
        """Test artifact schema creation."""
        artifact = ArtifactSchema(
            id="test-001",
            session_id="sess-001",
            origin="code_dump",
            kind="python",
            content_ref="/path/to/code.py",
        )

        assert artifact.id == "test-001"
        assert artifact.session_id == "sess-001"
        assert artifact.origin == "code_dump"
        assert artifact.kind == "python"
        assert artifact.content_ref == "/path/to/code.py"
        assert artifact.repo_binding is None
        assert artifact.size_bytes == 0

    def test_artifact_to_dict(self):
        """Test artifact serialization."""
        artifact = ArtifactSchema(
            id="test-001",
            session_id="sess-001",
            origin="code_dump",
            kind="python",
            content_ref="/path/to/code.py",
            labels={"language": "python"},
        )

        data = artifact.to_dict()
        assert data["id"] == "test-001"
        assert data["labels"] == {"language": "python"}
        assert isinstance(data["created_at"], str)

    def test_artifact_from_dict(self):
        """Test artifact deserialization."""
        data = {
            "id": "test-001",
            "session_id": "sess-001",
            "origin": "code_dump",
            "kind": "python",
            "content_ref": "/path/to/code.py",
            "repo_binding": None,
            "size_bytes": 0,
            "token_estimate": 0,
            "labels": {"language": "python"},
            "created_at": datetime.utcnow().isoformat(),
            "accessed_at": datetime.utcnow().isoformat(),
            "stats": {},
        }

        artifact = ArtifactSchema.from_dict(data)
        assert artifact.id == "test-001"
        assert artifact.labels == {"language": "python"}
        assert isinstance(artifact.created_at, datetime)


class TestChunkSchema:
    """Test chunk schema."""

    def test_chunk_creation(self):
        """Test chunk schema creation."""
        chunk = ChunkSchema(
            id="chunk-001",
            source="artifact-001",
            content="def hello():\n    print('hello')",
            token_estimate=10,
            symbols=["hello"],
        )

        assert chunk.id == "chunk-001"
        assert chunk.source == "artifact-001"
        assert chunk.token_estimate == 10
        assert chunk.symbols == ["hello"]

    def test_chunk_neighbors(self):
        """Test chunk neighbor links."""
        chunk = ChunkSchema(
            id="chunk-001",
            source="artifact-001",
            content="def hello(): pass",
            token_estimate=10,
            neighbors=["chunk-002", "chunk-003"],
        )

        assert len(chunk.neighbors) == 2
        assert "chunk-002" in chunk.neighbors

    def test_chunk_serialization(self):
        """Test chunk to_dict and from_dict."""
        chunk = ChunkSchema(
            id="chunk-001",
            source="artifact-001",
            content="code",
            token_estimate=10,
            symbols=["func"],
        )

        data = chunk.to_dict()
        restored = ChunkSchema.from_dict(data)

        assert restored.id == chunk.id
        assert restored.symbols == chunk.symbols


class TestSourceMapSchema:
    """Test source map schema."""

    def test_source_map_creation(self):
        """Test source map creation."""
        smap = SourceMapSchema(
            repo_id="repo-001",
            session_id="sess-001",
            truth_preference="repo",
        )

        assert smap.repo_id == "repo-001"
        assert smap.truth_preference == "repo"

    def test_source_map_resolve_repo_default(self):
        """Test truth resolution defaults to repo."""
        smap = SourceMapSchema(
            repo_id="repo-001",
            session_id="sess-001",
            truth_preference="repo",
        )

        result = smap.resolve("/path/to/file.py")
        assert result == "repo"

    def test_source_map_resolve_artifact(self):
        """Test truth resolution for artifact binding."""
        smap = SourceMapSchema(
            repo_id="repo-001",
            session_id="sess-001",
            truth_preference="repo",
        )

        artifact_id = "artifact-001"
        smap.bind_artifact("/path/to/file.py", artifact_id)

        result = smap.resolve("/path/to/file.py", artifact_id)
        assert result == "artifact"

    def test_source_map_conflict_recording(self):
        """Test conflict recording."""
        smap = SourceMapSchema(
            repo_id="repo-001",
            session_id="sess-001",
            truth_preference="repo",
        )

        smap.record_conflict("/path/to/file.py", "artifact-001")
        smap.record_conflict("/path/to/file.py", "artifact-002")

        assert len(smap.conflicts["/path/to/file.py"]) == 2


class TestRetrievalCacheSchema:
    """Test retrieval cache schema."""

    def test_cache_creation(self):
        """Test cache entry creation."""
        cache = RetrievalCacheSchema(
            query_fingerprint="fp-001",
            session_id="sess-001",
            repo_id="repo-001",
            intent="search",
            coverage_score=0.8,
        )

        assert cache.query_fingerprint == "fp-001"
        assert cache.coverage_score == 0.8
        assert cache.use_count == 0

    def test_cache_expiration(self):
        """Test cache TTL expiration."""
        cache = RetrievalCacheSchema(
            query_fingerprint="fp-001",
            session_id="sess-001",
            repo_id="repo-001",
            intent="search",
            ttl_minutes=0,  # Expire immediately
        )

        # Manually set created_at to past
        cache.created_at = datetime.utcnow() - timedelta(minutes=1)

        assert cache.is_expired() is True

    def test_cache_touch(self):
        """Test cache touch updates metrics."""
        cache = RetrievalCacheSchema(
            query_fingerprint="fp-001",
            session_id="sess-001",
            repo_id="repo-001",
            intent="search",
        )

        initial_count = cache.use_count
        cache.touch()

        assert cache.use_count == initial_count + 1
        assert cache.last_used_at > cache.created_at

    def test_cache_not_expired(self):
        """Test cache TTL not expired."""
        cache = RetrievalCacheSchema(
            query_fingerprint="fp-001",
            session_id="sess-001",
            repo_id="repo-001",
            intent="search",
            ttl_minutes=100,  # Long TTL
        )

        assert cache.is_expired() is False


class TestArtifactStoreBasic:
    """Test artifact store CRUD operations."""

    def test_store_retrieve_artifact(self, store):
        """Test artifact storage and retrieval."""
        artifact = ArtifactSchema(
            id="art-001",
            session_id="sess-001",
            origin="code_dump",
            kind="python",
            content_ref="/path/to/code.py",
            size_bytes=1024,
        )

        stored_id = store.store_artifact(artifact)
        assert stored_id == "art-001"

        retrieved = store.retrieve_artifact("art-001")
        assert retrieved is not None
        assert retrieved.id == "art-001"
        assert retrieved.size_bytes == 1024

    def test_retrieve_nonexistent_artifact(self, store):
        """Test retrieving nonexistent artifact."""
        result = store.retrieve_artifact("nonexistent")
        assert result is None

    def test_store_retrieve_chunk(self, store):
        """Test chunk storage and retrieval."""
        chunk = ChunkSchema(
            id="chunk-001",
            source="art-001",
            content="def hello(): pass",
            token_estimate=10,
            symbols=["hello"],
        )

        stored_id = store.store_chunk(chunk)
        assert stored_id == "chunk-001"

        retrieved = store.retrieve_chunk("chunk-001")
        assert retrieved is not None
        assert retrieved.source == "art-001"
        assert retrieved.symbols == ["hello"]

    def test_chunk_neighbor_retrieval(self, store):
        """Test retrieving chunk neighbors."""
        # Create chunks
        chunk1 = ChunkSchema(
            id="chunk-001",
            source="art-001",
            content="code1",
            token_estimate=10,
            neighbors=["chunk-002"],
        )

        chunk2 = ChunkSchema(
            id="chunk-002",
            source="art-001",
            content="code2",
            token_estimate=10,
        )

        store.store_chunk(chunk1)
        store.store_chunk(chunk2)

        neighbors = store.get_chunk_neighbors("chunk-001")
        assert len(neighbors) == 1
        assert neighbors[0].id == "chunk-002"


class TestRetrievalCache:
    """Test retrieval cache operations."""

    def test_cache_storage_retrieval(self, store):
        """Test cache storage and retrieval."""
        cache = RetrievalCacheSchema(
            query_fingerprint="fp-001",
            session_id="sess-001",
            repo_id="repo-001",
            intent="search",
            results=[{"id": "result-001", "score": 0.9}],
            coverage_score=0.8,
            ttl_minutes=20,
        )

        store.cache_retrieval_results(cache)

        retrieved = store.get_cached_results("fp-001")
        assert retrieved is not None
        assert retrieved.coverage_score == 0.8
        assert len(retrieved.results) == 1

    def test_cache_expiration_on_retrieval(self, store):
        """Test expired cache returns None."""
        cache = RetrievalCacheSchema(
            query_fingerprint="fp-001",
            session_id="sess-001",
            repo_id="repo-001",
            intent="search",
            ttl_minutes=0,  # Expire immediately
        )

        # Manually set created_at to past
        cache.created_at = datetime.utcnow() - timedelta(minutes=1)

        store.cache_retrieval_results(cache)

        # Retrieve should return None (expired)
        retrieved = store.get_cached_results("fp-001")
        assert retrieved is None

    def test_cache_invalidation(self, store):
        """Test cache entry invalidation."""
        cache = RetrievalCacheSchema(
            query_fingerprint="fp-001",
            session_id="sess-001",
            repo_id="repo-001",
            intent="search",
            results=[],
        )

        store.cache_retrieval_results(cache)
        store.invalidate_cache_entry("fp-001")

        retrieved = store.get_cached_results("fp-001")
        assert retrieved is None

    def test_cache_invalidate_by_repo(self, store):
        """Test invalidating all cache for a repo."""
        cache1 = RetrievalCacheSchema(
            query_fingerprint="fp-001",
            session_id="sess-001",
            repo_id="repo-001",
            intent="search",
        )

        cache2 = RetrievalCacheSchema(
            query_fingerprint="fp-002",
            session_id="sess-001",
            repo_id="repo-001",
            intent="search",
        )

        store.cache_retrieval_results(cache1)
        store.cache_retrieval_results(cache2)

        store.invalidate_cache_by_repo("repo-001")

        retrieved1 = store.get_cached_results("fp-001")
        retrieved2 = store.get_cached_results("fp-002")

        assert retrieved1 is None
        assert retrieved2 is None


class TestSourceMap:
    """Test source map operations."""

    def test_store_retrieve_source_map(self, store):
        """Test source map storage and retrieval."""
        smap = SourceMapSchema(
            repo_id="repo-001",
            session_id="sess-001",
            truth_preference="repo",
            bindings={"/path/to/file.py": "artifact-001"},
        )

        store.store_source_map(smap)

        retrieved = store.get_source_map("repo-001", "sess-001")
        assert retrieved is not None
        assert retrieved.truth_preference == "repo"
        assert retrieved.bindings["/path/to/file.py"] == "artifact-001"

    def test_source_map_not_found(self, store):
        """Test retrieving nonexistent source map."""
        result = store.get_source_map("repo-001", "sess-001")
        assert result is None


class TestIntegration:
    """Integration tests for artifact store."""

    def test_full_artifact_workflow(self, store):
        """Test complete artifact workflow."""
        # Create artifact
        artifact = ArtifactSchema(
            id="art-001",
            session_id="sess-001",
            origin="code_dump",
            kind="python",
            content_ref="/path/to/code.py",
        )

        # Create chunks
        chunk1 = ChunkSchema(
            id="chunk-001",
            source="art-001",
            content="def func1(): pass",
            token_estimate=10,
            neighbors=["chunk-002"],
        )

        chunk2 = ChunkSchema(
            id="chunk-002",
            source="art-001",
            content="def func2(): pass",
            token_estimate=10,
            neighbors=["chunk-001"],
        )

        # Store all
        store.store_artifact(artifact)
        store.store_chunk(chunk1)
        store.store_chunk(chunk2)

        # Retrieve and verify
        retrieved_art = store.retrieve_artifact("art-001")
        assert retrieved_art is not None

        neighbors = store.get_chunk_neighbors("chunk-001")
        assert len(neighbors) == 1
        assert neighbors[0].id == "chunk-002"

    def test_cache_with_coverage_score(self, store):
        """Test cache with coverage score tracking."""
        cache = RetrievalCacheSchema(
            query_fingerprint="fp-001",
            session_id="sess-001",
            repo_id="repo-001",
            intent="search",
            results=[
                {"id": "result-001", "score": 0.95},
                {"id": "result-002", "score": 0.80},
            ],
            coverage_score=0.75,  # "ok" range (0.55-0.75)
        )

        store.cache_retrieval_results(cache)

        retrieved = store.get_cached_results("fp-001")
        assert retrieved is not None
        assert retrieved.coverage_score == 0.75
        assert len(retrieved.results) == 2

    def test_pack_plan_in_cache(self, store):
        """Test storing pack plan in cache."""
        pack_plan = {"strategy": "greedy", "blocks": ["b1", "b2"]}

        cache = RetrievalCacheSchema(
            query_fingerprint="fp-001",
            session_id="sess-001",
            repo_id="repo-001",
            intent="search",
            pack_plan=pack_plan,
        )

        store.cache_retrieval_results(cache)

        retrieved = store.get_cached_results("fp-001")
        assert retrieved is not None
        assert retrieved.pack_plan == pack_plan
