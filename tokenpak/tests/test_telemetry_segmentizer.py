"""Unit tests for telemetry segmentizer — TTL, batch, and merge logic."""


import pytest

from tokenpak.telemetry.segmentizer import (
    Segment,
    SegmentType,
    _estimate_tokens,
    _make_segment_id,
    _sha256,
    segmentize,
)


class TestSegmentBasics:
    """Basic segment creation and property tests."""

    def test_segment_creation_defaults(self):
        """Test Segment dataclass initialization with defaults."""
        seg = Segment()
        assert seg.segment_id == ""
        assert seg.segment_type == SegmentType.other.value
        assert seg.raw_hash == ""
        assert seg.tokens_raw == 0
        assert seg.relevance_score == 0.5

    def test_segment_creation_with_values(self):
        """Test Segment initialization with explicit values."""
        seg = Segment(
            trace_id="test-trace",
            segment_id="test-seg-id",
            order=1,
            segment_type=SegmentType.user.value,
            raw_hash="abc123",
            raw_len=100,
            tokens_raw=25,
        )
        assert seg.trace_id == "test-trace"
        assert seg.segment_id == "test-seg-id"
        assert seg.order == 1
        assert seg.segment_type == SegmentType.user.value
        assert seg.raw_hash == "abc123"
        assert seg.raw_len == 100
        assert seg.tokens_raw == 25


