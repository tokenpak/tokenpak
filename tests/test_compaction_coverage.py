"""
Tests for TokenPak compaction module coverage gaps.

Task: TPK-COV-COMPACT-001
Target: Additional coverage for uncovered paths in:
  - policy.py: TopicAwarePolicy serialization, per_topic_limits, compact_block_with_topics
  - topic_aware.py: TopicSegment properties, edge cases, skipped content merging
  - modes.py: edge cases and enum coverage

Covers at least 3 functions/classes from each of the 3 compaction modules.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "tokenpak.compaction.topic_aware", reason="module not available in current build"
)
import unittest

from tokenpak.compaction import (
    CompactionMode,
    TopicAwarePolicy,
    compact,
)
from tokenpak.compaction.modes import (
    _multi_blank_sub,
    _normalise_whitespace,
    _trim_to_tokens,
)
from tokenpak.compaction.topic_aware import (
    TopicBoundaryDetector,
    TopicSegment,
    place_topic_aware_breakpoints,
)

# ---------------------------------------------------------------------------
# 1. modes.py — edge cases and helpers
# ---------------------------------------------------------------------------


class TestModesHelpers(unittest.TestCase):
    """Coverage for internal helper functions in modes.py."""

    def test_normalise_whitespace_with_tabs(self):
        """Tabs should be converted to 4-space indents."""
        text = "\tline1\n\t\tline2\n\t\t\tline3"
        result = _normalise_whitespace(text)
        # line1 has 1 leading tab -> 4 spaces, but strip() removes leading from first line
        # Check the indented lines have proper conversion
        self.assertIn("        line2", result)  # 2 tabs -> 8 spaces
        self.assertIn("            line3", result)  # 3 tabs -> 12 spaces
        self.assertNotIn("\t", result)

    def test_multi_blank_sub_collapses_blanks(self):
        """Multiple blank lines collapse to two."""
        text = "a\n\n\n\n\nb"
        result = _multi_blank_sub(text)
        self.assertEqual(result, "a\n\nb")

    def test_trim_to_tokens_short_text(self):
        """Text shorter than target should be unchanged."""
        text = "short text"
        result = _trim_to_tokens(text, 100)
        self.assertEqual(result, text)

    def test_trim_to_tokens_long_text(self):
        """Long text should be trimmed and end with ellipsis."""
        text = "word " * 1000  # 5000 chars
        result = _trim_to_tokens(text, 100)  # ~400 chars target
        self.assertLess(len(result), len(text))
        self.assertTrue(result.endswith("…"))


class TestCompactionModeEnum(unittest.TestCase):
    """Coverage for CompactionMode enum conversions."""

    def test_mode_string_values(self):
        """Mode values should be lowercase strings."""
        self.assertEqual(CompactionMode.LOSSLESS.value, "lossless")
        self.assertEqual(CompactionMode.BALANCED.value, "balanced")
        self.assertEqual(CompactionMode.AGGRESSIVE.value, "aggressive")
        self.assertEqual(CompactionMode.SEMANTIC.value, "semantic")

    def test_compact_with_string_mode(self):
        """compact() should accept string mode names."""
        text = "Hello world\n\n\n\nGoodbye"
        result = compact(text, mode="lossless")
        self.assertIn("Hello", result)


# ---------------------------------------------------------------------------
# 2. policy.py — TopicAwarePolicy serialization and per_topic_limits
# ---------------------------------------------------------------------------


class TestTopicAwarePolicySerialization(unittest.TestCase):
    """Coverage for TopicAwarePolicy to_dict with non-default values."""

    def test_to_dict_with_custom_active_mode(self):
        """to_dict should include active_mode when not balanced."""
        policy = TopicAwarePolicy(
            active_mode=CompactionMode.LOSSLESS,
            inactive_mode=CompactionMode.AGGRESSIVE,
        )
        d = policy.to_dict()
        inner = d["compaction"]
        self.assertEqual(inner.get("active_mode"), "lossless")

    def test_to_dict_with_custom_inactive_mode(self):
        """to_dict should include inactive_mode when not aggressive."""
        policy = TopicAwarePolicy(
            active_mode=CompactionMode.BALANCED,
            inactive_mode=CompactionMode.BALANCED,  # not aggressive
        )
        d = policy.to_dict()
        inner = d["compaction"]
        self.assertEqual(inner.get("inactive_mode"), "balanced")

    def test_to_dict_with_custom_threshold(self):
        """to_dict should include activity_threshold when not 0.5."""
        policy = TopicAwarePolicy(activity_threshold=0.7)
        d = policy.to_dict()
        inner = d["compaction"]
        self.assertEqual(inner.get("activity_threshold"), 0.7)

    def test_to_dict_with_per_topic_limits(self):
        """to_dict should include per_topic_limits when set."""
        policy = TopicAwarePolicy(per_topic_limits={"topic_1": 500, "topic_2": 200})
        d = policy.to_dict()
        inner = d["compaction"]
        self.assertEqual(inner.get("per_topic_limits"), {"topic_1": 500, "topic_2": 200})


class TestTopicAwarePolicyFromDict(unittest.TestCase):
    """Coverage for TopicAwarePolicy.from_dict with per_topic_limits."""

    def test_from_dict_with_per_topic_limits(self):
        """from_dict should parse per_topic_limits."""
        data = {
            "compaction": {
                "mode": "balanced",
                "per_topic_limits": {"topic_a": 1000, "topic_b": None},
            }
        }
        policy = TopicAwarePolicy.from_dict(data)
        self.assertEqual(policy.per_topic_limits["topic_a"], 1000)
        self.assertIsNone(policy.per_topic_limits["topic_b"])


class TestCompactBlockWithTopics(unittest.TestCase):
    """Coverage for TopicAwarePolicy.compact_block_with_topics."""

    def test_compact_block_with_topics_code_block(self):
        """Code blocks should use standard compaction."""
        policy = TopicAwarePolicy()
        code = "def foo():\n    return 42\n" * 50  # >500 chars
        result = policy.compact_block_with_topics(code, block_type="code")
        # Should use standard compact_block, not topic-aware
        self.assertIn("def foo", result)

    def test_compact_block_with_topics_short_text(self):
        """Short text (<500 chars) should use standard compaction."""
        policy = TopicAwarePolicy()
        short = "This is a short text."
        result = policy.compact_block_with_topics(short)
        self.assertIn("short", result)

    def test_compact_block_with_topics_instructions(self):
        """Instructions block type should use standard compaction."""
        policy = TopicAwarePolicy()
        instructions = "Step 1: Do this.\nStep 2: Do that.\n" * 50
        result = policy.compact_block_with_topics(instructions, block_type="instructions")
        self.assertIn("Step 1", result)


# ---------------------------------------------------------------------------
# 3. topic_aware.py — TopicSegment and edge cases
# ---------------------------------------------------------------------------


class TestTopicSegmentProperties(unittest.TestCase):
    """Coverage for TopicSegment computed properties."""

    def test_length_chars_property(self):
        """length_chars should return end - start."""
        segment = TopicSegment(
            start=10,
            end=50,
            content="test content",
            topic_id="topic_1",
        )
        self.assertEqual(segment.length_chars, 40)

    def test_to_dict_includes_all_fields(self):
        """to_dict should include all fields."""
        segment = TopicSegment(
            start=0,
            end=100,
            content="test",
            topic_id="topic_123",
            activity_score=0.75,
            reference_count=3,
            recency_score=0.8,
        )
        d = segment.to_dict()
        self.assertEqual(d["start"], 0)
        self.assertEqual(d["end"], 100)
        self.assertEqual(d["topic_id"], "topic_123")
        self.assertEqual(d["activity_score"], 0.75)
        self.assertEqual(d["reference_count"], 3)
        self.assertEqual(d["recency_score"], 0.8)
        self.assertEqual(d["length_chars"], 100)

    def test_is_active_above_threshold(self):
        """is_active should return True when activity_score >= 0.5."""
        segment = TopicSegment(start=0, end=10, content="test", topic_id="t1", activity_score=0.6)
        self.assertTrue(segment.is_active)

    def test_is_active_below_threshold(self):
        """is_active should return False when activity_score < 0.5."""
        segment = TopicSegment(start=0, end=10, content="test", topic_id="t1", activity_score=0.3)
        self.assertFalse(segment.is_active)


class TestTopicBoundaryDetectorEdgeCases(unittest.TestCase):
    """Coverage for edge cases in TopicBoundaryDetector.segment()."""

    def test_segment_empty_text(self):
        """Empty text should return empty list."""
        detector = TopicBoundaryDetector()
        segments = detector.segment("")
        self.assertEqual(segments, [])

    def test_segment_very_short_text(self):
        """Very short text should return single segment."""
        detector = TopicBoundaryDetector(chunk_size=100)
        segments = detector.segment("Hello world")
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0].content, "Hello world")

    def test_segment_skipped_content_merging(self):
        """Segments below min_segment_chars should be merged."""
        # Create text with a very short segment in the middle
        detector = TopicBoundaryDetector(
            chunk_size=50,
            similarity_threshold=0.1,  # Very low to force boundaries
            min_segment_chars=100,
        )
        # Text designed to have very different sections
        text = "A" * 150 + "\n\n" + "B" * 30 + "\n\n" + "C" * 150
        segments = detector.segment(text)
        # Short "B" section should be merged
        for s in segments:
            self.assertGreaterEqual(len(s.content), 100)


class TestPlaceTopicAwareBreakpoints(unittest.TestCase):
    """Coverage for place_topic_aware_breakpoints function."""

    def test_empty_segments(self):
        """Empty segments list should return empty dict."""
        result = place_topic_aware_breakpoints([], 1000)
        self.assertEqual(result, {})

    def test_zero_target_tokens(self):
        """Zero target tokens should return empty dict."""
        segments = [TopicSegment(start=0, end=100, content="test", topic_id="t1")]
        result = place_topic_aware_breakpoints(segments, 0)
        self.assertEqual(result, {})

    def test_inactive_topic_budget_allocation(self):
        """Inactive topics should receive budget from 30% allocation."""
        segments = [
            TopicSegment(
                start=0,
                end=100,
                content="inactive topic content",
                topic_id="inactive_1",
                activity_score=0.2,
            ),
        ]
        result = place_topic_aware_breakpoints(segments, 1000)
        # Inactive topics get from 30% budget
        self.assertIn("inactive_1", result)
        self.assertGreater(result["inactive_1"], 0)


# ---------------------------------------------------------------------------
# Run tests
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    unittest.main()
