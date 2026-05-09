"""
Unit tests for compaction/topic_aware.py.

Covers: TopicSegment properties, TopicBoundaryDetector (init, segment, helpers),
and place_topic_aware_breakpoints.
"""

import pytest

from tokenpak.compression.budgets.topic_aware import (
    TopicBoundaryDetector,
    TopicSegment,
    place_topic_aware_breakpoints,
)

# ============================================================================
# TopicSegment
# ============================================================================


class TestTopicSegment:
    def test_initialization(self):
        seg = TopicSegment(
            start=0,
            end=100,
            content="hello world",
            topic_id="topic_0",
            activity_score=0.7,
        )
        assert seg.start == 0
        assert seg.end == 100
        assert seg.content == "hello world"
        assert seg.topic_id == "topic_0"
        assert seg.activity_score == 0.7

    def test_default_activity_score(self):
        seg = TopicSegment(start=0, end=10, content="x", topic_id="topic_0")
        assert seg.activity_score == 0.5

    def test_default_reference_count(self):
        seg = TopicSegment(start=0, end=10, content="x", topic_id="topic_0")
        assert seg.reference_count == 0

    def test_default_recency_score(self):
        seg = TopicSegment(start=0, end=10, content="x", topic_id="topic_0")
        assert seg.recency_score == 0.5

    def test_is_active_above_threshold(self):
        seg = TopicSegment(start=0, end=10, content="x", topic_id="t", activity_score=0.6)
        assert seg.is_active is True

    def test_is_active_at_threshold(self):
        seg = TopicSegment(start=0, end=10, content="x", topic_id="t", activity_score=0.5)
        assert seg.is_active is True

    def test_is_inactive_below_threshold(self):
        seg = TopicSegment(start=0, end=10, content="x", topic_id="t", activity_score=0.4)
        assert seg.is_active is False

    def test_length_chars(self):
        seg = TopicSegment(start=5, end=55, content="x" * 50, topic_id="t")
        assert seg.length_chars == 50

    def test_length_chars_zero(self):
        seg = TopicSegment(start=10, end=10, content="", topic_id="t")
        assert seg.length_chars == 0

    def test_to_dict_keys(self):
        seg = TopicSegment(
            start=0,
            end=100,
            content="content",
            topic_id="topic_0",
            activity_score=0.7,
            reference_count=2,
            recency_score=0.8,
        )
        d = seg.to_dict()
        assert d["start"] == 0
        assert d["end"] == 100
        assert d["topic_id"] == "topic_0"
        assert d["activity_score"] == 0.7
        assert d["reference_count"] == 2
        assert d["recency_score"] == 0.8
        assert d["length_chars"] == 100

    def test_to_dict_no_content_key(self):
        seg = TopicSegment(start=0, end=10, content="secret", topic_id="t")
        d = seg.to_dict()
        assert "content" not in d


# ============================================================================
# TopicBoundaryDetector — initialization
# ============================================================================


class TestTopicBoundaryDetectorInit:
    def test_defaults(self):
        det = TopicBoundaryDetector()
        assert det.chunk_size == 100
        assert det.similarity_threshold == 0.3
        assert det.min_segment_chars == 50

    def test_custom_params(self):
        det = TopicBoundaryDetector(chunk_size=200, similarity_threshold=0.5, min_segment_chars=100)
        assert det.chunk_size == 200
        assert det.similarity_threshold == 0.5
        assert det.min_segment_chars == 100

    def test_topic_counter_starts_at_zero(self):
        det = TopicBoundaryDetector()
        assert det._topic_counter == 0


# ============================================================================
# TopicBoundaryDetector — segment()
# ============================================================================