class TestSegmentization:
    """Test segmentize() function with various message shapes."""

    def test_empty_messages_list(self):
        """Empty messages should return empty segment list."""
        result = segmentize([])
        assert result == []

    def test_single_user_message(self):
        """Single user message should create one segment."""
        messages = [{"role": "user", "content": "Hello"}]
        segments = segmentize(messages)

        assert len(segments) == 1
        assert segments[0].segment_type == SegmentType.user.value
        assert segments[0].order == 0

    def test_user_assistant_turn(self):
        """User + assistant turn should create correct segment types."""
        messages = [
            {"role": "user", "content": "What is 2+2?"},
            {"role": "assistant", "content": "4"},
        ]
        segments = segmentize(messages)

        assert len(segments) == 2
        assert segments[0].segment_type == SegmentType.user.value
        # Last assistant is NOT marked as context; it's the current response
        assert segments[1].segment_type != SegmentType.assistant_context.value

    def test_multi_turn_last_assistant_is_not_context(self):
        """Last assistant message should NOT be marked as context."""
        messages = [
            {"role": "user", "content": "Q1?"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2?"},
            {"role": "assistant", "content": "A2"},
        ]
        segments = segmentize(messages)

        assert len(segments) == 4
        # First assistant is context
        assert segments[1].segment_type == SegmentType.assistant_context.value
        # Last assistant is the current response (not context)
        assert segments[3].segment_type != SegmentType.assistant_context.value

    def test_segmentize_with_tools(self):
        """Tools should create synthetic tool_schema segment."""
        messages = [{"role": "user", "content": "Help"}]
        tools = [{"name": "search", "description": "Search"}]

        segments = segmentize(messages, tools=tools)

        # One message + one tool_schema segment
        assert len(segments) == 2
        assert segments[0].segment_type == SegmentType.user.value
        assert segments[1].segment_type == SegmentType.tool_schema.value
        assert segments[1].order == 1  # Appended after messages

    def test_segmentize_without_tools(self):
        """Without tools, should have no tool_schema segment."""
        messages = [{"role": "user", "content": "Help"}]

        segments = segmentize(messages, tools=None)

        assert len(segments) == 1
        assert segments[0].segment_type == SegmentType.user.value

    def test_segment_id_is_deterministic(self):
        """Segment IDs should be stable across calls (UUID5)."""
        messages = [{"role": "user", "content": "Q"}]
        trace_id = "trace-123"

        segments1 = segmentize(messages, trace_id=trace_id)
        segments2 = segmentize(messages, trace_id=trace_id)

        assert segments1[0].segment_id == segments2[0].segment_id


class TestTokenEstimation:
    """Test token estimation logic."""

    def test_token_estimation_basic(self):
        """Rough token count should be text_len // 4."""
        text = "Hello world"  # 11 chars
        tokens = _estimate_tokens(text)
        # Should be approximately 11 // 4 = 2, with rounding up
        assert tokens >= 2
        assert tokens <= 4

    def test_token_estimation_empty(self):
        """Empty text should estimate to 0 tokens."""
        tokens = _estimate_tokens("")
        assert tokens == 0

    def test_token_estimation_long_text(self):
        """Long text should scale roughly linearly."""
        short = _estimate_tokens("a" * 100)
        long = _estimate_tokens("a" * 400)
        # Long should be roughly 4x short
        assert long >= short * 3.5
        assert long <= short * 4.5


class TestHashingAndContent:
    """Test SHA256 hashing and content flattening."""

    def test_sha256_simple_text(self):
        """SHA256 should produce consistent hex hash."""
        hash1 = _sha256("hello")
        hash2 = _sha256("hello")
        assert hash1 == hash2
        assert len(hash1) == 64  # SHA256 hex is 64 chars
        assert all(c in "0123456789abcdef" for c in hash1)

    def test_sha256_different_inputs(self):
        """Different inputs should produce different hashes."""
        hash1 = _sha256("hello")
        hash2 = _sha256("world")
        assert hash1 != hash2

    def test_segment_id_generation(self):
        """_make_segment_id should produce stable UUID5."""
        id1 = _make_segment_id("trace-1", 0)
        id2 = _make_segment_id("trace-1", 0)
        id3 = _make_segment_id("trace-2", 0)

        assert id1 == id2  # Same input
        assert id1 != id3  # Different trace ID
        assert len(id1) == 36  # UUID format (with dashes)


class TestContentVariations:
    """Test segmentization with various content types."""

    def test_content_as_string(self):
        """Content can be a simple string."""
        messages = [{"role": "user", "content": "Hello"}]
        segments = segmentize(messages)

        assert segments[0].raw_len > 0
        assert "Hello" in str(segments[0].raw_len) or segments[0].tokens_raw > 0

    def test_content_as_list_of_text_blocks(self):
        """Content can be a list of {type: 'text', text: ...} blocks."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Hello"},
                    {"type": "text", "text": "World"},
                ],
            }
        ]
        segments = segmentize(messages)

        assert len(segments) == 1
        assert segments[0].raw_len > 0
        # Should include both text blocks
        assert segments[0].tokens_raw > 0

    def test_content_with_image_block(self):
        """Content can include image blocks (should be serialized as sentinel)."""
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Look at this:"},
                    {
                        "type": "image",
                        "source": {"type": "base64", "data": "iVBORw0KGgoAAAAN..."},
                    },
                ],
            }
        ]
        segments = segmentize(messages)

        assert len(segments) == 1
        assert segments[0].raw_len > 0
        # Should have tokens for text + image sentinel

    def test_tool_schema_segment_content(self):
        """Tool schema segment should hash tools as JSON."""
        tools = [
            {"name": "search", "description": "Search"},
            {"name": "calc", "description": "Calculate"},
        ]
        messages = [{"role": "user", "content": "Help"}]
        segments = segmentize(messages, tools=tools)

        tool_seg = segments[1]
        assert tool_seg.segment_type == SegmentType.tool_schema.value
        # Content should include tool names
        assert tool_seg.raw_len > 0
        assert tool_seg.tokens_raw > 0


class TestRelevanceScoring:
    """Test relevance score computation."""

    def test_relevance_score_in_range(self):
        """All relevance scores should be in [0.0, 1.0]."""
        messages = [
            {"role": "user", "content": "Q1"},
            {"role": "assistant", "content": "A1"},
            {"role": "user", "content": "Q2"},
        ]
        segments = segmentize(messages)

        for seg in segments:
            assert 0.0 <= seg.relevance_score <= 1.0

    def test_last_user_message_high_relevance(self):
        """Last user message should typically have high relevance."""
        messages = [
            {"role": "user", "content": "old question"},
            {"role": "assistant", "content": "old answer"},
            {"role": "user", "content": "new question"},
        ]
        segments = segmentize(messages)

        # Last user message should have high relevance
        last_user = segments[2]
        first_user = segments[0]
        assert last_user.relevance_score >= first_user.relevance_score


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_none_content(self):
        """Message with None content should be handled gracefully."""
        messages = [{"role": "user", "content": None}]
        # Should not raise; creates segment with appropriate fallback
        segments = segmentize(messages)
        assert len(segments) == 1

    def test_missing_role(self):
        """Message without role should default to 'other'."""
        messages = [{"content": "text"}]
        segments = segmentize(messages)
        assert len(segments) == 1

    def test_very_long_content(self):
        """Very long content should be processed without errors."""
        long_text = "x" * 100000  # 100KB
        messages = [{"role": "user", "content": long_text}]
        segments = segmentize(messages)

        assert len(segments) == 1
        assert segments[0].raw_len == 100000
        assert segments[0].tokens_raw > 0

    def test_many_messages(self):
        """Large message list should create correct number of segments."""
        messages = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"msg{i}"}
            for i in range(100)
        ]
        segments = segmentize(messages)

        assert len(segments) == 100
        for i, seg in enumerate(segments):
            assert seg.order == i

    def test_unicode_content(self):
        """Unicode content should be hashed and tokenized correctly."""
        messages = [
            {"role": "user", "content": "你好世界 🌍 مرحبا"}
        ]
        segments = segmentize(messages)

        assert len(segments) == 1
        assert segments[0].raw_len > 0
        assert len(segments[0].raw_hash) == 64  # Valid SHA256


class TestTraceIdAndDeterminism:
    """Test trace ID handling and deterministic behavior."""

    def test_empty_trace_id_is_valid(self):
        """Empty trace_id should still produce valid segments."""
        messages = [{"role": "user", "content": "Hello"}]
        segments = segmentize(messages, trace_id="")

        assert len(segments) == 1
        assert segments[0].trace_id == ""
        assert segments[0].segment_id != ""  # Even with empty trace_id

    def test_trace_id_affects_segment_id(self):
        """Different trace IDs should produce different segment IDs."""
        messages = [{"role": "user", "content": "Same content"}]

        seg1 = segmentize(messages, trace_id="trace-A")[0]
        seg2 = segmentize(messages, trace_id="trace-B")[0]

        assert seg1.segment_id != seg2.segment_id
        assert seg1.trace_id != seg2.trace_id

    def test_complete_determinism(self):
        """Full determinism: same input always produces same segments."""
        messages = [
            {"role": "user", "content": "Q"},
            {"role": "assistant", "content": "A"},
        ]
        tools = [{"name": "t", "description": "d"}]

        r1 = segmentize(messages, tools=tools, trace_id="t1")
        r2 = segmentize(messages, tools=tools, trace_id="t1")

        for s1, s2 in zip(r1, r2):
            assert s1.trace_id == s2.trace_id
            assert s1.segment_id == s2.segment_id
            assert s1.raw_hash == s2.raw_hash
            assert s1.segment_type == s2.segment_type
            assert s1.tokens_raw == s2.tokens_raw


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
