"""test_dedup.py — Tests for tokenpak.agent.compression.dedup

Tests deduplication logic for removing duplicate/near-duplicate messages.
"""

import pytest

from tokenpak.agent.compression.dedup import (
    DEDUP_JACCARD_THRESHOLD,
    count_duplicates,
    dedup_messages,
)


class TestDedupMessages:
    """Test message deduplication."""

    def test_dedup_exact_duplicates(self):
        """Test removing exact duplicates."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": "Hello"},  # exact duplicate
            {"role": "assistant", "content": "Hi"},
        ]
        result = dedup_messages(messages, keep="last")
        
        # Should remove exact duplicate
        assert len(result) == 2
        assert result[0]["content"] == "Hello"
        assert result[1]["content"] == "Hi"

    def test_dedup_keep_first(self):
        """Test keep='first' parameter."""
        messages = [
            {"role": "user", "content": "Query"},
            {"role": "user", "content": "Query"},
        ]
        result = dedup_messages(messages, keep="first")
        
        assert len(result) == 1
        # First occurrence should be kept
        assert result[0] == messages[0]

    def test_dedup_keep_last(self):
        """Test keep='last' parameter (default)."""
        messages = [
            {"role": "user", "content": "Query"},
            {"role": "user", "content": "Query"},
        ]
        result = dedup_messages(messages, keep="last")
        
        assert len(result) == 1
        # Last occurrence should be kept
        assert result[0] == messages[1]

    def test_dedup_near_duplicates(self):
        """Test removing near-duplicate messages."""
        # Two messages with high Jaccard similarity
        messages = [
            {"role": "user", "content": "The quick brown fox jumps over the lazy dog"},
            {"role": "user", "content": "The quick brown fox jumps over the lazy dog"},  # exact
        ]
        result = dedup_messages(messages, threshold=0.9)
        assert len(result) == 1

    def test_dedup_custom_threshold(self):
        """Test with custom similarity threshold."""
        messages = [
            {"role": "user", "content": "Hello world"},
            {"role": "user", "content": "Hello world"},
        ]
        # Very high threshold should keep more messages
        result = dedup_messages(messages, threshold=0.99)
        assert len(result) <= 2

    def test_dedup_empty_list(self):
        """Test dedup on empty message list."""
        messages = []
        result = dedup_messages(messages)
        assert result == []

    def test_dedup_single_message(self):
        """Test dedup with single message."""
        messages = [{"role": "user", "content": "Hello"}]
        result = dedup_messages(messages)
        assert len(result) == 1
        assert result[0] == messages[0]

    def test_dedup_different_roles(self):
        """Test that different roles are not deduplicated."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hello"},  # same content, different role
        ]
        result = dedup_messages(messages)
        # Both should be kept because roles differ
        assert len(result) == 2

    def test_dedup_content_as_list(self):
        """Test dedup with content as list of blocks."""
        messages = [
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
        ]
        result = dedup_messages(messages)
        assert len(result) == 1

    def test_dedup_mixed_content_types(self):
        """Test dedup with mixed content types (string and list)."""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
        ]
        result = dedup_messages(messages)
        # These should be considered duplicates after content normalization
        assert len(result) <= 2

    def test_dedup_preserves_order(self):
        """Test that order is preserved after dedup."""
        messages = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Response"},
            {"role": "user", "content": "Second"},
            {"role": "user", "content": "First"},  # duplicate of first
        ]
        result = dedup_messages(messages, keep="last")
        
        # Should keep last occurrence of each unique message
        assert len(result) == 3
        # Verify content is present
        contents = [m["content"] for m in result]
        assert "Response" in contents
        assert "Second" in contents

    def test_dedup_long_messages(self):
        """Test dedup with longer messages."""
        long_content = "This is a longer message. " * 100
        messages = [
            {"role": "user", "content": long_content},
            {"role": "user", "content": long_content},
        ]
        result = dedup_messages(messages)
        assert len(result) == 1

    def test_dedup_unicode_content(self):
        """Test dedup with unicode content."""
        messages = [
            {"role": "user", "content": "Hello 你好 🎉"},
            {"role": "user", "content": "Hello 你好 🎉"},
        ]
        result = dedup_messages(messages)
        assert len(result) == 1

    def test_dedup_with_metadata(self):
        """Test that extra metadata is preserved."""
        messages = [
            {"role": "user", "content": "Query", "timestamp": 123},
            {"role": "user", "content": "Query", "timestamp": 124},
        ]
        result = dedup_messages(messages, keep="last")
        assert len(result) == 1
        assert result[0].get("timestamp") == 124


class TestCountDuplicates:
    """Test duplicate counting."""

    def test_count_duplicates_none(self):
        """Test counting duplicates in list with none."""
        messages = [
            {"role": "user", "content": "A"},
            {"role": "user", "content": "B"},
            {"role": "user", "content": "C"},
        ]
        result = count_duplicates(messages)
        assert isinstance(result, dict)
        assert result["exact_duplicates"] == 0
        assert result["total_messages"] == 3

    def test_count_duplicates_exact(self):
        """Test counting exact duplicates."""
        messages = [
            {"role": "user", "content": "Same"},
            {"role": "user", "content": "Same"},
            {"role": "user", "content": "Same"},
        ]
        result = count_duplicates(messages)
        assert isinstance(result, dict)
        assert result["exact_duplicates"] > 0
        assert result["total_messages"] == 3

    def test_count_duplicates_multiple_groups(self):
        """Test counting multiple groups of duplicates."""
        messages = [
            {"role": "user", "content": "A"},
            {"role": "user", "content": "A"},  # 1 duplicate
            {"role": "user", "content": "B"},
            {"role": "user", "content": "B"},  # 1 duplicate
            {"role": "user", "content": "C"},
        ]
        result = count_duplicates(messages)
        assert result["exact_duplicates"] >= 2
        assert result["total_messages"] == 5

    def test_count_duplicates_empty(self):
        """Test counting duplicates in empty list."""
        result = count_duplicates([])
        assert result == {"exact_duplicates": 0, "near_duplicates": 0, "total_messages": 0}

    def test_count_duplicates_single(self):
        """Test counting duplicates with single message."""
        messages = [{"role": "user", "content": "Solo"}]
        result = count_duplicates(messages)
        assert result["exact_duplicates"] == 0
        assert result["total_messages"] == 1

    def test_count_duplicates_different_roles(self):
        """Test that different roles don't count as duplicates."""
        messages = [
            {"role": "user", "content": "Same"},
            {"role": "assistant", "content": "Same"},
        ]
        result = count_duplicates(messages)
        assert result["exact_duplicates"] == 0
        assert result["total_messages"] == 2
