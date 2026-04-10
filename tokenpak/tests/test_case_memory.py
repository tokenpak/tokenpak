"""Unit tests for CaseMemoryDB."""

import json
import tempfile
from pathlib import Path

import pytest

from tokenpak.agentic.case_memory import (
    CaseMemoryDB,
    CaseRecord,
    _extract_terms,
)


@pytest.fixture
def temp_db():
    """Create a temporary case memory DB for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "case_memory.json"
        db = CaseMemoryDB(storage_path=db_path)
        yield db, db_path


class TestCaseRecord:
    """Tests for CaseRecord dataclass."""

    def test_case_record_creation(self):
        """Test basic case record creation."""
        case = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Use BM25",
            problem="Need fast search",
            action_taken="Implemented BM25",
            outcome="57ms search",
            lesson_learned="BM25 is sufficient",
            entities=["BM25", "search"],
        )
        assert case.case_id == "case_001"
        assert case.case_type == "decision"
        assert case.confidence == 0.7
        assert case.status == "active"

    def test_case_record_invalid_type(self):
        """Test that invalid case_type raises error."""
        with pytest.raises(ValueError):
            CaseRecord(
                case_id="case_001",
                case_type="invalid_type",
                title="Test",
                problem="Test",
                action_taken="Test",
                outcome="Test",
                lesson_learned="Test",
            )

    def test_case_record_invalid_status(self):
        """Test that invalid status raises error."""
        with pytest.raises(ValueError):
            CaseRecord(
                case_id="case_001",
                case_type="decision",
                title="Test",
                problem="Test",
                action_taken="Test",
                outcome="Test",
                lesson_learned="Test",
                status="invalid_status",
            )

    def test_case_record_confidence_bounds(self):
        """Test that confidence is bounded [0, 1]."""
        case = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Test",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
            confidence=1.5,  # should be clamped to 1.0
        )
        assert case.confidence == 1.0

        case2 = CaseRecord(
            case_id="case_002",
            case_type="decision",
            title="Test",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
            confidence=-0.5,  # should be clamped to 0.0
        )
        assert case2.confidence == 0.0


class TestExtractTerms:
    """Tests for internal _extract_terms function."""

    def test_extract_terms_basic(self):
        """Test basic term extraction."""
        terms = _extract_terms("BM25 and vault search")
        assert terms == {"bm25", "and", "vault", "search"}

    def test_extract_terms_mixed_case(self):
        """Test that extraction is case-insensitive."""
        terms = _extract_terms("BM25 VAULT Search")
        assert terms == {"bm25", "vault", "search"}

    def test_extract_terms_with_underscores(self):
        """Test that underscores are preserved."""
        terms = _extract_terms("vault_sync and git_safe_push")
        assert terms == {"vault_sync", "and", "git_safe_push"}

    def test_extract_terms_empty(self):
        """Test extraction from empty string."""
        terms = _extract_terms("")
        assert terms == set()

    def test_extract_terms_no_alphanumeric(self):
        """Test extraction with only special chars."""
        terms = _extract_terms("!@#$%^&*()")
        assert terms == set()


class TestCRUD:
    """Tests for CRUD operations."""

    def test_add_case(self, temp_db):
        """Test adding a case."""
        db, _ = temp_db
        case = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Test case",
            problem="Test problem",
            action_taken="Test action",
            outcome="Test outcome",
            lesson_learned="Test lesson",
        )
        case_id = db.add(case)
        assert case_id == "case_001"
        assert db.get("case_001") is not None

    def test_add_case_auto_id(self, temp_db):
        """Test adding a case without case_id (auto-generated)."""
        db, _ = temp_db
        case = CaseRecord(
            case_id="",
            case_type="decision",
            title="Test case",
            problem="Test problem",
            action_taken="Test action",
            outcome="Test outcome",
            lesson_learned="Test lesson",
        )
        case_id = db.add(case)
        assert case_id.startswith("case_")
        assert len(case_id) > 5
        assert db.get(case_id) is not None

    def test_get_case(self, temp_db):
        """Test retrieving a case."""
        db, _ = temp_db
        case = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Test",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
        )
        db.add(case)
        retrieved = db.get("case_001")
        assert retrieved is not None
        assert retrieved.title == "Test"

    def test_get_nonexistent_case(self, temp_db):
        """Test retrieving a nonexistent case."""
        db, _ = temp_db
        assert db.get("nonexistent") is None

    def test_update_case(self, temp_db):
        """Test updating a case."""
        db, _ = temp_db
        case = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Original",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
        )
        db.add(case)

        updated = db.get("case_001")
        updated.title = "Updated"
        result = db.update(updated)
        assert result is True
        assert db.get("case_001").title == "Updated"

    def test_update_nonexistent_case(self, temp_db):
        """Test updating a nonexistent case."""
        db, _ = temp_db
        case = CaseRecord(
            case_id="nonexistent",
            case_type="decision",
            title="Test",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
        )
        result = db.update(case)
        assert result is False

    def test_delete_case(self, temp_db):
        """Test deleting a case."""
        db, _ = temp_db
        case = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Test",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
        )
        db.add(case)
        result = db.delete("case_001")
        assert result is True
        assert db.get("case_001") is None

    def test_delete_nonexistent_case(self, temp_db):
        """Test deleting a nonexistent case."""
        db, _ = temp_db
        result = db.delete("nonexistent")
        assert result is False


class TestSearch:
    """Tests for search functionality."""

    def test_search_basic(self, temp_db):
        """Test basic entity matching search."""
        db, _ = temp_db
        case = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="BM25 decision",
            problem="Search performance",
            action_taken="Implemented BM25",
            outcome="Fast search",
            lesson_learned="BM25 works",
            entities=["BM25", "search", "vault"],
        )
        db.add(case)

        # Search with matching terms
        results = db.search("How do we search the vault?")
        assert len(results) == 1
        assert results[0].case_id == "case_001"

    def test_search_no_match(self, temp_db):
        """Test search with no matches."""
        db, _ = temp_db
        case = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Test",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
            entities=["BM25", "search"],
        )
        db.add(case)

        # Search with non-matching terms
        results = db.search("How do we deploy databases?")
        assert len(results) == 0

    def test_search_type_filter(self, temp_db):
        """Test search with case_type filter."""
        db, _ = temp_db
        decision = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Decision",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
            entities=["vault", "search"],
        )
        workflow = CaseRecord(
            case_id="case_002",
            case_type="workflow",
            title="Workflow",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
            entities=["vault", "search"],
        )
        db.add(decision)
        db.add(workflow)

        # Search for workflow only
        results = db.search("vault search", case_type="workflow")
        assert len(results) == 1
        assert results[0].case_type == "workflow"

    def test_search_top_k(self, temp_db):
        """Test search with top_k limit."""
        db, _ = temp_db
        for i in range(5):
            case = CaseRecord(
                case_id=f"case_{i:03d}",
                case_type="decision",
                title=f"Case {i}",
                problem="Test",
                action_taken="Test",
                outcome="Test",
                lesson_learned="Test",
                entities=["vault", "search"],
            )
            db.add(case)

        # Search with top_k=2
        results = db.search("vault search", top_k=2)
        assert len(results) == 2

    def test_search_respects_superseded(self, temp_db):
        """Test that search skips superseded cases."""
        db, _ = temp_db
        original = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Original",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
            entities=["vault", "search"],
            status="superseded",
            superseded_by="case_002",
        )
        replacement = CaseRecord(
            case_id="case_002",
            case_type="decision",
            title="Replacement",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
            entities=["vault", "search"],
            status="active",
        )
        db.add(original)
        db.add(replacement)

        results = db.search("vault search")
        # Should only get the active replacement, not the superseded original
        assert len(results) == 1
        assert results[0].case_id == "case_002"

    def test_search_scored_by_confidence(self, temp_db):
        """Test that results are scored by confidence."""
        db, _ = temp_db
        case1 = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="High confidence",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
            entities=["vault", "search"],
            confidence=0.9,
        )
        case2 = CaseRecord(
            case_id="case_002",
            case_type="decision",
            title="Low confidence",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
            entities=["vault", "search"],
            confidence=0.3,
        )
        db.add(case1)
        db.add(case2)

        results = db.search("vault search")
        # Higher confidence should come first
        assert results[0].confidence > results[1].confidence


class TestLearningLoop:
    """Tests for learning loop / record_outcome."""

    def test_record_outcome_success(self, temp_db):
        """Test recording a successful outcome."""
        db, _ = temp_db
        case = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Test",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
            confidence=0.7,
        )
        db.add(case)

        result = db.record_outcome("case_001", success=True)
        assert result is not None
        assert result.success_count == 1
        assert result.retrieval_count == 1
        # Confidence should increase by 0.05
        assert result.confidence == 0.75

    def test_record_outcome_failure(self, temp_db):
        """Test recording a failed outcome."""
        db, _ = temp_db
        case = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Test",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
            confidence=0.7,
        )
        db.add(case)

        result = db.record_outcome("case_001", success=False)
        assert result is not None
        assert result.failure_count == 1
        assert result.retrieval_count == 1
        # Confidence should decrease by 0.1
        assert result.confidence == 0.6

    def test_record_outcome_confidence_bounds(self, temp_db):
        """Test that confidence stays bounded [0, 1]."""
        db, _ = temp_db
        case = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Test",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
            confidence=0.95,
        )
        db.add(case)

        # Multiple successes
        for _ in range(10):
            db.record_outcome("case_001", success=True)

        result = db.get("case_001")
        # Confidence should not exceed 1.0
        assert result.confidence == 1.0

        # Reset and test lower bound
        case.confidence = 0.05
        db.update(case)

        for _ in range(10):
            db.record_outcome("case_001", success=False)

        result = db.get("case_001")
        # Confidence should not go below 0.0
        assert result.confidence == 0.0

    def test_record_outcome_nonexistent(self, temp_db):
        """Test recording outcome for nonexistent case."""
        db, _ = temp_db
        result = db.record_outcome("nonexistent", success=True)
        assert result is None


class TestQueries:
    """Tests for query methods (all, by_type, active, count)."""

    def test_all(self, temp_db):
        """Test all() method."""
        db, _ = temp_db
        for i in range(3):
            case = CaseRecord(
                case_id=f"case_{i:03d}",
                case_type="decision",
                title=f"Case {i}",
                problem="Test",
                action_taken="Test",
                outcome="Test",
                lesson_learned="Test",
            )
            db.add(case)

        cases = db.all()
        assert len(cases) == 3

    def test_by_type(self, temp_db):
        """Test by_type() method."""
        db, _ = temp_db
        decision = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Decision",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
        )
        workflow = CaseRecord(
            case_id="case_002",
            case_type="workflow",
            title="Workflow",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
        )
        db.add(decision)
        db.add(workflow)

        decisions = db.by_type("decision")
        assert len(decisions) == 1
        assert decisions[0].case_type == "decision"

    def test_active(self, temp_db):
        """Test active() method."""
        db, _ = temp_db
        active = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Active",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
            status="active",
        )
        superseded = CaseRecord(
            case_id="case_002",
            case_type="decision",
            title="Superseded",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
            status="superseded",
        )
        db.add(active)
        db.add(superseded)

        active_cases = db.active()
        assert len(active_cases) == 1
        assert active_cases[0].status == "active"

    def test_count(self, temp_db):
        """Test count() method."""
        db, _ = temp_db
        for i in range(5):
            case = CaseRecord(
                case_id=f"case_{i:03d}",
                case_type="decision",
                title=f"Case {i}",
                problem="Test",
                action_taken="Test",
                outcome="Test",
                lesson_learned="Test",
            )
            db.add(case)

        assert db.count() == 5


class TestPersistence:
    """Tests for persistence (saving/loading)."""

    def test_persistence_save_and_load(self, temp_db):
        """Test that cases are persisted to disk and can be reloaded."""
        db, db_path = temp_db
        case = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Persistent case",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
        )
        db.add(case)

        # Create a new DB instance and load from the same path
        db2 = CaseMemoryDB(storage_path=db_path)
        retrieved = db2.get("case_001")
        assert retrieved is not None
        assert retrieved.title == "Persistent case"

    def test_json_format(self, temp_db):
        """Test that the JSON format is valid."""
        db, db_path = temp_db
        case = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Test case",
            problem="Test problem",
            action_taken="Test action",
            outcome="Test outcome",
            lesson_learned="Test lesson",
            entities=["test", "case"],
        )
        db.add(case)

        # Read and validate JSON
        content = db_path.read_text()
        data = json.loads(content)
        assert "case_001" in data
        assert data["case_001"]["title"] == "Test case"

    def test_reload(self, temp_db):
        """Test reload() method."""
        db, db_path = temp_db
        case = CaseRecord(
            case_id="case_001",
            case_type="decision",
            title="Original",
            problem="Test",
            action_taken="Test",
            outcome="Test",
            lesson_learned="Test",
        )
        db.add(case)

        # Modify on disk
        data = json.loads(db_path.read_text())
        data["case_001"]["title"] = "Modified"
        db_path.write_text(json.dumps(data))

        # Reload and verify
        db.reload()
        retrieved = db.get("case_001")
        assert retrieved.title == "Modified"
