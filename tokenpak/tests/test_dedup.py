"""
Unit tests for compression/dedup.py — request deduplication logic.

Covers initialization (constants), core transform (dedup_messages),
helper functions, and edge cases (empty input, unicode, large input).
"""

from __future__ import annotations

from tokenpak.compression.dedup import (
    DEDUP_JACCARD_THRESHOLD,
    _content_to_str,
    _jaccard,
    _ngrams,
    _sha256,
    count_duplicates,
    dedup_messages,
)

# ── Constants ─────────────────────────────────────────────────────────────────


class TestConstants:
    def test_default_threshold_in_range(self):
        assert 0.0 < DEDUP_JACCARD_THRESHOLD < 1.0

    def test_default_threshold_value(self):
        # Spec says 90% character 4-gram overlap
        assert DEDUP_JACCARD_THRESHOLD == 0.90


# ── _content_to_str ───────────────────────────────────────────────────────────


class TestContentToStr:
    def test_plain_string_passthrough(self):
        assert _content_to_str("hello") == "hello"

    def test_empty_string(self):
        assert _content_to_str("") == ""

    def test_list_text_blocks_joined(self):
        content = [{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]
        result = _content_to_str(content)
        assert "a" in result and "b" in result

    def test_list_non_text_block_json_dumped(self):
        content = [{"type": "image_url", "url": "http://x"}]
        result = _content_to_str(content)
        assert "image_url" in result

    def test_list_missing_text_key(self):
        content = [{"type": "text"}, {"type": "text", "text": "hi"}]
        result = _content_to_str(content)
        assert "hi" in result

    def test_list_non_dict_items_stringified(self):
        result = _content_to_str(["abc", 42])
        assert "abc" in result

    def test_dict_content_json_encoded(self):
        result = _content_to_str({"k": "v"})
        assert "k" in result and "v" in result

    def test_unicode_string(self):
        result = _content_to_str("こんにちは")
        assert "こんにちは" in result

    def test_unicode_in_text_block(self):
        content = [{"type": "text", "text": "日本語"}]
        assert "日本語" in _content_to_str(content)


# ── _sha256 ───────────────────────────────────────────────────────────────────


class TestSha256:
    def test_returns_64_hex_chars(self):
        assert len(_sha256("test")) == 64

    def test_deterministic(self):
        assert _sha256("same") == _sha256("same")

    def test_distinct_inputs(self):
        assert _sha256("a") != _sha256("b")

    def test_empty_string(self):
        assert len(_sha256("")) == 64

    def test_unicode_input(self):
        # Should not raise
        h = _sha256("emoji 🐰")
        assert len(h) == 64


# ── _ngrams ───────────────────────────────────────────────────────────────────


class TestNgrams:
    def test_basic_4grams(self):
        result = _ngrams("hello")
        assert "hell" in result
        assert "ello" in result

    def test_string_shorter_than_n(self):
        assert _ngrams("hi", n=4) == set()

    def test_empty_string(self):
        assert _ngrams("") == set()

    def test_exact_length_string(self):
        assert _ngrams("abcd", n=4) == {"abcd"}

    def test_custom_n(self):
        result_2 = _ngrams("abcde", n=2)
        result_3 = _ngrams("abcde", n=3)
        assert len(result_2) > len(result_3)


# ── _jaccard ──────────────────────────────────────────────────────────────────


class TestJaccard:
    def test_identical_strings(self):
        assert _jaccard("hello world", "hello world") == 1.0

    def test_two_empty_strings(self):
        assert _jaccard("", "") == 1.0

    def test_one_empty_string(self):
        assert _jaccard("", "hello") == 0.0
        assert _jaccard("hello", "") == 0.0

    def test_symmetric(self):
        assert _jaccard("abc", "xyz") == _jaccard("xyz", "abc")

    def test_completely_different(self):
        sim = _jaccard("abcd", "wxyz")
        assert sim == 0.0

    def test_partial_overlap_in_range(self):
        # "abcdefg" and "abcdxyz" share the 4-gram "abcd" → Jaccard = 1/7 ≈ 0.14
        sim = _jaccard("abcdefg", "abcdxyz")
        assert 0.0 < sim < 1.0


# ── dedup_messages — exact deduplication ─────────────────────────────────────


class TestDedupExact:
    def test_no_duplicates_unchanged(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        assert dedup_messages(msgs) == msgs

    def test_exact_dup_keep_last(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "hello"},
        ]
        result = dedup_messages(msgs, keep="last")
        assert len(result) == 1

    def test_exact_dup_keep_first(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "hello"},
        ]
        result = dedup_messages(msgs, keep="first")
        assert len(result) == 1

    def test_three_exact_dupes_collapse_to_one(self):
        msgs = [{"role": "user", "content": "x"} for _ in range(3)]
        assert len(dedup_messages(msgs)) == 1

    def test_different_roles_not_deduped(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hello"},
        ]
        assert len(dedup_messages(msgs)) == 2

    def test_empty_list(self):
        assert dedup_messages([]) == []


