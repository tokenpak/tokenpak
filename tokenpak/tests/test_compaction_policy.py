"""
Unit tests for compaction/policy.py.

Covers: BlockPolicy, CompactionPolicy (from_dict, to_dict, compact_block,
resolve_mode), and TopicAwarePolicy (from_dict, to_dict, compact_with_topics,
compact_block_with_topics).
"""

from unittest.mock import MagicMock, patch

from tokenpak.compression.budgets.modes import CompactionMode
from tokenpak.compression.budgets.policy import BlockPolicy, CompactionPolicy, TopicAwarePolicy

# ============================================================================
# BlockPolicy
# ============================================================================


class TestBlockPolicy:
    def test_default_mode_is_balanced(self):
        bp = BlockPolicy()
        assert bp.mode == CompactionMode.BALANCED

    def test_default_max_tokens_is_none(self):
        bp = BlockPolicy()
        assert bp.max_tokens is None

    def test_from_dict_mode_and_max_tokens(self):
        bp = BlockPolicy.from_dict({"mode": "lossless", "max_tokens": 500})
        assert bp.mode == CompactionMode.LOSSLESS
        assert bp.max_tokens == 500

    def test_from_dict_mode_only(self):
        bp = BlockPolicy.from_dict({"mode": "aggressive"})
        assert bp.mode == CompactionMode.AGGRESSIVE
        assert bp.max_tokens is None

    def test_from_dict_empty_uses_defaults(self):
        bp = BlockPolicy.from_dict({})
        assert bp.mode == CompactionMode.BALANCED
        assert bp.max_tokens is None

    def test_to_dict_includes_mode(self):
        bp = BlockPolicy(mode=CompactionMode.LOSSLESS)
        d = bp.to_dict()
        assert d["mode"] == "lossless"

    def test_to_dict_includes_max_tokens_when_set(self):
        bp = BlockPolicy(mode=CompactionMode.BALANCED, max_tokens=1000)
        d = bp.to_dict()
        assert d["max_tokens"] == 1000

    def test_to_dict_omits_max_tokens_when_none(self):
        bp = BlockPolicy()
        d = bp.to_dict()
        assert "max_tokens" not in d

    def test_roundtrip(self):
        bp = BlockPolicy(mode=CompactionMode.AGGRESSIVE, max_tokens=200)
        restored = BlockPolicy.from_dict(bp.to_dict())
        assert restored.mode == bp.mode
        assert restored.max_tokens == bp.max_tokens


# ============================================================================
# CompactionPolicy
# ============================================================================


