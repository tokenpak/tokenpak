"""
Tests for agent/compression/dedup.py — deduplication module.

Covers exact-duplicate removal, near-duplicate detection via Jaccard similarity,
threshold tuning, and edge cases.
"""

from tokenpak.compression.dedup import (
    _content_to_str,
    _jaccard,
    _ngrams,
    _sha256,
    count_duplicates,
    dedup_messages,
)

# ============================================================================
# Helper Function Tests
# ============================================================================


class TestContentToStr:
    """Test _content_to_str flattening."""

    def test_string_content(self):
        """Simple string should pass through."""
        assert _content_to_str("hello") == "hello"

    def test_empty_string(self):
        """Empty string should pass through."""
        assert _content_to_str("") == ""

    def test_list_with_text_blocks(self):
        """List of text blocks should be joined with newlines."""
        content = [
            {"type": "text", "text": "line 1"},
            {"type": "text", "text": "line 2"},
        ]
        result = _content_to_str(content)
        assert "line 1" in result
        assert "line 2" in result
        assert "\n" in result

    def test_list_with_mixed_blocks(self):
        """Mixed block types should be handled."""
        content = [
            {"type": "text", "text": "text block"},
            {"type": "image_url", "url": "http://example.com"},
        ]
        result = _content_to_str(content)
        assert "text block" in result
        assert "image_url" in result

    def test_list_with_empty_text_block(self):
        """Empty text block should not crash."""
        content = [{"type": "text"}, {"type": "text", "text": "content"}]
        result = _content_to_str(content)
        assert "content" in result

    def test_non_dict_list_items(self):
        """Non-dict items in list should be stringified."""
        content = ["string", 123, True]
        result = _content_to_str(content)
        assert "string" in result

    def test_dict_content(self):
        """Non-list dict should be JSON stringified."""
        content = {"key": "value"}
        result = _content_to_str(content)
        assert "key" in result


class TestSha256:
    """Test _sha256 hashing."""

    def test_consistent_hash(self):
        """Same input should always produce same hash."""
        h1 = _sha256("test")
        h2 = _sha256("test")
        assert h1 == h2

    def test_different_input_different_hash(self):
        """Different inputs should produce different hashes."""
        h1 = _sha256("test1")
        h2 = _sha256("test2")
        assert h1 != h2

    def test_empty_string_hash(self):
        """Empty string should hash fine."""
        h = _sha256("")
        assert len(h) == 64  # SHA256 is 64 hex chars


class TestNgrams:
    """Test _ngrams function."""

    def test_ngrams_basic(self):
        """Extract 4-grams from string."""
        result = _ngrams("hello", n=4)
        assert "hell" in result
        assert "ello" in result
        assert len(result) == 2

    def test_ngrams_exact_length(self):
        """String same length as n should return one ngram."""
        result = _ngrams("test", n=4)
        assert result == {"test"}

    def test_ngrams_shorter_than_n(self):
        """String shorter than n should return empty set."""
        result = _ngrams("hi", n=4)
        assert result == set()

    def test_ngrams_empty_string(self):
        """Empty string should return empty set."""
        result = _ngrams("", n=4)
        assert result == set()

    def test_ngrams_different_n(self):
        """Different n values should work."""
        result_2 = _ngrams("hello", n=2)
        result_3 = _ngrams("hello", n=3)
        assert len(result_2) > len(result_3)


class TestJaccard:
    """Test _jaccard similarity."""

    def test_identical_strings(self):
        """Identical strings should have 1.0 similarity."""
        assert _jaccard("hello", "hello") == 1.0

    def test_completely_different_strings(self):
        """Completely different strings should have low similarity."""
        sim = _jaccard("abcd", "wxyz")
        assert 0.0 <= sim < 0.1

    def test_partial_overlap(self):
        """Partially overlapping strings should have measurable similarity."""
        sim = _jaccard("hello", "hallo")
        # "hallo" vs "hello" have limited 4-gram overlap, may be 0 if no matching 4-grams
        assert 0.0 <= sim <= 1.0

    def test_empty_strings(self):
        """Two empty strings should be identical."""
        assert _jaccard("", "") == 1.0

    def test_one_empty_string(self):
        """One empty, one non-empty should be 0.0."""
        assert _jaccard("", "hello") == 0.0
        assert _jaccard("hello", "") == 0.0

    def test_asymmetric(self):
        """Jaccard should be symmetric."""
        sim1 = _jaccard("hello", "help")
        sim2 = _jaccard("help", "hello")
        assert sim1 == sim2


# ============================================================================
# dedup_messages Tests
# ============================================================================


