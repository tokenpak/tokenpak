"""Tests for MemoryPromoter — memory tier promotion system."""

from __future__ import annotations


import pytest
pytest.importorskip("tokenpak.agentic.memory_promoter", reason="module not available in current build")
import json
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from tokenpak.agentic.memory_promoter import (
    Lesson,
    MemoryPromoter,
    PROMOTION_RULES,
    TIER_NAMES,
)


@pytest.fixture
def temp_memory_file(tmp_path):
    """Fixture for a temporary memory file."""
    return tmp_path / "test_memory.json"


@pytest.fixture
def promoter(temp_memory_file):
    """Fixture for a MemoryPromoter instance."""
    return MemoryPromoter(path=temp_memory_file)


# ---------------------------------------------------------------------------
# Test: Lesson Creation and Tier 1
# ---------------------------------------------------------------------------


def test_add_lesson_starts_at_tier1(promoter):
    """New lesson should start at Tier 1."""
    lesson = promoter.add_lesson(
        lesson_id="test-1",
        content="Always use the fast path",
        specificity_score=0.7,
        savings_pct=20.0,
    )
    assert lesson.tier == 1
    assert lesson.occurrences == 1
    assert lesson.successes == 0
    assert lesson.created_at == lesson.last_promoted_at


def test_lesson_success_rate_calculation():
    """Success rate should be successes / occurrences."""
    lesson = Lesson(
        lesson_id="test",
        content="test",
        tier=1,
        occurrences=10,
        successes=7,
        failures=3,
        contradictions=0,
        specificity_score=0.5,
        savings_pct=10.0,
        created_at=time.time(),
        last_seen_at=time.time(),
        last_promoted_at=time.time(),
        promoted_from=None,
    )
    assert lesson.success_rate() == pytest.approx(0.7, abs=0.01)


def test_record_success_increments_counters(promoter):
    """Recording success should increment occurrences and successes."""
    lesson = promoter.add_lesson("test-1", "Fast path", specificity_score=0.7, savings_pct=20.0)
    initial_occurrences = lesson.occurrences
    
    lesson = promoter.record_success("test-1")
    assert lesson.occurrences == initial_occurrences + 1
    assert lesson.successes == 1


def test_record_failure_increments_counters(promoter):
    """Recording failure should increment occurrences and failures."""
    lesson = promoter.add_lesson("test-1", "Fast path", specificity_score=0.7, savings_pct=20.0)
    
    lesson = promoter.record_failure("test-1")
    assert lesson.occurrences == 2
    assert lesson.failures == 1


# ---------------------------------------------------------------------------
# Test: Promotion Tier 1 → Tier 2
# ---------------------------------------------------------------------------


def test_promote_tier1_to_tier2(promoter):
    """Lesson should promote to Tier 2 after meeting criteria."""
    lesson = promoter.add_lesson(
        lesson_id="test-1",
        content="Fast path works",
        specificity_score=0.7,
        savings_pct=20.0,
    )
    assert lesson.tier == 1
    
    # Record 1 more success: 2 occurrences, 1 success (50%) - not enough yet
    promoter.record_success("test-1")
    lesson = promoter.get_lesson("test-1")
    assert lesson.tier == 1  # Still Tier 1 (50% < 70%)
    
    # Record another success: 3 occurrences, 2 successes (66%) - still not enough
    promoter.record_success("test-1")
    lesson = promoter.get_lesson("test-1")
    assert lesson.tier == 1  # Still Tier 1 (66% < 70%)
    
    # Record another success: 4 occurrences, 3 successes (75%) - now should promote!
    promoter.record_success("test-1")
    lesson = promoter.get_lesson("test-1")
    
    assert lesson.tier == 2
    assert lesson.occurrences >= PROMOTION_RULES["min_occurrences"]
    assert lesson.success_rate() >= PROMOTION_RULES["min_success_rate"]


def test_no_promote_tier1_insufficient_occurrences(promoter):
    """Lesson should not promote without min occurrences."""
    lesson = promoter.add_lesson(
        lesson_id="test-1",
        content="Fast path",
        specificity_score=0.7,
        savings_pct=20.0,
    )
    # Only 1 occurrence, min is 2
    assert lesson.tier == 1