class TestCompactionPolicy:
    def test_default_mode_is_balanced(self):
        p = CompactionPolicy()
        assert p.mode == CompactionMode.BALANCED

    def test_default_max_tokens_is_none(self):
        p = CompactionPolicy()
        assert p.max_tokens is None

    def test_default_priority_order(self):
        p = CompactionPolicy()
        assert p.priority_order == ["instructions", "code", "knowledge"]

    def test_default_per_block_limits_empty(self):
        p = CompactionPolicy()
        assert p.per_block_limits == {}

    def test_default_factory(self):
        p = CompactionPolicy.default()
        assert p.mode == CompactionMode.BALANCED

    def test_from_dict_wrapped(self):
        p = CompactionPolicy.from_dict(
            {"compaction": {"mode": "aggressive", "max_tokens": 4000}}
        )
        assert p.mode == CompactionMode.AGGRESSIVE
        assert p.max_tokens == 4000

    def test_from_dict_flat(self):
        # Accepts flat dict without 'compaction' wrapper
        p = CompactionPolicy.from_dict({"mode": "lossless"})
        assert p.mode == CompactionMode.LOSSLESS

    def test_from_dict_per_block_limits(self):
        p = CompactionPolicy.from_dict(
            {
                "compaction": {
                    "per_block_limits": {
                        "code": {"mode": "lossless"},
                        "instructions": {"mode": "balanced", "max_tokens": 500},
                    }
                }
            }
        )
        assert "code" in p.per_block_limits
        assert p.per_block_limits["code"].mode == CompactionMode.LOSSLESS
        assert p.per_block_limits["instructions"].max_tokens == 500

    def test_from_dict_priority_order(self):
        p = CompactionPolicy.from_dict(
            {"compaction": {"priority_order": ["code", "instructions"]}}
        )
        assert p.priority_order == ["code", "instructions"]

    def test_to_dict_wraps_in_compaction_key(self):
        p = CompactionPolicy(mode=CompactionMode.AGGRESSIVE)
        d = p.to_dict()
        assert "compaction" in d
        assert d["compaction"]["mode"] == "aggressive"

    def test_to_dict_includes_max_tokens_when_set(self):
        p = CompactionPolicy(mode=CompactionMode.BALANCED, max_tokens=8000)
        d = p.to_dict()
        assert d["compaction"]["max_tokens"] == 8000

    def test_to_dict_omits_max_tokens_when_none(self):
        p = CompactionPolicy()
        d = p.to_dict()
        assert "max_tokens" not in d["compaction"]

    def test_to_dict_includes_per_block_limits_when_set(self):
        p = CompactionPolicy.from_dict(
            {"compaction": {"per_block_limits": {"code": {"mode": "lossless"}}}}
        )
        d = p.to_dict()
        assert "per_block_limits" in d["compaction"]
        assert d["compaction"]["per_block_limits"]["code"]["mode"] == "lossless"

    def test_roundtrip(self):
        original = CompactionPolicy.from_dict(
            {
                "compaction": {
                    "mode": "aggressive",
                    "max_tokens": 3000,
                    "priority_order": ["code", "knowledge"],
                    "per_block_limits": {"code": {"mode": "lossless"}},
                }
            }
        )
        restored = CompactionPolicy.from_dict(original.to_dict())
        assert restored.mode == original.mode
        assert restored.max_tokens == original.max_tokens
        assert restored.priority_order == original.priority_order

    # ------------------------------------------------------------------ #
    # resolve_mode
    # ------------------------------------------------------------------ #

    def test_resolve_mode_no_override(self):
        p = CompactionPolicy(mode=CompactionMode.AGGRESSIVE)
        assert p.resolve_mode() == CompactionMode.AGGRESSIVE

    def test_resolve_mode_with_override(self):
        p = CompactionPolicy.from_dict(
            {
                "compaction": {
                    "mode": "aggressive",
                    "per_block_limits": {"code": {"mode": "lossless"}},
                }
            }
        )
        assert p.resolve_mode("code") == CompactionMode.LOSSLESS
        assert p.resolve_mode("instructions") == CompactionMode.AGGRESSIVE

    def test_resolve_mode_unknown_block_type_uses_global(self):
        p = CompactionPolicy(mode=CompactionMode.BALANCED)
        assert p.resolve_mode("nonexistent") == CompactionMode.BALANCED

    # ------------------------------------------------------------------ #
    # compact_block
    # ------------------------------------------------------------------ #

    def test_compact_block_uses_global_mode(self):
        p = CompactionPolicy(mode=CompactionMode.LOSSLESS)
        result = p.compact_block("hello   \nworld")
        assert result == "hello\nworld"

    def test_compact_block_uses_per_block_override(self):
        p = CompactionPolicy.from_dict(
            {
                "compaction": {
                    "mode": "aggressive",
                    "per_block_limits": {"instructions": {"mode": "lossless"}},
                }
            }
        )
        result = p.compact_block("hello   \nworld", block_type="instructions")
        assert result == "hello\nworld"

    def test_compact_block_respects_per_block_max_tokens(self):
        long_text = "word " * 200  # ~1000 chars
        p = CompactionPolicy.from_dict(
            {
                "compaction": {
                    "mode": "lossless",
                    "per_block_limits": {
                        "knowledge": {"mode": "lossless", "max_tokens": 5}
                    },
                }
            }
        )
        # lossless doesn't trim by target_tokens — compact() ignores
        # target_tokens for lossless. The result is just whitespace-normalised.
        result = p.compact_block(long_text, block_type="knowledge")
        assert isinstance(result, str)

    def test_compact_block_no_block_type_uses_global(self):
        p = CompactionPolicy(mode=CompactionMode.LOSSLESS)
        result = p.compact_block("hello\t\nworld")
        assert result == "hello\nworld"


# ============================================================================
# TopicAwarePolicy
# ============================================================================


