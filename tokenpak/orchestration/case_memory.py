"""TokenPak — Case-Based Reasoning Store

Persistent memory of decisions, workflows, lessons, and anti-patterns.
Enables memory-first retrieval and learning loops.

Key concepts
------------
- CaseRecord: dataclass capturing problem, action, outcome, lesson,
  entities for search, confidence, and validation state.
- CaseMemoryDB: CRUD + entity-based search + learning loop.
  Persists to ~/.tokenpak/case_memory.json.
- Entity matching: no embeddings, pure string set operations.
  Query terms matched against case.entities, scored by overlap * confidence.
- Learning loop: success → confidence up; failure → down.
  Bounded [0.0, 1.0].

Usage
-----
    db = CaseMemoryDB()
    cases = db.search("How do we deploy without downtime?")
    if cases:
        case = cases[0]
        # Apply case lesson...
        db.record_outcome(case.case_id, success=True)
    else:
        db.add(CaseRecord(
            case_id="case_001",
            case_type="workflow",
            title="Safe proxy deployment",
            problem="Deploy changes without downtime",
            action_taken="Use staging → validate → sync → restart",
            outcome="Zero downtime",
            lesson_learned="Never edit production directly",
            entities=["proxy", "deployment", "zero-downtime"],
        ))
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CASE_MEMORY_PATH = Path.home() / ".tokenpak" / "case_memory.json"

# Confidence bounds
_CONF_MIN = 0.0
_CONF_MAX = 1.0
_CONF_STEP_UP = 0.05
_CONF_STEP_DOWN = 0.1

# Valid case types and statuses
CASE_TYPES = {"decision", "workflow", "lesson", "anti-pattern", "error"}
CASE_STATUSES = {"active", "superseded", "rejected", "experimental"}


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class CaseRecord:
    """A recorded case with problem, action, outcome, and lesson."""

    case_id: str
    case_type: str  # "decision" | "workflow" | "lesson" | "anti-pattern" | "error"
    title: str  # human-readable title
    problem: str  # what was the problem/question?
    action_taken: str  # what did we do?
    outcome: str  # what happened?
    lesson_learned: str  # what did we learn?
    entities: list[str] = field(
        default_factory=list
    )  # search terms: ["BM25", "vault search", "embeddings"]
    source_blocks: list[str] = field(default_factory=list)  # vault block IDs that informed this
    confidence: float = 0.7  # 0.0-1.0, updated by learning loop
    status: str = "active"  # "active" | "superseded" | "rejected" | "experimental"
    superseded_by: Optional[str] = None  # case_id of replacement (if status=superseded)
    created_at: str = field(default_factory=lambda: _now_iso())
    updated_at: str = field(default_factory=lambda: _now_iso())
    retrieval_count: int = 0  # how many times this was retrieved
    success_count: int = 0  # outcome was helpful
    failure_count: int = 0  # outcome was unhelpful

    def __post_init__(self) -> None:
        # Validate case_type
        if self.case_type not in CASE_TYPES:
            raise ValueError(f"case_type must be one of {CASE_TYPES}, got {self.case_type}")
        # Validate status
        if self.status not in CASE_STATUSES:
            raise ValueError(f"status must be one of {CASE_STATUSES}, got {self.status}")
        # Bound confidence
        self.confidence = float(max(_CONF_MIN, min(_CONF_MAX, self.confidence)))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _extract_terms(text: str) -> set[str]:
    """Extract searchable terms from text (alphanumeric + underscore)."""
    return set(re.findall(r"[a-z0-9_]+", text.lower()))


def _case_to_dict(case: CaseRecord) -> dict:
    return asdict(case)


def _case_from_dict(d: dict) -> CaseRecord:
    return CaseRecord(
        case_id=d["case_id"],
        case_type=d.get("case_type", "lesson"),
        title=d.get("title", ""),
        problem=d.get("problem", ""),
        action_taken=d.get("action_taken", ""),
        outcome=d.get("outcome", ""),
        lesson_learned=d.get("lesson_learned", ""),
        entities=d.get("entities", []),
        source_blocks=d.get("source_blocks", []),
        confidence=float(d.get("confidence", 0.7)),
        status=d.get("status", "active"),
        superseded_by=d.get("superseded_by"),
        created_at=d.get("created_at", _now_iso()),
        updated_at=d.get("updated_at", _now_iso()),
        retrieval_count=int(d.get("retrieval_count", 0)),
        success_count=int(d.get("success_count", 0)),
        failure_count=int(d.get("failure_count", 0)),
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def _load_cases(path: Path) -> dict[str, CaseRecord]:
    """Load case memory from JSON file."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
        return {k: _case_from_dict(v) for k, v in raw.items()}
    except (json.JSONDecodeError, KeyError, TypeError, ValueError):
        return {}