def test_no_promote_tier1_low_success_rate(promoter):
    """Lesson should not promote with low success rate."""
    lesson = promoter.add_lesson(
        lesson_id="test-1",
        content="Risky path",
        specificity_score=0.7,
        savings_pct=10.0,
    )
    
    # Record 1 failure (total: 2 occurrences, 0 successes = 0% success rate)
    promoter.record_failure("test-1")
    lesson = promoter.get_lesson("test-1")
    
    assert lesson.tier == 1  # Should not promote


def test_no_promote_tier1_low_specificity(promoter):
    """Lesson should not promote with low specificity score."""
    lesson = promoter.add_lesson(
        lesson_id="test-1",
        content="Vague idea",
        specificity_score=0.2,  # Below threshold of 0.5
        savings_pct=20.0,
    )
    
    # Even with 2 successful occurrences, should not promote
    promoter.record_success("test-1")
    lesson = promoter.get_lesson("test-1")
    
    assert lesson.tier == 1


# ---------------------------------------------------------------------------
# Test: Demotion and Contradiction
# ---------------------------------------------------------------------------


def test_demote_on_contradiction(promoter):
    """Lesson should demote when contradicted."""
    lesson = promoter.add_lesson(
        lesson_id="test-1",
        content="Fast path",
        specificity_score=0.8,
        savings_pct=20.0,
    )
    
    # Get to Tier 2: need 2+ occurrences and 70%+ success rate
    promoter.record_success("test-1")  # 2 occ, 1 succ = 50%
    promoter.record_success("test-1")  # 3 occ, 2 succ = 66%
    promoter.record_success("test-1")  # 4 occ, 3 succ = 75% -> promotes to Tier 2
    
    lesson = promoter.get_lesson("test-1")
    assert lesson.tier == 2
    
    # Record contradiction
    promoter.record_contradiction("test-1")
    lesson = promoter.get_lesson("test-1")
    
    # Should be demoted back to Tier 1
    assert lesson.tier == 1


def test_no_promote_with_contradiction(promoter):
    """Lesson with contradictions should be removed (Tier 1) or demoted (higher tiers)."""
    lesson = promoter.add_lesson(
        lesson_id="test-1",
        content="Questionable approach",
        specificity_score=0.8,
        savings_pct=20.0,
    )
    
    # Record success + contradiction
    promoter.record_success("test-1")
    promoter.record_contradiction("test-1")
    lesson = promoter.get_lesson("test-1")
    
    # Tier 1 with contradiction should be deleted
    assert lesson is None
    
    # Now test with a promoted lesson that gets contradicted
    lesson2 = promoter.add_lesson(
        lesson_id="test-2",
        content="Risky pattern",
        specificity_score=0.8,
        savings_pct=20.0,
    )
    # Promote to Tier 2: 75% success rate
    promoter.record_success("test-2")  # 50%
    promoter.record_success("test-2")  # 66%
    promoter.record_success("test-2")  # 75% -> promotes
    assert promoter.get_lesson("test-2").tier == 2
    
    # Now contradict it - should demote to Tier 1
    promoter.record_contradiction("test-2")
    lesson2 = promoter.get_lesson("test-2")
    assert lesson2 is not None
    assert lesson2.tier == 1
    assert lesson2.contradictions == 1


# ---------------------------------------------------------------------------
# Test: Expiry and Cleanup
# ---------------------------------------------------------------------------


def test_is_expired_tier1(promoter):
    """Tier 1 lesson should expire after 5 minutes (300s)."""
    lesson = Lesson(
        lesson_id="expired",
        content="Old lesson",
        tier=1,
        occurrences=1,
        successes=0,
        failures=0,
        contradictions=0,
        specificity_score=0.5,
        savings_pct=10.0,
        created_at=time.time(),
        last_seen_at=time.time() - 400,  # 400 seconds ago
        last_promoted_at=time.time(),
        promoted_from=None,
    )
    assert lesson.is_expired() is True


def test_is_not_expired_tier4(promoter):
    """Tier 4 lessons should never expire."""
    lesson = Lesson(
        lesson_id="durable",
        content="Durable lesson",
        tier=4,
        occurrences=20,
        successes=20,
        failures=0,
        contradictions=0,
        specificity_score=0.8,
        savings_pct=30.0,
        created_at=time.time() - 86400 * 365,  # 1 year ago
        last_seen_at=time.time() - 86400 * 365,
        last_promoted_at=time.time() - 86400 * 365,
        promoted_from=3,
    )
    assert lesson.is_expired() is False