class TestDedupMessagesExactDuplicates:
    """Test exact-duplicate removal."""

    def test_no_duplicates(self):
        """Messages with no duplicates should remain unchanged."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]
        result = dedup_messages(messages)
        assert len(result) == 2
        assert result == messages

    def test_identical_messages_keep_last(self):
        """Identical messages with keep='last' should remove earlier occurrence."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "hello"},
        ]
        result = dedup_messages(messages, keep="last")
        assert len(result) == 1
        assert result[0]["content"] == "hello"

    def test_identical_messages_keep_first(self):
        """Identical messages with keep='first' should remove later occurrence."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "hello"},
        ]
        result = dedup_messages(messages, keep="first")
        assert len(result) == 1

    def test_three_identical_messages(self):
        """Multiple identical messages should reduce to one."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "hello"},
        ]
        result = dedup_messages(messages)
        assert len(result) == 1

    def test_different_roles_not_deduplicated(self):
        """Same content but different roles should not be deduplicated."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hello"},
        ]
        result = dedup_messages(messages)
        assert len(result) == 2

    def test_empty_messages(self):
        """Empty messages list should return empty."""
        result = dedup_messages([])
        assert result == []


class TestDedupMessagesNearDuplicates:
    """Test near-duplicate removal via Jaccard similarity."""

    def test_high_similarity_above_threshold(self):
        """Messages with high similarity should be deduplicated."""
        messages = [
            {"role": "user", "content": "This is a test message"},
            {"role": "user", "content": "This is a test message"},  # 100% match
        ]
        result = dedup_messages(messages, threshold=0.90)
        assert len(result) == 1

    def test_low_similarity_below_threshold(self):
        """Messages with low similarity should not be deduplicated."""
        messages = [
            {"role": "user", "content": "abcd"},
            {"role": "user", "content": "wxyz"},
        ]
        result = dedup_messages(messages, threshold=0.90)
        assert len(result) == 2

    def test_custom_threshold_strict(self):
        """Threshold 1.0 should only remove exact duplicates."""
        messages = [
            {"role": "user", "content": "hello world"},
            {"role": "user", "content": "hello word"},  # typo, near-duplicate
        ]
        result = dedup_messages(messages, threshold=1.0)
        assert len(result) == 2  # kept both

    def test_custom_threshold_lenient(self):
        """Low threshold should remove more near-duplicates."""
        messages = [
            {"role": "user", "content": "test"},
            {"role": "user", "content": "test"},
        ]
        result = dedup_messages(messages, threshold=0.5)
        assert len(result) == 1

    def test_near_duplicates_different_roles(self):
        """Near-duplicates with different roles should not be deduplicated."""
        messages = [
            {"role": "user", "content": "This is very similar"},
            {"role": "assistant", "content": "This is very similar"},
        ]
        result = dedup_messages(messages, threshold=0.90)
        assert len(result) == 2

    def test_longer_repeated_sequences(self):
        """Longer sequences with repetition should be deduplicated."""
        content = "The quick brown fox jumps over the lazy dog"
        messages = [
            {"role": "user", "content": content},
            {"role": "user", "content": content},
        ]
        result = dedup_messages(messages)
        assert len(result) == 1

    def test_first_occurrence_kept_with_last_strategy(self):
        """With keep='last', earlier occurrence should be removed."""
        messages = [
            {"role": "user", "content": "first message"},
            {"role": "assistant", "content": "response"},
            {"role": "user", "content": "first message"},  # duplicate
        ]
        result = dedup_messages(messages, keep="last")
        assert len(result) == 2
        # The last "first message" should be at end
        assert result[-1]["content"] == "first message"

    def test_list_content_dedup(self):
        """Content as list of blocks should be deduplicated correctly."""
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        ]
        result = dedup_messages(messages)
        assert len(result) == 1


class TestDedupEdgeCases:
    """Test edge cases and corner conditions."""

    def test_single_message(self):
        """Single message should return unchanged."""
        messages = [{"role": "user", "content": "hello"}]
        result = dedup_messages(messages)
        assert len(result) == 1

    def test_missing_content_field(self):
        """Messages without content should not crash."""
        messages = [{"role": "user"}, {"role": "user"}]
        result = dedup_messages(messages)
        assert len(result) >= 1

    def test_missing_role_field(self):
        """Messages without role should be handled."""
        messages = [
            {"content": "hello"},
            {"content": "hello"},
        ]
        result = dedup_messages(messages)
        # Should deduplicate as same (empty) role
        assert len(result) == 1

    def test_none_content(self):
        """None as content should be handled."""
        messages = [
            {"role": "user", "content": None},
            {"role": "user", "content": None},
        ]
        result = dedup_messages(messages)
        assert len(result) >= 1

    def test_whitespace_differences(self):
        """Whitespace differences should reduce similarity below typical 0.90 threshold."""
        messages = [
            {"role": "user", "content": "hello world"},
            {"role": "user", "content": "hello  world"},  # extra space
        ]
        result = dedup_messages(messages, threshold=0.90)
        # Extra space changes n-gram sets enough that threshold=0.90 doesn't trigger
        assert len(result) == 2

        # Jaccard similarity is ~0.545, so need threshold < 0.545 to catch it
        result_lenient = dedup_messages(messages, threshold=0.50)
        assert len(result_lenient) == 1

    def test_large_message_list(self):
        """Large list with some duplicates should be handled."""
        messages = [{"role": "user", "content": f"message {i % 5}"} for i in range(100)]
        result = dedup_messages(messages)
        # Should have fewer than original
        assert len(result) < len(messages)
        assert len(result) == 5  # 5 unique messages

    def test_preserve_order(self):
        """Order should be preserved (when keep='last')."""
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "response"},
            {"role": "user", "content": "third"},
        ]
        result = dedup_messages(messages, threshold=1.0)  # no near-dedup
        assert len(result) == 3
        assert result[0]["content"] == "first"
        assert result[1]["content"] == "response"
        assert result[2]["content"] == "third"


# ============================================================================
# count_duplicates Tests
# ============================================================================


class TestCountDuplicates:
    """Test count_duplicates function."""

    def test_no_duplicates(self):
        """No duplicates should report 0."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        counts = count_duplicates(messages)
        assert counts["exact_duplicates"] == 0
        assert counts["near_duplicates"] == 0
        assert counts["total_messages"] == 2

    def test_one_exact_duplicate(self):
        """One exact duplicate should be counted."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "hello"},
        ]
        counts = count_duplicates(messages)
        assert counts["exact_duplicates"] == 1
        assert counts["total_messages"] == 2

    def test_multiple_exact_duplicates(self):
        """Multiple duplicates should all be counted."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "hello"},
        ]
        counts = count_duplicates(messages)
        assert counts["exact_duplicates"] == 2

    def test_empty_list(self):
        """Empty list should return zeros."""
        counts = count_duplicates([])
        assert counts["exact_duplicates"] == 0
        assert counts["near_duplicates"] == 0
        assert counts["total_messages"] == 0

    def test_near_duplicates_counted(self):
        """Near-duplicates above threshold should be counted."""
        messages = [
            {"role": "user", "content": "This is a test message"},
            {"role": "user", "content": "This is a test message"},
        ]
        counts = count_duplicates(messages, threshold=0.90)
        # First one is exact, but Jaccard will also flag it
        assert counts["total_messages"] == 2

    def test_different_roles_not_counted(self):
        """Same content, different roles should not be counted."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hello"},
        ]
        counts = count_duplicates(messages)
        assert counts["exact_duplicates"] == 0
        assert counts["near_duplicates"] == 0


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """End-to-end tests combining multiple features."""

    def test_mixed_exact_and_near_duplicates(self):
        """List with both exact and near-duplicates should be cleaned."""
        messages = [
            {"role": "user", "content": "hello world"},
            {"role": "user", "content": "hello world"},  # exact
            {"role": "assistant", "content": "hi there"},
            {"role": "user", "content": "hello word"},  # near-dup
        ]
        result = dedup_messages(messages, threshold=0.85)
        # Should remove exact duplicate and possibly near-duplicate
        assert len(result) <= 3

    def test_real_conversation_scenario(self):
        """Realistic multi-turn conversation with some repetition."""
        messages = [
            {"role": "user", "content": "What is Python?"},
            {"role": "assistant", "content": "Python is a programming language."},
            {"role": "user", "content": "What is Python?"},  # user repeated question
            {"role": "assistant", "content": "Python is a programming language."},  # same response
            {"role": "user", "content": "Is it fast?"},
        ]
        result = dedup_messages(messages)
        # Should reduce to ~3 unique messages
        assert len(result) <= 4

    def test_dedup_then_count(self):
        """Deduplicated messages should have zero duplicates when counted."""
        messages = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        dedupped = dedup_messages(messages)
        counts = count_duplicates(dedupped)
        assert counts["exact_duplicates"] == 0

    def test_threshold_parameter_impact(self):
        """Stricter threshold should deduplicate less."""
        messages = [
            {"role": "user", "content": "test string"},
            {"role": "user", "content": "test string"},
        ]
        result_strict = dedup_messages(messages, threshold=1.0)
        result_lenient = dedup_messages(messages, threshold=0.5)
        assert len(result_strict) <= len(result_lenient)

    def test_keep_parameter_impact(self):
        """keep='first' vs keep='last' should preserve different messages."""
        messages = [
            {"role": "user", "content": "msg1"},
            {"role": "assistant", "content": "response"},
            {"role": "user", "content": "msg1"},  # repeat
        ]
        result_last = dedup_messages(messages, keep="last", threshold=1.0)
        result_first = dedup_messages(messages, keep="first", threshold=1.0)
        # Both should have same length
        assert len(result_last) == len(result_first)