def _save_cases(cases: dict[str, CaseRecord], path: Path) -> None:
    """Persist case memory to JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: _case_to_dict(v) for k, v in cases.items()}, indent=2))


# ---------------------------------------------------------------------------
# Public API — CaseMemoryDB
# ---------------------------------------------------------------------------


class CaseMemoryDB:
    """Persistent case-based reasoning store with CRUD, search, and learning.

    Args:
        storage_path: Path to the JSON file. Defaults to
                      ``~/.tokenpak/case_memory.json``.
    """

    def __init__(self, storage_path: Optional[Path | str] = None) -> None:
        self._path = Path(storage_path) if storage_path else DEFAULT_CASE_MEMORY_PATH
        self._cases: dict[str, CaseRecord] = _load_cases(self._path)

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def add(self, case: CaseRecord) -> str:
        """Add or overwrite a case record.

        If case_id is empty, generates a unique one.
        Returns the case_id.
        """
        if not case.case_id:
            case.case_id = f"case_{uuid.uuid4().hex[:12]}"
        case.updated_at = _now_iso()
        self._cases[case.case_id] = case
        self._persist()
        return case.case_id

    def get(self, case_id: str) -> Optional[CaseRecord]:
        """Return a case by ID, or None if not found."""
        return self._cases.get(case_id)

    def update(self, case: CaseRecord) -> bool:
        """Update an existing case (must exist). Returns True if succeeded."""
        if case.case_id not in self._cases:
            return False
        case.updated_at = _now_iso()
        self._cases[case.case_id] = case
        self._persist()
        return True

    def delete(self, case_id: str) -> bool:
        """Remove a case. Returns True if it existed."""
        if case_id in self._cases:
            del self._cases[case_id]
            self._persist()
            return True
        return False

    # ------------------------------------------------------------------
    # Query / retrieval
    # ------------------------------------------------------------------

    def all(self) -> list[CaseRecord]:
        """Return all cases."""
        return list(self._cases.values())

    def by_type(self, case_type: str) -> list[CaseRecord]:
        """Return cases of a given type."""
        return [c for c in self._cases.values() if c.case_type == case_type]

    def active(self) -> list[CaseRecord]:
        """Return only active cases (status='active')."""
        return [c for c in self._cases.values() if c.status == "active"]

    def count(self) -> int:
        return len(self._cases)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self, query: str, case_type: Optional[str] = None, top_k: int = 5
    ) -> list[CaseRecord]:
        """Search cases by entity overlap.

        Extracts terms from the query, matches against case.entities,
        scores by (overlap count * confidence). Skips superseded cases.
        If case_type filter is active, only returns that type.

        Args:
            query: Search query string.
            case_type: Optional case type filter.
            top_k: Number of results to return.

        Returns:
            List of matching cases, sorted by score descending.
        """
        query_terms = _extract_terms(query)
        if not query_terms:
            return []

        scored: list[tuple[CaseRecord, float]] = []

        for case in self._cases.values():
            # Skip if type filter is active and doesn't match
            if case_type and case.case_type != case_type:
                continue

            # Skip superseded cases
            if case.status == "superseded":
                continue

            # Extract entity terms
            entity_terms = set()
            for entity in case.entities:
                entity_terms.update(_extract_terms(entity))

            # Score by overlap count * confidence
            overlap = len(query_terms & entity_terms)
            if overlap > 0:
                score = overlap * case.confidence
                scored.append((case, score))

        # Sort by score descending
        scored.sort(key=lambda x: -x[1])

        # Return top_k
        return [case for case, _ in scored[:top_k]]

    # ------------------------------------------------------------------
    # Learning loop
    # ------------------------------------------------------------------

    def record_outcome(self, case_id: str, success: bool) -> Optional[CaseRecord]:
        """Update a case after it's been applied.

        - Success: increment success_count, nudge confidence up (+0.05).
        - Failure: increment failure_count, nudge confidence down (-0.1).
        Confidence is always bounded [0.0, 1.0].

        Args:
            case_id: ID of the case that was applied.
            success: Whether the case was helpful.

        Returns:
            Updated case or None if not found.
        """
        case = self._cases.get(case_id)
        if case is None:
            return None

        case.retrieval_count += 1

        if success:
            case.success_count += 1
            case.confidence = min(_CONF_MAX, case.confidence + _CONF_STEP_UP)
        else:
            case.failure_count += 1
            case.confidence = max(_CONF_MIN, case.confidence - _CONF_STEP_DOWN)

        case.updated_at = _now_iso()
        self._persist()
        return case

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _persist(self) -> None:
        _save_cases(self._cases, self._path)

    def reload(self) -> None:
        """Re-read the on-disk store (useful after external edits)."""
        self._cases = _load_cases(self._path)


# ---------------------------------------------------------------------------
# Module-level convenience functions (operate on a shared singleton)
# ---------------------------------------------------------------------------

_default_db: Optional[CaseMemoryDB] = None


def _get_db() -> CaseMemoryDB:
    global _default_db
    if _default_db is None:
        _default_db = CaseMemoryDB()
    return _default_db


def search(query: str, case_type: Optional[str] = None, top_k: int = 5) -> list[CaseRecord]:
    """Search the default case memory DB."""
    return _get_db().search(query, case_type, top_k)


def record_outcome(case_id: str, *, success: bool) -> Optional[CaseRecord]:
    """Record outcome in the default case memory DB."""
    return _get_db().record_outcome(case_id, success=success)


def add_case(case: CaseRecord) -> str:
    """Add a case to the default case memory DB."""
    return _get_db().add(case)
