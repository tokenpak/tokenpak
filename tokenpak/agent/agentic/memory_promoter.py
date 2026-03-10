"""Memory Promotion Rules for TokenPak Agent Learning.

Implements a 4-tier memory system with strict promotion gates:
  Tier 1: Working memory  — current session only, auto-expires
  Tier 2: Session memory  — persists across turns, TTL in hours
  Tier 3: Project memory  — persists across sessions, TTL in days
  Tier 4: Durable memory  — permanent, highest quality gate

Promotion Flow:
  New lesson → Tier 1
  2+ occurrences → candidate for Tier 2
  5+ with >70% success → candidate for Tier 3
  10+ with >85% success, not contradicted in 7 days → Tier 4

Demotion:
  Contradicted lesson → demote one tier
  Unused for >30 days → demote one tier
  Failed validation → remove from all tiers

Integrates with learning.py via record_lesson() / promote_all().
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

# ---------------------------------------------------------------------------
# Promotion gates
# ---------------------------------------------------------------------------

PROMOTION_RULES = {
    "min_occurrences": 2,       # happened more than once
    "min_success_rate": 0.7,    # validated by outcome
    "not_contradicted_days": 7, # not contradicted in last 7 days
    "material_savings": 0.15,   # reduces future work by >15%
    "specificity_score": 0.5,   # specific enough to be actionable
}

# Per-tier thresholds (override defaults above where stricter)
TIER_GATES = {
    1: {  # Working → Session
        "min_occurrences": 2,
        "min_success_rate": 0.0,    # any positive outcome
        "specificity_score": 0.0,   # no specificity required yet
    },
    2: {  # Session → Project
        "min_occurrences": 5,
        "min_success_rate": 0.70,
        "material_savings": 0.15,
        "specificity_score": 0.5,
    },
    3: {  # Project → Durable
        "min_occurrences": 10,
        "min_success_rate": 0.85,
        "not_contradicted_days": 7,
        "material_savings": 0.15,
        "specificity_score": 0.5,
    },
}

# TTL per tier (None = no expiry)
TIER_TTL = {
    1: timedelta(hours=4),    # Working memory: 4 hours
    2: timedelta(hours=24),   # Session memory: 1 day
    3: timedelta(days=30),    # Project memory: 30 days
    4: None,                  # Durable: permanent (but can be demoted)
}

UNUSED_DEMOTION_DAYS = 30     # Demote if not accessed for 30 days
DEFAULT_MEMORY_PATH = os.path.expanduser("~/.tokenpak/memory_tiers.json")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


@dataclass
class Lesson:
    """A single learned lesson tracked across tiers."""

    id: str                           # unique identifier (e.g. "model_routing_CODING")
    content: str                      # human-readable lesson text
    tier: int = 1                     # current tier (1-4)
    occurrences: int = 1              # how many times observed
    successes: int = 0                # validated positive outcomes
    failures: int = 0                 # validated negative outcomes
    specificity_score: float = 0.5   # 0.0–1.0, how actionable
    material_savings: float = 0.0    # estimated work reduction (0.0–1.0)
    contradictions: int = 0           # contradiction events
    last_contradicted: Optional[str] = None   # ISO datetime
    last_accessed: Optional[str] = None       # ISO datetime
    last_promoted: Optional[str] = None       # ISO datetime
    created: str = field(default_factory=_now_iso)
    updated: str = field(default_factory=_now_iso)
    metadata: Dict = field(default_factory=dict)

    @property
    def success_rate(self) -> float:
        total = self.successes + self.failures
        return self.successes / total if total > 0 else 0.0

    def touch(self) -> None:
        self.last_accessed = _now_iso()
        self.updated = _now_iso()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Lesson":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------

def _load_store(path: str) -> Dict[str, dict]:
    p = Path(path)
    if p.exists():
        try:
            data = json.loads(p.read_text())
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_store(store: Dict[str, dict], path: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(store, indent=2))


def _load_lessons(path: str) -> Dict[str, Lesson]:
    raw = _load_store(path)
    lessons: Dict[str, Lesson] = {}
    for lid, d in raw.items():
        try:
            lessons[lid] = Lesson.from_dict(d)
        except (TypeError, KeyError):
            pass
    return lessons


def _save_lessons(lessons: Dict[str, Lesson], path: str) -> None:
    store = {lid: lesson.to_dict() for lid, lesson in lessons.items()}
    _save_store(store, path)


# ---------------------------------------------------------------------------
# Core promotion logic
# ---------------------------------------------------------------------------

def _check_gate(lesson: Lesson, gate: dict) -> tuple[bool, List[str]]:
    """Check if a lesson passes the given promotion gate.

    Returns (passed, list_of_reasons_if_failed).
    """
    reasons: List[str] = []

    if lesson.occurrences < gate.get("min_occurrences", 1):
        reasons.append(
            f"occurrences={lesson.occurrences} < {gate['min_occurrences']}"
        )

    min_sr = gate.get("min_success_rate", 0.0)
    if min_sr > 0 and lesson.success_rate < min_sr:
        reasons.append(
            f"success_rate={lesson.success_rate:.2f} < {min_sr}"
        )

    min_spec = gate.get("specificity_score", 0.0)
    if lesson.specificity_score < min_spec:
        reasons.append(
            f"specificity_score={lesson.specificity_score:.2f} < {min_spec}"
        )

    min_savings = gate.get("material_savings", 0.0)
    if lesson.material_savings < min_savings:
        reasons.append(
            f"material_savings={lesson.material_savings:.2f} < {min_savings}"
        )

    not_contradicted_days = gate.get("not_contradicted_days", 0)
    if not_contradicted_days > 0 and lesson.last_contradicted:
        contradicted_at = _parse_dt(lesson.last_contradicted)
        if contradicted_at:
            window = timedelta(days=not_contradicted_days)
            if _now() - contradicted_at < window:
                reasons.append(
                    f"contradicted within last {not_contradicted_days} days"
                )

    return (len(reasons) == 0, reasons)


def _is_expired(lesson: Lesson) -> bool:
    """Return True if lesson has passed its tier TTL."""
    ttl = TIER_TTL.get(lesson.tier)
    if ttl is None:
        return False  # Tier 4 never expires by TTL
    created_at = _parse_dt(lesson.created)
    if created_at is None:
        return False
    return _now() - created_at > ttl


def _is_unused_too_long(lesson: Lesson) -> bool:
    """Return True if lesson hasn't been accessed in UNUSED_DEMOTION_DAYS."""
    if lesson.tier <= 1:
        return False  # Don't demote from tier 1 for inactivity
    last_used = _parse_dt(lesson.last_accessed) or _parse_dt(lesson.created)
    if last_used is None:
        return False
    return _now() - last_used > timedelta(days=UNUSED_DEMOTION_DAYS)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def record_lesson(
    lesson_id: str,
    content: str,
    outcome: Optional[float] = None,
    specificity_score: float = 0.5,
    material_savings: float = 0.0,
    metadata: Optional[dict] = None,
    memory_path: str = DEFAULT_MEMORY_PATH,
) -> Lesson:
    """Record or update a lesson observation.

    New lessons start at Tier 1. Occurrences are incremented on each call.
    Outcome (0.0–1.0) updates success/failure counters.

    Args:
        lesson_id:         Unique key for this lesson (e.g. "model_routing_CODING").
        content:           Human-readable description.
        outcome:           1.0 = success, 0.0 = failure, None = unknown.
        specificity_score: How actionable the lesson is (0.0–1.0).
        material_savings:  Estimated work reduction (0.0–1.0).
        metadata:          Optional extra context dict.
        memory_path:       Path to memory_tiers.json.

    Returns:
        The updated Lesson object.
    """
    lessons = _load_lessons(memory_path)

    if lesson_id in lessons:
        lesson = lessons[lesson_id]
        lesson.occurrences += 1
        lesson.content = content  # Update to latest phrasing
    else:
        lesson = Lesson(
            id=lesson_id,
            content=content,
            tier=1,
            specificity_score=specificity_score,
            material_savings=material_savings,
            metadata=metadata or {},
        )

    # Record outcome
    if outcome is not None:
        if outcome >= 0.5:
            lesson.successes += 1
        else:
            lesson.failures += 1

    lesson.specificity_score = max(lesson.specificity_score, specificity_score)
    lesson.material_savings = max(lesson.material_savings, material_savings)
    if metadata:
        lesson.metadata.update(metadata)

    lesson.touch()
    lessons[lesson_id] = lesson
    _save_lessons(lessons, memory_path)
    return lesson