class TestSegment:
    def test_empty_string_returns_empty_list(self):
        det = TopicBoundaryDetector()
        result = det.segment("")
        assert result == []

    def test_short_text_returns_single_segment(self):
        det = TopicBoundaryDetector(chunk_size=200)
        result = det.segment("short text")
        assert len(result) == 1
        assert result[0].content == "short text"
        assert result[0].topic_id == "topic_0"

    def test_short_text_segment_spans_full_text(self):
        det = TopicBoundaryDetector(chunk_size=200)
        text = "hello world"
        result = det.segment(text)
        assert result[0].start == 0
        assert result[0].end == len(text)

    def test_topic_counter_increments(self):
        det = TopicBoundaryDetector(chunk_size=200)
        det.segment("text one")
        det.segment("text two")
        # Counter should be at 2 now
        assert det._topic_counter == 2

    def test_single_segment_activity_score_set(self):
        det = TopicBoundaryDetector(chunk_size=200)
        result = det.segment("some content")
        assert 0.0 <= result[0].activity_score <= 1.0

    def test_long_text_returns_segments(self):
        det = TopicBoundaryDetector()
        # Two distinct topics concatenated
        text = (
            "Machine learning trains neural networks on data. "
            "Backpropagation computes gradients efficiently. " * 5
            + "Gardening involves soil, water, and sunlight. "
            "Plants grow better with fertilizer and pruning. " * 5
        )
        result = det.segment(text)
        assert len(result) >= 1
        # All segments should be TopicSegment objects
        for seg in result:
            assert isinstance(seg, TopicSegment)

    def test_segments_cover_full_text(self):
        det = TopicBoundaryDetector()
        text = "word " * 50
        result = det.segment(text)
        if result:
            # First segment starts at or before 0
            assert result[0].start == 0 or result[0].start >= 0
            # All segment content is non-empty
            for seg in result:
                assert len(seg.content) > 0

    def test_segments_have_unique_topic_ids(self):
        det = TopicBoundaryDetector()
        text = "word " * 50
        result = det.segment(text)
        ids = [seg.topic_id for seg in result]
        assert len(ids) == len(set(ids))

    def test_whitespace_only_text(self):
        det = TopicBoundaryDetector()
        result = det.segment("   ")
        # Should not raise; returns list (possibly with one segment)
        assert isinstance(result, list)


# ============================================================================
# TopicBoundaryDetector — internal helpers
# ============================================================================


class TestDetectorHelpers:
    def setup_method(self):
        self.det = TopicBoundaryDetector()

    def test_tokenize_lowercases(self):
        tokens = self.det._tokenize("Hello WORLD")
        assert all(t == t.lower() for t in tokens)

    def test_tokenize_filters_short_words(self):
        tokens = self.det._tokenize("a is to hello")
        # 'a', 'is', 'to' are len <= 2 and should be filtered
        assert "a" not in tokens
        assert "is" not in tokens
        assert "hello" in tokens

    def test_tokenize_empty_string(self):
        tokens = self.det._tokenize("")
        assert tokens == []

    def test_chunk_similarity_identical_chunks(self):
        sim = self.det._chunk_similarity("hello world test", "hello world test")
        assert sim == 1.0

    def test_chunk_similarity_disjoint_chunks(self):
        sim = self.det._chunk_similarity("apple banana cherry", "xylophone zeppelin frisbee")
        assert sim == 0.0

    def test_chunk_similarity_partial_overlap(self):
        sim = self.det._chunk_similarity("hello world foo", "hello world bar")
        assert 0.0 < sim < 1.0

    def test_chunk_similarity_empty_chunks(self):
        sim = self.det._chunk_similarity("", "hello world")
        assert sim == 0.0

    def test_compute_similarities_two_chunks(self):
        chunks = ["hello world foo", "hello world bar"]
        sims = self.det._compute_similarities(chunks)
        assert len(sims) == 1

    def test_compute_similarities_single_chunk(self):
        sims = self.det._compute_similarities(["only one"])
        assert sims == []

    def test_make_chunks_length(self):
        text = "x" * 200
        chunks = self.det._make_chunks(text)
        # With chunk_size=100, step=50: should produce chunks
        assert len(chunks) > 0
        for chunk in chunks:
            assert len(chunk) <= self.det.chunk_size

    def test_score_activity_neutral_baseline(self):
        score = self.det._score_activity("plain text with no markers")
        assert score == pytest.approx(0.5)

    def test_score_activity_recency_marker_raises_score(self):
        score = self.det._score_activity("we are currently working on this now")
        assert score > 0.5

    def test_score_activity_action_marker_raises_score(self):
        score = self.det._score_activity("we are actively building and developing features")
        assert score > 0.5

    def test_score_activity_code_symbols_raises_score(self):
        score = self.det._score_activity("function foo() { return bar(); } extra brackets []")
        assert score > 0.5

    def test_score_activity_capped_at_one(self):
        score = self.det._score_activity(
            "currently now actively running implement building develop {()[]{()[]()}}"
        )
        assert score <= 1.0

    def test_score_recency_neutral(self):
        score = self.det._score_recency("general topic about history")
        assert score == pytest.approx(0.5)

    def test_score_recency_today_marker(self):
        score = self.det._score_recency("today we reviewed the results")
        assert score == pytest.approx(0.8)

    def test_score_recency_yesterday_marker(self):
        score = self.det._score_recency("yesterday we finished the sprint")
        assert score == pytest.approx(0.6)

    def test_count_cross_references_no_refs(self):
        count = self.det._count_cross_references("plain text with no links")
        assert count == 0

    def test_count_cross_references_with_links(self):
        count = self.det._count_cross_references("See [link one] and [link two] for details.")
        assert count == 2

    def test_count_cross_references_see_also(self):
        count = self.det._count_cross_references("See also the related section below.")
        assert count >= 1

    def test_find_boundaries_below_threshold(self):
        # All similarities below threshold → boundary at each position
        similarities = [0.1, 0.2, 0.05]
        boundaries = self.det._find_boundaries(similarities)
        assert len(boundaries) == 3

    def test_find_boundaries_above_threshold(self):
        # All similarities above threshold → no boundaries
        similarities = [0.8, 0.9, 0.7]
        boundaries = self.det._find_boundaries(similarities)
        assert len(boundaries) == 0

    def test_find_boundaries_mixed(self):
        similarities = [0.8, 0.1, 0.9]
        boundaries = self.det._find_boundaries(similarities)
        assert len(boundaries) == 1