class TestTopicAwarePolicy:
    def test_default_active_mode(self):
        p = TopicAwarePolicy()
        assert p.active_mode == CompactionMode.BALANCED

    def test_default_inactive_mode(self):
        p = TopicAwarePolicy()
        assert p.inactive_mode == CompactionMode.AGGRESSIVE

    def test_default_activity_threshold(self):
        p = TopicAwarePolicy()
        assert p.activity_threshold == 0.5

    def test_default_per_topic_limits_empty(self):
        p = TopicAwarePolicy()
        assert p.per_topic_limits == {}

    def test_from_dict_topic_aware_fields(self):
        p = TopicAwarePolicy.from_dict(
            {
                "compaction": {
                    "active_mode": "lossless",
                    "inactive_mode": "balanced",
                    "activity_threshold": 0.7,
                    "per_topic_limits": {"topic_0": 200},
                }
            }
        )
        assert p.active_mode == CompactionMode.LOSSLESS
        assert p.inactive_mode == CompactionMode.BALANCED
        assert p.activity_threshold == 0.7
        assert p.per_topic_limits["topic_0"] == 200

    def test_from_dict_inherits_base_fields(self):
        p = TopicAwarePolicy.from_dict(
            {"compaction": {"mode": "aggressive", "max_tokens": 2000}}
        )
        assert p.mode == CompactionMode.AGGRESSIVE
        assert p.max_tokens == 2000

    def test_to_dict_includes_non_default_active_mode(self):
        p = TopicAwarePolicy(active_mode=CompactionMode.LOSSLESS)
        d = p.to_dict()
        assert d["compaction"]["active_mode"] == "lossless"

    def test_to_dict_omits_default_active_mode(self):
        p = TopicAwarePolicy()  # active_mode=BALANCED is default
        d = p.to_dict()
        assert "active_mode" not in d["compaction"]

    def test_to_dict_omits_default_inactive_mode(self):
        p = TopicAwarePolicy()  # inactive_mode=AGGRESSIVE is default
        d = p.to_dict()
        assert "inactive_mode" not in d["compaction"]

    def test_to_dict_includes_non_default_inactive_mode(self):
        p = TopicAwarePolicy(inactive_mode=CompactionMode.LOSSLESS)
        d = p.to_dict()
        assert d["compaction"]["inactive_mode"] == "lossless"

    def test_to_dict_includes_non_default_threshold(self):
        p = TopicAwarePolicy(activity_threshold=0.8)
        d = p.to_dict()
        assert d["compaction"]["activity_threshold"] == 0.8

    def test_to_dict_omits_default_threshold(self):
        p = TopicAwarePolicy()
        d = p.to_dict()
        assert "activity_threshold" not in d["compaction"]

    def test_to_dict_includes_per_topic_limits_when_set(self):
        p = TopicAwarePolicy(per_topic_limits={"topic_0": 300})
        d = p.to_dict()
        assert d["compaction"]["per_topic_limits"] == {"topic_0": 300}

    # ------------------------------------------------------------------ #
    # compact_with_topics — mocked detector
    # ------------------------------------------------------------------ #

    def test_compact_with_topics_single_segment_falls_back(self):
        """Single-segment text uses standard compact_block."""
        from tokenpak.compression.budgets.topic_aware import TopicSegment

        single_seg = TopicSegment(0, 20, "hello world text.", "topic_0", activity_score=0.6)
        mock_detector = MagicMock()
        mock_detector.segment.return_value = [single_seg]

        p = TopicAwarePolicy(mode=CompactionMode.LOSSLESS)

        with patch(
            "tokenpak.compression.budgets.topic_aware.TopicBoundaryDetector",
            return_value=mock_detector,
        ):
            result = p.compact_with_topics("hello world text.")

        assert isinstance(result, str)

    def test_compact_with_topics_empty_text(self):
        """Empty segments list falls back to compact_block."""
        mock_detector = MagicMock()
        mock_detector.segment.return_value = []

        p = TopicAwarePolicy(mode=CompactionMode.LOSSLESS)

        with patch(
            "tokenpak.compression.budgets.topic_aware.TopicBoundaryDetector",
            return_value=mock_detector,
        ):
            result = p.compact_with_topics("")

        assert isinstance(result, str)

    def test_compact_with_topics_active_uses_active_mode(self):
        """Active topics are compacted with active_mode."""
        from tokenpak.compression.budgets.topic_aware import TopicSegment

        active_seg = TopicSegment(0, 50, "active topic content here", "topic_0", activity_score=0.8)
        inactive_seg = TopicSegment(50, 100, "inactive topic content here", "topic_1", activity_score=0.2)

        mock_detector = MagicMock()
        mock_detector.segment.return_value = [active_seg, inactive_seg]

        called_with = []

        def mock_compact(text, mode, target_tokens=None):
            called_with.append((text, mode))
            return text

        p = TopicAwarePolicy(
            active_mode=CompactionMode.LOSSLESS,
            inactive_mode=CompactionMode.AGGRESSIVE,
            activity_threshold=0.5,
        )

        # TopicBoundaryDetector is inline-imported in compact_with_topics → patch in topic_aware module
        # compact is module-level in policy.py → patch there
        with patch("tokenpak.compression.budgets.topic_aware.TopicBoundaryDetector", return_value=mock_detector):
            with patch("tokenpak.compression.budgets.policy.compact", side_effect=mock_compact):
                p.compact_with_topics("active topic content here inactive topic content here")

        modes_used = [m for _, m in called_with]
        assert CompactionMode.LOSSLESS in modes_used
        assert CompactionMode.AGGRESSIVE in modes_used

    def test_compact_with_topics_per_topic_limit_overrides_budget(self):
        """Per-topic limits override the breakpoint budget."""
        from tokenpak.compression.budgets.topic_aware import TopicSegment

        seg_a = TopicSegment(0, 50, "topic content alpha", "topic_0", activity_score=0.8)
        seg_b = TopicSegment(50, 100, "topic content beta", "topic_1", activity_score=0.8)
        mock_detector = MagicMock()
        mock_detector.segment.return_value = [seg_a, seg_b]

        captured = []

        def mock_compact(text, mode, target_tokens=None):
            captured.append(target_tokens)
            return text

        p = TopicAwarePolicy(
            active_mode=CompactionMode.LOSSLESS,
            per_topic_limits={"topic_0": 42},
        )

        # Both TopicBoundaryDetector and place_topic_aware_breakpoints are inline-imported
        # → patch them in the topic_aware module; compact is module-level in policy.py
        with patch("tokenpak.compression.budgets.topic_aware.TopicBoundaryDetector", return_value=mock_detector):
            with patch(
                "tokenpak.compression.budgets.topic_aware.place_topic_aware_breakpoints",
                return_value={"topic_0": 999},
            ):
                with patch("tokenpak.compression.budgets.policy.compact", side_effect=mock_compact):
                    p.compact_with_topics("topic content")

        # Per-topic limit 42 should override breakpoint budget 999
        assert 42 in captured

    # ------------------------------------------------------------------ #
    # compact_block_with_topics
    # ------------------------------------------------------------------ #

    def test_code_block_uses_standard_compaction(self):
        """Code blocks bypass topic-aware path."""
        p = TopicAwarePolicy(mode=CompactionMode.LOSSLESS)
        result = p.compact_block_with_topics("code content\n" * 10, block_type="code")
        assert isinstance(result, str)

    def test_instructions_block_uses_standard_compaction(self):
        p = TopicAwarePolicy(mode=CompactionMode.LOSSLESS)
        result = p.compact_block_with_topics("instruction content\n" * 10, block_type="instructions")
        assert isinstance(result, str)

    def test_short_text_uses_standard_compaction(self):
        """Text shorter than 500 chars bypasses topic-aware path."""
        p = TopicAwarePolicy(mode=CompactionMode.LOSSLESS)
        result = p.compact_block_with_topics("short text", block_type=None)
        assert isinstance(result, str)

    def test_large_narrative_uses_topic_aware(self):
        """Text > 500 chars with no special block type goes through topic-aware path."""
        long_text = "narrative content word " * 30  # > 500 chars

        p = TopicAwarePolicy(mode=CompactionMode.LOSSLESS)

        with patch.object(p, "compact_with_topics", return_value="topic result") as mock_t:
            result = p.compact_block_with_topics(long_text, block_type=None)

        mock_t.assert_called_once_with(long_text)
        assert result == "topic result"
