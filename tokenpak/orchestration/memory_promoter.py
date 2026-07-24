"""TokenPak Memory Promotion Rules — manages memory tier progression.

Implements strict gates for promoting learned knowledge:
  Tier 1 → Tier 2 → Tier 3 → Tier 4

Principle: Only promote if:
  - Happened 2+ times (min_occurrences)
  - >70% success rate (min_success_rate)
  - Not contradicted in 7 days (not_contradicted_days)
  - Saves >15% future work (material_savings)
  - Specific enough to act on (specificity_score >= 0.5)
"""

from __future__ import annotations

__all__ = (
    "DEFAULT_TTL",
    "Lesson",
    "MemoryPromoter",
    "PROMOTION_RULES",
    "TIER_NAMES",
)


import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Optional

logger = logging.getLogger(__name__)

# Promotion gate thresholds
PROMOTION_RULES = {
    "min_occurrences": 2,  # happened more than once
    "min_success_rate": 0.7,  # validated by outcome
    "not_contradicted_days": 7,  # not contradicted in last 7 days
    "material_savings": 0.15,  # reduces future work by >15% (e.g., 15% token reduction)
    "specificity_score": 0.5,  # specific enough to be actionable (0-1 scale)
}

# Memory tier definitions
TIER_NAMES = {
    1: "working",  # current session, auto-expires
    2: "session",  # persists across turns, TTL: hours
    3: "project",  # persists across sessions, TTL: days
    4: "durable",  # permanent, highest quality gate
}

DEFAULT_TTL = {
    1: 300,  # Tier 1: 5 minutes
    2: 3600 * 4,  # Tier 2: 4 hours
    3: 86400 * 7,  # Tier 3: 7 days
    4: None,  # Tier 4: permanent
}

DEFAULT_PROMOTER_PATH = Path.home() / ".tokenpak" / "memory_promoter.json"
# Backward-compatible name used by the public learning bridge.
DEFAULT_MEMORY_PATH = DEFAULT_PROMOTER_PATH


@dataclass
class Lesson:
    """A learned lesson with promotion tracking."""

    lesson_id: str
    content: str  # The actual lesson text
    tier: int  # 1-4
    occurrences: int  # How many times we've seen this
    successes: int  # How many times it succeeded
    failures: int  # How many times it failed
    contradictions: int  # How many times it was contradicted
    specificity_score: float  # 0-1: actionability
    savings_pct: float  # 0-100: estimated % savings
    created_at: float  # Unix timestamp
    last_seen_at: float  # Unix timestamp
    last_promoted_at: float  # Unix timestamp when promoted to current tier
    promoted_from: Optional[int]  # Previous tier
    metadata: dict[str, object] = field(default_factory=dict)

    def success_rate(self) -> float:
        """Return success rate (0-1)."""
        total = self.occurrences
        return self.successes / total if total > 0 else 0.0

    def days_since_contradicted(self) -> float:
        """Return days since last contradiction, or infinity if never contradicted."""
        if self.contradictions == 0:
            return float("inf")
        # Approximate: last contradiction was about (occurrences - successes - failures) / 2 occurrences ago
        # For simplicity, we'll track the actual timestamp separately in a real implementation
        return 0  # Placeholder—would need actual contradiction timestamp tracking

    def is_expired(self) -> bool:
        """Check if this lesson has exceeded its tier's TTL."""
        ttl = DEFAULT_TTL.get(self.tier)
        if ttl is None:  # Tier 4 never expires
            return False
        age_seconds = time.time() - self.last_seen_at
        return age_seconds > ttl

    def to_dict(self) -> dict[str, object]:
        return {
            "lesson_id": self.lesson_id,
            "content": self.content,
            "tier": self.tier,
            "occurrences": self.occurrences,
            "successes": self.successes,
            "failures": self.failures,
            "contradictions": self.contradictions,
            "specificity_score": self.specificity_score,
            "savings_pct": self.savings_pct,
            "created_at": self.created_at,
            "last_seen_at": self.last_seen_at,
            "last_promoted_at": self.last_promoted_at,
            "promoted_from": self.promoted_from,
            "metadata": self.metadata,
        }