def contradict_lesson(
    lesson_id: str,
    memory_path: str = DEFAULT_MEMORY_PATH,
) -> Optional[Lesson]:
    """Mark a lesson as contradicted, demoting it one tier.

    Args:
        lesson_id:   ID of the lesson to contradict.
        memory_path: Path to memory_tiers.json.

    Returns:
        Updated lesson, or None if not found.
    """
    lessons = _load_lessons(memory_path)
    if lesson_id not in lessons:
        return None

    lesson = lessons[lesson_id]
    lesson.contradictions += 1
    lesson.last_contradicted = _now_iso()
    lesson.failures += 1

    # Demote one tier
    if lesson.tier > 1:
        lesson.tier -= 1

    lesson.touch()
    lessons[lesson_id] = lesson
    _save_lessons(lessons, memory_path)
    return lesson


def invalidate_lesson(
    lesson_id: str,
    memory_path: str = DEFAULT_MEMORY_PATH,
) -> bool:
    """Remove a lesson from all tiers (hard delete on failed validation).

    Args:
        lesson_id:   ID of the lesson to remove.
        memory_path: Path to memory_tiers.json.

    Returns:
        True if found and removed, False if not found.
    """
    lessons = _load_lessons(memory_path)
    if lesson_id not in lessons:
        return False
    del lessons[lesson_id]
    _save_lessons(lessons, memory_path)
    return True