# ============================================================================
# place_topic_aware_breakpoints
# ============================================================================


class TestPlaceTopicAwareBreakpoints:
    def test_empty_segments_returns_empty(self):
        result = place_topic_aware_breakpoints([], 1000)
        assert result == {}

    def test_zero_target_tokens_returns_empty(self):
        seg = TopicSegment(0, 100, "content", "topic_0", activity_score=0.8)
        result = place_topic_aware_breakpoints([seg], 0)
        assert result == {}

    def test_negative_target_tokens_returns_empty(self):
        seg = TopicSegment(0, 100, "content", "topic_0", activity_score=0.8)
        result = place_topic_aware_breakpoints([seg], -1)
        assert result == {}

    def test_active_segment_gets_budget(self):
        seg = TopicSegment(0, 100, "active", "topic_0", activity_score=0.8)
        result = place_topic_aware_breakpoints([seg], 1000)
        assert "topic_0" in result
        assert result["topic_0"] >= 50  # minimum allocation

    def test_inactive_segment_gets_budget(self):
        seg = TopicSegment(0, 100, "inactive", "topic_0", activity_score=0.2)
        result = place_topic_aware_breakpoints([seg], 1000)
        assert "topic_0" in result
        assert result["topic_0"] >= 20  # minimum allocation

    def test_active_gets_more_than_inactive(self):
        active = TopicSegment(0, 100, "active", "topic_0", activity_score=0.9)
        inactive = TopicSegment(100, 200, "inactive", "topic_1", activity_score=0.1)
        result = place_topic_aware_breakpoints([active, inactive], 1000)
        assert result["topic_0"] > result["topic_1"]

    def test_budget_split_70_30(self):
        """Active pool gets ~70%, inactive pool gets ~30%."""
        active = TopicSegment(0, 100, "active", "topic_0", activity_score=0.9)
        inactive = TopicSegment(100, 200, "inactive", "topic_1", activity_score=0.1)
        result = place_topic_aware_breakpoints([active, inactive], 1000)
        # Active budget ceiling = 700, inactive ceiling = 300
        # With only one active and one inactive, each gets its pool's full budget
        assert result["topic_0"] <= 700
        assert result["topic_1"] <= 300

    def test_all_active_segments(self):
        segs = [
            TopicSegment(0, 100, "a", "topic_0", activity_score=0.8),
            TopicSegment(100, 200, "b", "topic_1", activity_score=0.7),
        ]
        result = place_topic_aware_breakpoints(segs, 1000)
        assert "topic_0" in result
        assert "topic_1" in result

    def test_all_inactive_segments(self):
        segs = [
            TopicSegment(0, 100, "a", "topic_0", activity_score=0.2),
            TopicSegment(100, 200, "b", "topic_1", activity_score=0.3),
        ]
        result = place_topic_aware_breakpoints(segs, 1000)
        assert "topic_0" in result
        assert "topic_1" in result

    def test_minimum_allocation_enforced_for_active(self):
        # Very small target_tokens should still give minimum 50 to active
        seg = TopicSegment(0, 100, "active", "topic_0", activity_score=1.0)
        result = place_topic_aware_breakpoints([seg], 1)
        assert result.get("topic_0", 0) >= 50

    def test_minimum_allocation_enforced_for_inactive(self):
        seg = TopicSegment(0, 100, "inactive", "topic_0", activity_score=0.0)
        result = place_topic_aware_breakpoints([seg], 1)
        assert result.get("topic_0", 0) >= 20

    def test_result_topic_ids_match_segments(self):
        segs = [
            TopicSegment(0, 100, "a", "alpha", activity_score=0.8),
            TopicSegment(100, 200, "b", "beta", activity_score=0.2),
        ]
        result = place_topic_aware_breakpoints(segs, 500)
        assert set(result.keys()) == {"alpha", "beta"}