# ── dedup_messages — near-duplicate deduplication ────────────────────────────


class TestDedupNear:
    def test_identical_passes_threshold(self):
        content = "The quick brown fox jumps over the lazy dog. " * 3
        msgs = [
            {"role": "user", "content": content},
            {"role": "user", "content": content},
        ]
        result = dedup_messages(msgs, threshold=0.90)
        assert len(result) == 1

    def test_very_different_strings_kept(self):
        msgs = [
            {"role": "user", "content": "abcdefgh"},
            {"role": "user", "content": "wxyzijkl"},
        ]
        result = dedup_messages(msgs, threshold=0.90)
        assert len(result) == 2

    def test_threshold_one_disables_near_dedup(self):
        msgs = [
            {"role": "user", "content": "hello world"},
            {"role": "user", "content": "hello worrd"},
        ]
        result = dedup_messages(msgs, threshold=1.0)
        assert len(result) == 2

    def test_different_roles_not_near_deduped(self):
        content = "very long repeated content for testing purposes " * 5
        msgs = [
            {"role": "user", "content": content},
            {"role": "assistant", "content": content},
        ]
        result = dedup_messages(msgs, threshold=0.90)
        assert len(result) == 2

    def test_list_content_deduped(self):
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
            {"role": "user", "content": [{"type": "text", "text": "hello"}]},
        ]
        assert len(dedup_messages(msgs)) == 1


# ── dedup_messages — edge cases ───────────────────────────────────────────────


class TestDedupEdgeCases:
    def test_single_message_unchanged(self):
        msgs = [{"role": "user", "content": "solo"}]
        assert dedup_messages(msgs) == msgs

    def test_missing_content_field_no_crash(self):
        msgs = [{"role": "user"}, {"role": "user"}]
        result = dedup_messages(msgs)
        assert len(result) >= 1

    def test_missing_role_field_no_crash(self):
        msgs = [{"content": "hi"}, {"content": "hi"}]
        result = dedup_messages(msgs)
        assert len(result) == 1

    def test_unicode_content(self):
        msgs = [
            {"role": "user", "content": "日本語テスト"},
            {"role": "user", "content": "日本語テスト"},
        ]
        assert len(dedup_messages(msgs)) == 1

    def test_large_list_deduplication(self):
        # 200 messages cycling through 5 unique values
        msgs = [{"role": "user", "content": f"msg-{i % 5}"} for i in range(200)]
        result = dedup_messages(msgs)
        assert len(result) == 5

    def test_order_preserved_keep_last(self):
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "response"},
            {"role": "user", "content": "third"},
        ]
        result = dedup_messages(msgs, threshold=1.0)
        assert [m["content"] for m in result] == ["first", "response", "third"]

    def test_empty_string_content(self):
        msgs = [
            {"role": "user", "content": ""},
            {"role": "user", "content": ""},
        ]
        result = dedup_messages(msgs)
        assert len(result) == 1


# ── count_duplicates ──────────────────────────────────────────────────────────


class TestCountDuplicates:
    def test_empty_list(self):
        result = count_duplicates([])
        assert result == {"exact_duplicates": 0, "near_duplicates": 0, "total_messages": 0}

    def test_no_duplicates(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
        ]
        counts = count_duplicates(msgs)
        assert counts["exact_duplicates"] == 0
        assert counts["total_messages"] == 2

    def test_one_exact_duplicate(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "hello"},
        ]
        counts = count_duplicates(msgs)
        assert counts["exact_duplicates"] == 1

    def test_two_exact_duplicates(self):
        msgs = [{"role": "user", "content": "hello"}] * 3
        counts = count_duplicates(msgs)
        assert counts["exact_duplicates"] == 2

    def test_different_roles_not_counted(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hello"},
        ]
        counts = count_duplicates(msgs)
        assert counts["exact_duplicates"] == 0

    def test_total_messages_accurate(self):
        msgs = [{"role": "user", "content": f"msg-{i}"} for i in range(10)]
        counts = count_duplicates(msgs)
        assert counts["total_messages"] == 10

    def test_after_dedup_zero_exact_dupes(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "world"},
        ]
        deduped = dedup_messages(msgs)
        counts = count_duplicates(deduped)
        assert counts["exact_duplicates"] == 0
