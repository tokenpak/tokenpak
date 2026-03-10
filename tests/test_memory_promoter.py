"""Tests for tokenpak.agent.agentic.memory_promoter.

Covers:
  1. Promotion gates enforced correctly (gate failures block promotion)
  2. New lesson starts at Tier 1
  3. Sufficient evidence promotes to higher tier
  4. Contradicted lesson is demoted
  5. Expired lesson is cleaned up by promote_all()
  6. Unused lesson is demoted by promote_all()
  7. promote_all() raises a lesson that passes all gates
  8. invalidate_lesson() removes from all tiers
"""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from tokenpak.agent.agentic.memory_promoter import (
    TIER_GATES,
    TIER_TTL,
    Lesson,
    contradict_lesson,
    get_durable_lessons,
    get_lesson,
    get_lessons_by_tier,
    invalidate_lesson,
    promote_all,
    record_lesson,
    summarize,
    try_promote,
    _now_iso,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _tmp_path() -> str:
    """Return a fresh temp file path that doesn't exist yet."""
    f = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
    f.close()
    Path(f.name).unlink()
    return f.name


def _lesson_at_tier(lid: str, tier: int, mp: str) -> Lesson:
    """Create a lesson that already satisfies gates up to `tier`."""
    lesson = record_lesson(
        lesson_id=lid,
        content=f"Lesson {lid} content",
        memory_path=mp,
    )
    # Force the lesson directly to the desired tier by patching the JSON
    p = Path(mp)
    store = json.loads(p.read_text())
    store[lid]["tier"] = tier
    store[lid]["occurrences"] = 20
    store[lid]["successes"] = 18
    store[lid]["failures"] = 2
    store[lid]["specificity_score"] = 0.9
    store[lid]["material_savings"] = 0.5
    p.write_text(json.dumps(store, indent=2))
    return record_lesson(lid, f"Lesson {lid} content", memory_path=mp)


# ---------------------------------------------------------------------------
# Test 1: New lesson starts at Tier 1
# ---------------------------------------------------------------------------

def test_new_lesson_starts_at_tier_1():
    mp = _tmp_path()
    lesson = record_lesson("lesson_new", "A brand new lesson", memory_path=mp)
    assert lesson.tier == 1, f"Expected tier=1, got {lesson.tier}"
    assert lesson.occurrences == 1


# ---------------------------------------------------------------------------
# Test 2: Promotion gates enforced — insufficient occurrences blocked
# ---------------------------------------------------------------------------

def test_promotion_gate_enforces_min_occurrences():
    mp = _tmp_path()
    # Record exactly 1 occurrence → should not meet Tier 1→2 gate (requires 2+)
    lesson = record_lesson("gate_test", "Test gate enforcement", memory_path=mp)
    assert lesson.occurrences == 1

    promoted, reason = try_promote("gate_test", memory_path=mp)
    assert not promoted, "Should NOT have promoted with only 1 occurrence"
    assert "occurrences" in reason


def test_promotion_gate_enforces_success_rate():
    mp = _tmp_path()
    # Create a lesson with enough occurrences but bad success rate
    for _ in range(5):
        record_lesson("sr_test", "Low success rate lesson", outcome=0.0, memory_path=mp)

    p = Path(mp)
    store = json.loads(p.read_text())
    store["sr_test"]["tier"] = 2          # Try to promote Tier 2 → 3
    store["sr_test"]["occurrences"] = 10
    store["sr_test"]["successes"] = 5     # 50% success rate
    store["sr_test"]["failures"] = 5
    store["sr_test"]["specificity_score"] = 0.9
    store["sr_test"]["material_savings"] = 0.5
    p.write_text(json.dumps(store))

    promoted, reason = try_promote("sr_test", memory_path=mp)
    assert not promoted, "Should NOT promote with 50% success rate to Tier 3 (needs 85%)"
    assert "success_rate" in reason


def test_promotion_gate_enforces_specificity():
    mp = _tmp_path()
    record_lesson("spec_test", "Vague lesson", memory_path=mp)

    p = Path(mp)
    store = json.loads(p.read_text())
    store["spec_test"]["tier"] = 2
    store["spec_test"]["occurrences"] = 10
    store["spec_test"]["successes"] = 9
    store["spec_test"]["specificity_score"] = 0.1   # too vague
    store["spec_test"]["material_savings"] = 0.5
    p.write_text(json.dumps(store))

    promoted, reason = try_promote("spec_test", memory_path=mp)
    assert not promoted, "Should NOT promote with low specificity"
    assert "specificity_score" in reason


# ---------------------------------------------------------------------------
# Test 3: Sufficient evidence promotes to higher tier
# ---------------------------------------------------------------------------

def test_sufficient_evidence_promotes_tier1_to_tier2():
    mp = _tmp_path()
    # Add 2+ occurrences → should pass Tier 1 gate
    record_lesson("promote_test", "Promote me", outcome=1.0, memory_path=mp)
    record_lesson("promote_test", "Promote me", outcome=1.0, memory_path=mp)

    lesson = get_lesson("promote_test", memory_path=mp)
    assert lesson.occurrences == 2

    promoted, reason = try_promote("promote_test", memory_path=mp)
    assert promoted, f"Should have promoted to Tier 2; reason: {reason}"

    lesson = get_lesson("promote_test", memory_path=mp)
    assert lesson.tier == 2


def test_sufficient_evidence_promotes_tier2_to_tier3():
    mp = _tmp_path()
    lesson = record_lesson("promo_t3", "Promote to tier 3", memory_path=mp)

    # Manually craft a lesson that passes Tier 2 → 3 gates
    p = Path(mp)
    store = json.loads(p.read_text())
    store["promo_t3"]["tier"] = 2
    store["promo_t3"]["occurrences"] = 5
    store["promo_t3"]["successes"] = 4
    store["promo_t3"]["failures"] = 1   # 80% success
    store["promo_t3"]["specificity_score"] = 0.8
    store["promo_t3"]["material_savings"] = 0.3
    p.write_text(json.dumps(store))

    promoted, reason = try_promote("promo_t3", memory_path=mp)
    assert promoted, f"Should have promoted to Tier 3; reason: {reason}"
    lesson = get_lesson("promo_t3", memory_path=mp)
    assert lesson.tier == 3


def test_sufficient_evidence_promotes_tier3_to_tier4():
    mp = _tmp_path()
    record_lesson("durable_test", "Durable lesson candidate", memory_path=mp)

    p = Path(mp)
    store = json.loads(p.read_text())
    store["durable_test"]["tier"] = 3
    store["durable_test"]["occurrences"] = 10
    store["durable_test"]["successes"] = 9
    store["durable_test"]["failures"] = 1   # 90% success
    store["durable_test"]["specificity_score"] = 0.9
    store["durable_test"]["material_savings"] = 0.4
    store["durable_test"]["last_contradicted"] = None
    p.write_text(json.dumps(store))

    promoted, reason = try_promote("durable_test", memory_path=mp)
    assert promoted, f"Should have promoted to Tier 4; reason: {reason}"
    lesson = get_lesson("durable_test", memory_path=mp)
    assert lesson.tier == 4

    # Check it appears in durable lessons
    durable = get_durable_lessons(memory_path=mp)
    assert any(l.id == "durable_test" for l in durable)


# ---------------------------------------------------------------------------
# Test 4: Contradicted lesson is demoted
# ---------------------------------------------------------------------------

def test_contradicted_lesson_demoted():
    mp = _tmp_path()
    _lesson_at_tier("contradict_me", 3, mp)

    lesson = get_lesson("contradict_me", memory_path=mp)
    assert lesson.tier == 3

    updated = contradict_lesson("contradict_me", memory_path=mp)
    assert updated is not None
    assert updated.tier == 2, f"Expected tier=2 after contradiction, got {updated.tier}"
    assert updated.contradictions == 1
    assert updated.last_contradicted is not None


def test_multiple_contradictions_demote_further():
    mp = _tmp_path()
    _lesson_at_tier("multi_contradict", 4, mp)

    contradict_lesson("multi_contradict", memory_path=mp)
    l = get_lesson("multi_contradict", memory_path=mp)
    assert l.tier == 3

    contradict_lesson("multi_contradict", memory_path=mp)
    l = get_lesson("multi_contradict", memory_path=mp)
    assert l.tier == 2


# ---------------------------------------------------------------------------
# Test 5: Expired lesson cleaned up
# ---------------------------------------------------------------------------

def test_expired_tier1_lesson_removed_by_sweep():
    mp = _tmp_path()
    record_lesson("expire_me", "Expiring soon", memory_path=mp)

    # Backdate creation to exceed Tier 1 TTL (4 hours)
    p = Path(mp)
    store = json.loads(p.read_text())
    old_time = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    store["expire_me"]["created"] = old_time
    store["expire_me"]["tier"] = 1
    p.write_text(json.dumps(store))

    results = promote_all(memory_path=mp)
    assert "expire_me" in results
    assert "removed" in results["expire_me"] or "expired" in results["expire_me"]

    lesson = get_lesson("expire_me", memory_path=mp)
    assert lesson is None, "Tier 1 expired lesson should have been removed"


def test_expired_tier2_lesson_demoted():
    mp = _tmp_path()
    _lesson_at_tier("expire_t2", 2, mp)

    p = Path(mp)
    store = json.loads(p.read_text())
    old_time = (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()
    store["expire_t2"]["created"] = old_time
    store["expire_t2"]["tier"] = 2
    p.write_text(json.dumps(store))

    results = promote_all(memory_path=mp)
    assert "expire_t2" in results
    assert "demoted" in results["expire_t2"]

    lesson = get_lesson("expire_t2", memory_path=mp)
    assert lesson is not None
    assert lesson.tier == 1


# ---------------------------------------------------------------------------
# Test 6: Unused lesson is demoted
# ---------------------------------------------------------------------------

def test_unused_lesson_demoted_after_30_days():
    mp = _tmp_path()
    _lesson_at_tier("unused_test", 3, mp)

    p = Path(mp)
    store = json.loads(p.read_text())
    old_access = (datetime.now(timezone.utc) - timedelta(days=35)).isoformat()
    store["unused_test"]["last_accessed"] = old_access
    store["unused_test"]["tier"] = 3
    # Also reset created so it's not expired
    store["unused_test"]["created"] = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
    p.write_text(json.dumps(store))

    results = promote_all(memory_path=mp)
    assert "unused_test" in results
    assert "unused" in results["unused_test"]

    lesson = get_lesson("unused_test", memory_path=mp)
    assert lesson.tier == 2


# ---------------------------------------------------------------------------
# Test 7: promote_all() advances a qualifying lesson
# ---------------------------------------------------------------------------

def test_promote_all_advances_ready_lesson():
    mp = _tmp_path()
    # Lesson ready for Tier 1 → 2 (2+ occurrences)
    record_lesson("ready_promo", "Ready to promote", outcome=1.0, memory_path=mp)
    record_lesson("ready_promo", "Ready to promote", outcome=1.0, memory_path=mp)

    results = promote_all(memory_path=mp)
    assert "ready_promo" in results
    assert "promoted" in results["ready_promo"]

    lesson = get_lesson("ready_promo", memory_path=mp)
    assert lesson.tier == 2


# ---------------------------------------------------------------------------
# Test 8: invalidate_lesson() removes from all tiers
# ---------------------------------------------------------------------------

def test_invalidate_lesson_removes_it():
    mp = _tmp_path()
    _lesson_at_tier("invalidate_me", 4, mp)

    removed = invalidate_lesson("invalidate_me", memory_path=mp)
    assert removed is True

    lesson = get_lesson("invalidate_me", memory_path=mp)
    assert lesson is None


def test_invalidate_nonexistent_lesson():
    mp = _tmp_path()
    removed = invalidate_lesson("does_not_exist", memory_path=mp)
    assert removed is False


# ---------------------------------------------------------------------------
# Test: summarize() reports correct tier distribution
# ---------------------------------------------------------------------------

def test_summarize_tier_distribution():
    mp = _tmp_path()

    # Create lessons at different tiers
    _lesson_at_tier("s_t1a", 1, mp)
    _lesson_at_tier("s_t1b", 1, mp)
    _lesson_at_tier("s_t3", 3, mp)

    summary = summarize(memory_path=mp)
    assert summary["total"] == 3
    assert summary["tiers"]["tier_1"]["count"] == 2
    assert summary["tiers"]["tier_3"]["count"] == 1
    assert summary["tiers"]["tier_4"]["count"] == 0


# ---------------------------------------------------------------------------
# Test: get_lessons_by_tier() returns correct subset
# ---------------------------------------------------------------------------

def test_get_lessons_by_tier():
    mp = _tmp_path()
    _lesson_at_tier("by_tier_t2a", 2, mp)
    _lesson_at_tier("by_tier_t2b", 2, mp)
    _lesson_at_tier("by_tier_t3", 3, mp)

    tier2 = get_lessons_by_tier(2, memory_path=mp)
    ids = {l.id for l in tier2}
    assert "by_tier_t2a" in ids
    assert "by_tier_t2b" in ids
    assert "by_tier_t3" not in ids


# ---------------------------------------------------------------------------
# Test: tier 4 has no TTL expiry
# ---------------------------------------------------------------------------

def test_tier4_does_not_expire():
    mp = _tmp_path()
    _lesson_at_tier("durable_no_expire", 4, mp)

    p = Path(mp)
    store = json.loads(p.read_text())
    old_time = (datetime.now(timezone.utc) - timedelta(days=365)).isoformat()
    store["durable_no_expire"]["created"] = old_time
    store["durable_no_expire"]["tier"] = 4
    # Reset last_accessed to avoid unused demotion
    store["durable_no_expire"]["last_accessed"] = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).isoformat()
    p.write_text(json.dumps(store))

    results = promote_all(memory_path=mp)
    # Should NOT be demoted for expiry (no TTL at Tier 4)
    if "durable_no_expire" in results:
        assert "expired" not in results["durable_no_expire"]

    lesson = get_lesson("durable_no_expire", memory_path=mp)
    assert lesson is not None
    assert lesson.tier == 4