class MemoryPromoter:
    """Manages promotion and demotion of learned lessons."""

    def __init__(self, path: str | Path = DEFAULT_PROMOTER_PATH):
        self.path = Path(path)
        self.lessons: dict[str, Lesson] = {}
        self._load()

    def _load(self) -> None:
        """Load lessons from disk."""
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text())
            for lesson_id, lesson_data in data.get("lessons", {}).items():
                lesson_data["lesson_id"] = lesson_id
                self.lessons[lesson_id] = Lesson(**lesson_data)
        except (json.JSONDecodeError, OSError, TypeError) as e:
            logger.warning(f"Could not load memory promoter: {e}")

    def _save(self) -> None:
        """Save lessons to disk."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "version": 1,
            "updated": datetime.now(timezone.utc).isoformat(),
            "lessons": {lid: lesson.to_dict() for lid, lesson in self.lessons.items()},
        }
        self.path.write_text(json.dumps(data, indent=2))

    def add_lesson(
        self,
        lesson_id: str,
        content: str,
        specificity_score: float = 0.5,
        savings_pct: float = 0.0,
        metadata: Optional[Mapping[str, object]] = None,
    ) -> Lesson:
        """Create a new lesson starting at Tier 1."""
        now = time.time()
        lesson = Lesson(
            lesson_id=lesson_id,
            content=content,
            tier=1,
            occurrences=1,
            successes=0,
            failures=0,
            contradictions=0,
            specificity_score=specificity_score,
            savings_pct=savings_pct,
            created_at=now,
            last_seen_at=now,
            last_promoted_at=now,
            promoted_from=None,
            metadata=dict(metadata or {}),
        )
        self.lessons[lesson_id] = lesson
        self._save()
        return lesson

    def record_success(self, lesson_id: str, dry_run: bool = False) -> Optional[Lesson]:
        """Record a successful application of this lesson."""
        if lesson_id not in self.lessons:
            return None
        lesson = self.lessons[lesson_id]
        lesson.occurrences += 1
        lesson.successes += 1
        lesson.last_seen_at = time.time()
        self._maybe_promote(lesson, dry_run=dry_run)
        if not dry_run:
            self._save()
        return lesson

    def record_failure(self, lesson_id: str) -> Optional[Lesson]:
        """Record a failed application of this lesson."""
        if lesson_id not in self.lessons:
            return None
        lesson = self.lessons[lesson_id]
        lesson.occurrences += 1
        lesson.failures += 1
        lesson.last_seen_at = time.time()
        self._save()
        return lesson

    def record_contradiction(self, lesson_id: str) -> Optional[Lesson]:
        """Record that this lesson was contradicted by new evidence."""
        if lesson_id not in self.lessons:
            return None
        lesson = self.lessons[lesson_id]
        lesson.contradictions += 1
        lesson.last_seen_at = time.time()
        self._maybe_demote(lesson)
        self._save()
        return lesson

    def _maybe_promote(self, lesson: Lesson, dry_run: bool = False) -> bool:
        """Check if lesson should be promoted to next tier."""
        if lesson.tier >= 4:
            return False  # Already at max tier

        # Check promotion gates
        success_rate = lesson.success_rate()

        if lesson.tier == 1 and self._check_tier1_to_2(lesson):
            self._promote(lesson, 2, dry_run=dry_run)
            return True
        elif lesson.tier == 2 and self._check_tier2_to_3(lesson):
            self._promote(lesson, 3, dry_run=dry_run)
            return True
        elif lesson.tier == 3 and self._check_tier3_to_4(lesson):
            self._promote(lesson, 4, dry_run=dry_run)
            return True

        return False

    def _check_tier1_to_2(self, lesson: Lesson) -> bool:
        """Check if lesson can be promoted from Tier 1 to Tier 2."""
        return (
            lesson.occurrences >= PROMOTION_RULES["min_occurrences"]
            and lesson.success_rate() >= PROMOTION_RULES["min_success_rate"]
            and lesson.contradictions == 0
            and lesson.specificity_score >= PROMOTION_RULES["specificity_score"]
        )

    def _check_tier2_to_3(self, lesson: Lesson) -> bool:
        """Check if lesson can be promoted from Tier 2 to Tier 3."""
        return (
            lesson.occurrences >= 5
            and lesson.success_rate() >= 0.7
            and lesson.contradictions == 0
            and lesson.savings_pct >= PROMOTION_RULES["material_savings"] * 100
        )

    def _check_tier3_to_4(self, lesson: Lesson) -> bool:
        """Check if lesson can be promoted from Tier 3 to Tier 4 (durable)."""
        return (
            lesson.occurrences >= 10
            and lesson.success_rate() >= 0.85
            and lesson.contradictions == 0
            and lesson.savings_pct >= PROMOTION_RULES["material_savings"] * 100
            and lesson.specificity_score >= 0.6
        )

    def _maybe_demote(self, lesson: Lesson) -> bool:
        """Check if lesson should be demoted due to contradictions or age."""
        # Demote if contradicted
        if lesson.contradictions > 0:
            self._demote(lesson)
            return True

        # Demote or remove if expired
        if lesson.is_expired():
            self._demote(lesson)
            return True

        return False

    def _promote(self, lesson: Lesson, new_tier: int, dry_run: bool = False) -> None:
        """Promote lesson to a higher tier."""
        old_tier = lesson.tier
        lesson.promoted_from = old_tier
        lesson.tier = new_tier
        lesson.last_promoted_at = time.time()
        if dry_run:
            logger.info(
                f"[DRY-RUN] Would promote lesson {lesson.lesson_id} from Tier {old_tier} to Tier {new_tier}"
            )
        else:
            logger.info(
                f"Promoted lesson {lesson.lesson_id} from Tier {old_tier} to Tier {new_tier}"
            )

    def _demote(self, lesson: Lesson) -> None:
        """Demote lesson to lower tier."""
        if lesson.tier <= 1:
            logger.info(f"Removing lesson {lesson.lesson_id} (failed quality gates)")
            del self.lessons[lesson.lesson_id]
        else:
            old_tier = lesson.tier
            lesson.tier = max(1, lesson.tier - 1)
            lesson.last_promoted_at = time.time()
            logger.info(
                f"Demoted lesson {lesson.lesson_id} from Tier {old_tier} to Tier {lesson.tier}"
            )

    def cleanup_expired(self, dry_run: bool = False) -> int:
        """Remove or demote expired lessons. Returns count of lessons affected."""
        affected = 0
        lesson_ids_to_check = list(self.lessons.keys())
        for lesson_id in lesson_ids_to_check:
            lesson = self.lessons[lesson_id]
            if lesson.is_expired() and self._maybe_demote(lesson):
                affected += 1
        if affected > 0 and not dry_run:
            self._save()
        return affected

    def get_tier_lessons(self, tier: int) -> list[Lesson]:
        """Get all lessons at a specific tier."""
        return [lesson for lesson in self.lessons.values() if lesson.tier == tier]

    def get_lesson(self, lesson_id: str) -> Optional[Lesson]:
        """Get a specific lesson."""
        return self.lessons.get(lesson_id)

    def get_all_lessons(self) -> list[Lesson]:
        """Get all lessons."""
        return list(self.lessons.values())

    def stats(self) -> dict[str, object]:
        """Return statistics about the memory store."""
        by_tier = {i: 0 for i in range(1, 5)}
        total_lessons = len(self.lessons)
        for lesson in self.lessons.values():
            by_tier[lesson.tier] += 1

        return {
            "total_lessons": total_lessons,
            "by_tier": by_tier,
            "tier_names": TIER_NAMES,
        }


def record_lesson(
    lesson_id: str,
    content: str,
    outcome: Optional[float] = None,
    specificity_score: float = 0.5,
    material_savings: float = 0.0,
    metadata: Optional[Mapping[str, object]] = None,
    memory_path: str | Path = DEFAULT_MEMORY_PATH,
) -> Lesson:
    """Create or update one lesson through the stable module-level bridge."""
    promoter = MemoryPromoter(path=memory_path)
    lesson = promoter.get_lesson(lesson_id)
    if lesson is None:
        lesson = promoter.add_lesson(
            lesson_id=lesson_id,
            content=content,
            specificity_score=specificity_score,
            savings_pct=material_savings * 100,
            metadata=metadata,
        )
    if outcome is None:
        return lesson
    updated = (
        promoter.record_success(lesson_id) if outcome >= 0.5 else promoter.record_failure(lesson_id)
    )
    return updated if updated is not None else lesson


def promote_all(
    memory_path: str | Path = DEFAULT_MEMORY_PATH,
) -> dict[str, str]:
    """Reconcile expiry and one-step promotion for every stored lesson."""
    promoter = MemoryPromoter(path=memory_path)
    before = {lesson.lesson_id: lesson.tier for lesson in promoter.get_all_lessons()}
    expired = {lesson.lesson_id for lesson in promoter.get_all_lessons() if lesson.is_expired()}
    promoter.cleanup_expired()

    promoted = False
    for lesson in promoter.get_all_lessons():
        if lesson.lesson_id in expired:
            continue
        promoted = promoter._maybe_promote(lesson) or promoted
    if promoted:
        promoter._save()

    after = {lesson.lesson_id: lesson.tier for lesson in promoter.get_all_lessons()}
    actions: dict[str, str] = {}
    for lesson_id, old_tier in before.items():
        new_tier = after.get(lesson_id)
        if new_tier is None:
            actions[lesson_id] = "removed"
        elif new_tier > old_tier:
            actions[lesson_id] = f"promoted_to_tier_{new_tier}"
        elif new_tier < old_tier:
            actions[lesson_id] = f"demoted_to_tier_{new_tier}"
    return actions


def get_durable_lessons(
    memory_path: str | Path = DEFAULT_MEMORY_PATH,
) -> list[Lesson]:
    """Return all durable (Tier 4) lessons from the selected store."""
    return MemoryPromoter(path=memory_path).get_tier_lessons(4)