def try_promote(
    lesson_id: str,
    memory_path: str = DEFAULT_MEMORY_PATH,
) -> tuple[bool, str]:
    """Attempt to promote a single lesson to the next tier.

    Args:
        lesson_id:   ID of the lesson to promote.
        memory_path: Path to memory_tiers.json.

    Returns:
        (promoted: bool, reason: str)
    """
    lessons = _load_lessons(memory_path)
    if lesson_id not in lessons:
        return False, "lesson not found"

    lesson = lessons[lesson_id]
    if lesson.tier >= 4:
        return False, "already at max tier (Durable)"

    gate = TIER_GATES.get(lesson.tier, {})
    passed, fail_reasons = _check_gate(lesson, gate)

    if not passed:
        return False, f"gate failed: {'; '.join(fail_reasons)}"

    lesson.tier += 1
    lesson.last_promoted = _now_iso()
    lesson.touch()
    lessons[lesson_id] = lesson
    _save_lessons(lessons, memory_path)
    return True, f"promoted to Tier {lesson.tier}"


def promote_all(
    memory_path: str = DEFAULT_MEMORY_PATH,
) -> Dict[str, str]:
    """Run promotion + demotion sweep across all tracked lessons.

    - Expired lessons are demoted one tier (or removed at Tier 1)
    - Unused lessons are demoted one tier
    - Promotable lessons advance one tier

    Returns:
        Dict of {lesson_id: action_taken} for all modified lessons.
    """
    lessons = _load_lessons(memory_path)
    results: Dict[str, str] = {}

    for lid, lesson in list(lessons.items()):

        # --- Expiry check ---
        if _is_expired(lesson):
            if lesson.tier <= 1:
                del lessons[lid]
                results[lid] = "removed (expired at Tier 1)"
                continue
            lesson.tier -= 1
            lesson.touch()
            results[lid] = f"demoted to Tier {lesson.tier} (expired)"
            continue

        # --- Inactivity demotion ---
        if _is_unused_too_long(lesson):
            lesson.tier -= 1
            lesson.touch()
            results[lid] = f"demoted to Tier {lesson.tier} (unused {UNUSED_DEMOTION_DAYS}d)"
            continue

        # --- Promotion attempt ---
        if lesson.tier < 4:
            gate = TIER_GATES.get(lesson.tier, {})
            passed, fail_reasons = _check_gate(lesson, gate)
            if passed:
                lesson.tier += 1
                lesson.last_promoted = _now_iso()
                lesson.touch()
                results[lid] = f"promoted to Tier {lesson.tier}"

    _save_lessons(lessons, memory_path)
    return results


def get_lessons_by_tier(
    tier: int,
    memory_path: str = DEFAULT_MEMORY_PATH,
) -> List[Lesson]:
    """Return all lessons currently in a given tier.

    Args:
        tier:        Tier number (1–4).
        memory_path: Path to memory_tiers.json.

    Returns:
        List of Lesson objects.
    """
    lessons = _load_lessons(memory_path)
    result = [l for l in lessons.values() if l.tier == tier]
    # Touch lessons as they're accessed
    for lesson in result:
        lesson.touch()
    if result:
        _save_lessons(lessons, memory_path)
    return result


def get_durable_lessons(
    memory_path: str = DEFAULT_MEMORY_PATH,
) -> List[Lesson]:
    """Return all Tier 4 (Durable) lessons.

    These are the highest-quality, permanent lessons.

    Args:
        memory_path: Path to memory_tiers.json.

    Returns:
        List of Lesson objects.
    """
    return get_lessons_by_tier(4, memory_path)


def get_lesson(
    lesson_id: str,
    memory_path: str = DEFAULT_MEMORY_PATH,
) -> Optional[Lesson]:
    """Retrieve a single lesson by ID, touching its access time.

    Args:
        lesson_id:   Lesson ID.
        memory_path: Path to memory_tiers.json.

    Returns:
        Lesson or None.
    """
    lessons = _load_lessons(memory_path)
    if lesson_id not in lessons:
        return None
    lesson = lessons[lesson_id]
    lesson.touch()
    lessons[lesson_id] = lesson
    _save_lessons(lessons, memory_path)
    return lesson


def summarize(
    memory_path: str = DEFAULT_MEMORY_PATH,
) -> dict:
    """Return a summary of all tiers.

    Args:
        memory_path: Path to memory_tiers.json.

    Returns:
        Dict with tier counts and lesson list.
    """
    lessons = _load_lessons(memory_path)
    by_tier: Dict[int, List[str]] = {1: [], 2: [], 3: [], 4: []}
    for lid, lesson in lessons.items():
        tier = max(1, min(4, lesson.tier))
        by_tier[tier].append(lid)

    return {
        "total": len(lessons),
        "tiers": {
            f"tier_{t}": {
                "name": ["working", "session", "project", "durable"][t - 1],
                "count": len(ids),
                "lessons": ids,
            }
            for t, ids in by_tier.items()
        },
    }
