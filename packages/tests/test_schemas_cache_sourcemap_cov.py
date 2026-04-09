"""
Tests for tokenpak.schemas.retrieval_cache and tokenpak.schemas.source_map.

Coverage targets:
- RetrievalCacheSchema defaults, TTL expiry, touch behavior
- Serialization/deserialization (to_dict/from_dict)
- SourceMapSchema resolution, binding, conflict tracking
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tokenpak.schemas.retrieval_cache import RetrievalCacheSchema
from tokenpak.schemas.source_map import SourceMapSchema


# ---------------------------------------------------------------------------
# RetrievalCacheSchema tests
# ---------------------------------------------------------------------------


class TestRetrievalCacheSchema:
    """Tests for RetrievalCacheSchema behavior and serialization."""

    def test_defaults(self):
        """Default values are set as expected."""
        schema = RetrievalCacheSchema(
            query_fingerprint="qf",
            session_id="sess",
            repo_id="repo",
            intent="search",
        )
        assert schema.results == []
        assert schema.coverage_score == 0.0
        assert schema.pack_plan is None
        assert schema.ttl_minutes == 20
        assert isinstance(schema.created_at, datetime)
        assert isinstance(schema.last_used_at, datetime)
        assert schema.created_at.tzinfo is not None
        assert schema.last_used_at.tzinfo is not None
        assert schema.use_count == 0
        assert schema.metadata == {}

    def test_is_expired_true_when_past_ttl(self):
        """is_expired returns True when created_at is beyond TTL."""
        created_at = datetime.now(timezone.utc) - timedelta(minutes=25)
        schema = RetrievalCacheSchema(
            query_fingerprint="qf",
            session_id="sess",
            repo_id="repo",
            intent="search",
            ttl_minutes=20,
            created_at=created_at,
            last_used_at=created_at,
        )
        assert schema.is_expired() is True

    def test_is_expired_false_when_within_ttl(self):
        """is_expired returns False when created_at is within TTL."""
        created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        schema = RetrievalCacheSchema(
            query_fingerprint="qf",
            session_id="sess",
            repo_id="repo",
            intent="search",
            ttl_minutes=20,
            created_at=created_at,
            last_used_at=created_at,
        )
        assert schema.is_expired() is False

    def test_touch_updates_last_used_and_use_count(self):
        """touch updates last_used_at and increments use_count."""
        created_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        last_used = datetime.now(timezone.utc) - timedelta(minutes=3)
        schema = RetrievalCacheSchema(
            query_fingerprint="qf",
            session_id="sess",
            repo_id="repo",
            intent="search",
            created_at=created_at,
            last_used_at=last_used,
            use_count=3,
        )
        schema.touch()
        assert schema.use_count == 4
        assert schema.last_used_at >= last_used

    def test_to_dict_serializes_datetimes(self):
        """to_dict outputs ISO-formatted datetime strings."""
        created_at = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
        last_used = datetime(2026, 4, 6, 12, 5, 0, tzinfo=timezone.utc)
        schema = RetrievalCacheSchema(
            query_fingerprint="qf",
            session_id="sess",
            repo_id="repo",
            intent="search",
            created_at=created_at,
            last_used_at=last_used,
        )
        payload = schema.to_dict()
        assert payload["created_at"] == created_at.isoformat()
        assert payload["last_used_at"] == last_used.isoformat()
        assert payload["query_fingerprint"] == "qf"

    def test_from_dict_parses_isoformat_strings(self):
        """from_dict parses ISO strings into datetime objects."""
        created_at = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc).isoformat()
        last_used = datetime(2026, 4, 6, 12, 5, 0, tzinfo=timezone.utc).isoformat()
        data = {
            "query_fingerprint": "qf",
            "session_id": "sess",
            "repo_id": "repo",
            "intent": "search",
            "created_at": created_at,
            "last_used_at": last_used,
        }
        schema = RetrievalCacheSchema.from_dict(data)
        assert isinstance(schema.created_at, datetime)
        assert isinstance(schema.last_used_at, datetime)
        assert schema.created_at.isoformat() == created_at
        assert schema.last_used_at.isoformat() == last_used

    def test_from_dict_does_not_mutate_input(self):
        """from_dict should not mutate the input dictionary."""
        created_at = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc).isoformat()
        data = {
            "query_fingerprint": "qf",
            "session_id": "sess",
            "repo_id": "repo",
            "intent": "search",
            "created_at": created_at,
            "last_used_at": created_at,
        }
        RetrievalCacheSchema.from_dict(data)
        assert data["created_at"] == created_at
        assert isinstance(data["created_at"], str)

    def test_from_dict_accepts_datetime_objects(self):
        """from_dict supports datetime inputs without re-parsing."""
        created_at = datetime(2026, 4, 6, 12, 0, 0, tzinfo=timezone.utc)
        last_used = datetime(2026, 4, 6, 12, 5, 0, tzinfo=timezone.utc)
        schema = RetrievalCacheSchema.from_dict(
            {
                "query_fingerprint": "qf",
                "session_id": "sess",
                "repo_id": "repo",
                "intent": "search",
                "created_at": created_at,
                "last_used_at": last_used,
            }
        )
        assert schema.created_at == created_at
        assert schema.last_used_at == last_used

    def test_roundtrip_to_from_dict_preserves_fields(self):
        """Roundtrip serialization preserves field values."""
        schema = RetrievalCacheSchema(
            query_fingerprint="qf",
            session_id="sess",
            repo_id="repo",
            intent="search",
            results=[{"doc": "one"}],
            coverage_score=0.75,
            pack_plan={"plan": "alpha"},
            ttl_minutes=15,
            metadata={"source": "unit"},
        )
        payload = schema.to_dict()
        rebuilt = RetrievalCacheSchema.from_dict(payload)
        assert rebuilt.query_fingerprint == schema.query_fingerprint
        assert rebuilt.results == schema.results
        assert rebuilt.coverage_score == schema.coverage_score
        assert rebuilt.pack_plan == schema.pack_plan
        assert rebuilt.ttl_minutes == schema.ttl_minutes
        assert rebuilt.metadata == schema.metadata


# ---------------------------------------------------------------------------
# SourceMapSchema tests
# ---------------------------------------------------------------------------


class TestSourceMapSchema:
    """Tests for SourceMapSchema behavior and serialization."""

    def test_defaults(self):
        """Default mappings are empty dicts."""
        schema = SourceMapSchema(
            repo_id="repo",
            session_id="sess",
            truth_preference="repo",
        )
        assert schema.bindings == {}
        assert schema.conflicts == {}
        assert schema.metadata == {}

    def test_resolve_prefers_bound_artifact(self):
        """resolve returns artifact when binding matches artifact_id."""
        schema = SourceMapSchema(
            repo_id="repo",
            session_id="sess",
            truth_preference="repo",
            bindings={"path.py": "art-1"},
        )
        assert schema.resolve("path.py", "art-1") == "artifact"

    def test_resolve_uses_truth_preference_on_mismatch(self):
        """resolve falls back to truth_preference on mismatch."""
        schema = SourceMapSchema(
            repo_id="repo",
            session_id="sess",
            truth_preference="repo",
            bindings={"path.py": "art-1"},
        )
        assert schema.resolve("path.py", "art-2") == "repo"

    def test_resolve_uses_truth_preference_when_unbound(self):
        """resolve returns truth_preference when no binding exists."""
        schema = SourceMapSchema(
            repo_id="repo",
            session_id="sess",
            truth_preference="artifact",
        )
        assert schema.resolve("path.py", "art-1") == "artifact"

    def test_bind_artifact_sets_binding(self):
        """bind_artifact sets the path binding."""
        schema = SourceMapSchema(
            repo_id="repo",
            session_id="sess",
            truth_preference="repo",
        )
        schema.bind_artifact("path.py", "art-1")
        assert schema.bindings["path.py"] == "art-1"

    def test_record_conflict_creates_list(self):
        """record_conflict creates a new list for a path."""
        schema = SourceMapSchema(
            repo_id="repo",
            session_id="sess",
            truth_preference="repo",
        )
        schema.record_conflict("path.py", "art-1")
        assert schema.conflicts["path.py"] == ["art-1"]

    def test_record_conflict_appends(self):
        """record_conflict appends additional conflicts."""
        schema = SourceMapSchema(
            repo_id="repo",
            session_id="sess",
            truth_preference="repo",
            conflicts={"path.py": ["art-1"]},
        )
        schema.record_conflict("path.py", "art-2")
        assert schema.conflicts["path.py"] == ["art-1", "art-2"]

    def test_to_dict_outputs_expected_payload(self):
        """to_dict outputs expected keys and values."""
        schema = SourceMapSchema(
            repo_id="repo",
            session_id="sess",
            truth_preference="repo",
            bindings={"path.py": "art-1"},
            conflicts={"path.py": ["art-1"]},
            metadata={"mode": "strict"},
        )
        payload = schema.to_dict()
        assert payload["repo_id"] == "repo"
        assert payload["bindings"]["path.py"] == "art-1"
        assert payload["conflicts"]["path.py"] == ["art-1"]
        assert payload["metadata"]["mode"] == "strict"

    def test_roundtrip_to_from_dict_preserves_fields(self):
        """Roundtrip serialization preserves field values."""
        schema = SourceMapSchema(
            repo_id="repo",
            session_id="sess",
            truth_preference="repo",
            bindings={"path.py": "art-1"},
            conflicts={"path.py": ["art-1", "art-2"]},
            metadata={"mode": "strict"},
        )
        payload = schema.to_dict()
        rebuilt = SourceMapSchema.from_dict(payload)
        assert rebuilt.repo_id == schema.repo_id
        assert rebuilt.session_id == schema.session_id
        assert rebuilt.truth_preference == schema.truth_preference
        assert rebuilt.bindings == schema.bindings
        assert rebuilt.conflicts == schema.conflicts
        assert rebuilt.metadata == schema.metadata