def test_cleanup_expired_lessons(promoter):
    """cleanup_expired should remove or demote expired lessons."""
    # Create a lesson
    lesson = promoter.add_lesson(
        lesson_id="test-1",
        content="Old lesson",
        specificity_score=0.7,
        savings_pct=20.0,
    )
    
    # Make it expired by setting last_seen far in the past
    promoter.lessons["test-1"].last_seen_at = time.time() - 400  # 400s ago, expired for Tier 1 (5 min TTL)
    
    # Run cleanup
    affected = promoter.cleanup_expired()
    
    # Should be removed (since Tier 1 demotes to 0, which deletes)
    assert "test-1" not in promoter.lessons or promoter.get_lesson("test-1") is None
    assert affected >= 1


# ---------------------------------------------------------------------------
# Test: Persistence
# ---------------------------------------------------------------------------


def test_save_and_load(promoter, temp_memory_file):
    """Lessons should persist to disk and reload."""
    lesson1 = promoter.add_lesson("test-1", "Lesson 1", specificity_score=0.7, savings_pct=20.0)
    # Get to Tier 2: 4 occurrences, 3 successes (75%)
    promoter.record_success("test-1")  # 2, 1 = 50%
    promoter.record_success("test-1")  # 3, 2 = 66%
    promoter.record_success("test-1")  # 4, 3 = 75% -> promotes
    
    lesson2 = promoter.add_lesson("test-2", "Lesson 2", specificity_score=0.6, savings_pct=15.0)
    
    # Create new promoter instance from same file
    promoter2 = MemoryPromoter(path=temp_memory_file)
    
    assert len(promoter2.lessons) == 2
    assert promoter2.get_lesson("test-1").tier == 2
    assert promoter2.get_lesson("test-2").tier == 1


# ---------------------------------------------------------------------------
# Test: Stats and Reporting
# ---------------------------------------------------------------------------


def test_get_tier_lessons(promoter):
    """get_tier_lessons should return lessons at specific tier."""
    promoter.add_lesson("tier1a", "Lesson", specificity_score=0.7, savings_pct=20.0)
    promoter.add_lesson("tier1b", "Lesson", specificity_score=0.7, savings_pct=20.0)
    
    # Promote tier1a to Tier 2: need 75%+ success
    promoter.record_success("tier1a")  # 2 occ, 1 succ = 50%
    promoter.record_success("tier1a")  # 3 occ, 2 succ = 66%
    promoter.record_success("tier1a")  # 4 occ, 3 succ = 75% -> promotes
    
    tier1 = promoter.get_tier_lessons(1)
    tier2 = promoter.get_tier_lessons(2)
    
    assert len(tier1) == 1
    assert len(tier2) == 1
    assert tier1[0].lesson_id == "tier1b"
    assert tier2[0].lesson_id == "tier1a"


def test_stats(promoter):
    """stats() should return accurate counts."""
    promoter.add_lesson("test-1", "L1", specificity_score=0.7, savings_pct=20.0)
    promoter.add_lesson("test-2", "L2", specificity_score=0.7, savings_pct=20.0)
    
    # Promote test-1 to Tier 2: need 75%+ success
    promoter.record_success("test-1")  # 2 occ, 1 succ = 50%
    promoter.record_success("test-1")  # 3 occ, 2 succ = 66%
    promoter.record_success("test-1")  # 4 occ, 3 succ = 75% -> promotes
    
    stats = promoter.stats()
    
    assert stats["total_lessons"] == 2
    assert stats["by_tier"][1] == 1
    assert stats["by_tier"][2] == 1
    assert stats["by_tier"][3] == 0
    assert stats["by_tier"][4] == 0


# ---------------------------------------------------------------------------
# Test: Integration with Learning System (Future)
# ---------------------------------------------------------------------------


def test_lesson_to_dict(promoter):
    """Lesson.to_dict() should serialize correctly."""
    lesson = promoter.add_lesson("test", "content", specificity_score=0.7, savings_pct=20.0)
    data = lesson.to_dict()
    
    assert data["lesson_id"] == "test"
    assert data["content"] == "content"
    assert data["tier"] == 1
    assert "created_at" in data
