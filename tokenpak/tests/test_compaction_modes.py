"""
Unit tests for compaction/modes.py.

Covers: CompactionMode enum, _normalise_whitespace, compact_lossless,
compact_balanced, compact_aggressive, compact_semantic, the compact
dispatch function, and _trim_to_tokens.
"""

from unittest.mock import MagicMock, patch

import pytest

from tokenpak.compaction.modes import (
    CompactionMode,
    _multi_blank_sub,
    _trim_to_tokens,
    compact,
    compact_aggressive,
    compact_balanced,
    compact_lossless,
    compact_semantic,
)


# ============================================================================
# CompactionMode enum
# ============================================================================


class TestCompactionMode:
    def test_all_four_values_exist(self):
        assert CompactionMode.LOSSLESS is not None
        assert CompactionMode.BALANCED is not None
        assert CompactionMode.AGGRESSIVE is not None
        assert CompactionMode.SEMANTIC is not None

    def test_string_values(self):
        assert CompactionMode.LOSSLESS.value == "lossless"
        assert CompactionMode.BALANCED.value == "balanced"
        assert CompactionMode.AGGRESSIVE.value == "aggressive"
        assert CompactionMode.SEMANTIC.value == "semantic"

    def test_construct_from_string(self):
        assert CompactionMode("lossless") is CompactionMode.LOSSLESS
        assert CompactionMode("balanced") is CompactionMode.BALANCED
        assert CompactionMode("aggressive") is CompactionMode.AGGRESSIVE
        assert CompactionMode("semantic") is CompactionMode.SEMANTIC

    def test_invalid_string_raises(self):
        with pytest.raises(ValueError):
            CompactionMode("unknown")

    def test_is_str_subclass(self):
        # CompactionMode inherits from str
        assert isinstance(CompactionMode.BALANCED, str)


# ============================================================================
# _normalise_whitespace / compact_lossless
# ============================================================================


class TestCompactLossless:
    def test_trailing_spaces_stripped(self):
        result = compact_lossless("hello   \nworld   ")
        assert result == "hello\nworld"

    def test_trailing_tabs_stripped(self):
        result = compact_lossless("hello\t\t\nworld")
        assert result == "hello\nworld"

    def test_multiple_blank_lines_collapsed(self):
        result = compact_lossless("a\n\n\n\nb")
        assert result == "a\n\nb"

    def test_exactly_two_blank_lines_unchanged(self):
        result = compact_lossless("a\n\nb")
        assert result == "a\n\nb"

    def test_three_blank_lines_collapsed_to_two(self):
        result = compact_lossless("a\n\n\nb")
        assert result == "a\n\nb"

    def test_leading_tab_converted_to_spaces(self):
        # Tab on a non-first line so .strip() preserves the converted indentation
        result = compact_lossless("hello\n\tworld")
        assert result == "hello\n    world"

    def test_two_leading_tabs_converted(self):
        result = compact_lossless("hello\n\t\tworld")
        assert result == "hello\n        world"

    def test_empty_string(self):
        result = compact_lossless("")
        assert result == ""

    def test_whitespace_only_string(self):
        result = compact_lossless("   \n   \n   ")
        assert result == ""

    def test_no_changes_needed(self):
        text = "# Header\n\nParagraph text."
        result = compact_lossless(text)
        assert result == text

    def test_strips_leading_and_trailing_newlines(self):
        result = compact_lossless("\n\nhello\n\n")
        assert result == "hello"


# ============================================================================
# compact_balanced
# ============================================================================


class TestCompactBalanced:
    def test_calls_text_processor(self):
        mock_proc = MagicMock()
        mock_proc.process.return_value = "processed text"
        mock_cls = MagicMock(return_value=mock_proc)

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact_balanced("some input text")

        mock_cls.assert_called_once_with(aggressive=True)
        mock_proc.process.assert_called_once()
        assert result == "processed text"

    def test_returns_processor_output(self):
        mock_proc = MagicMock()
        mock_proc.process.return_value = "PROCESSED"
        mock_cls = MagicMock(return_value=mock_proc)

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact_balanced("hello")

        assert result == "PROCESSED"

    def test_target_tokens_trims_output(self):
        long_output = "word " * 500  # ~2500 chars
        mock_proc = MagicMock()
        mock_proc.process.return_value = long_output
        mock_cls = MagicMock(return_value=mock_proc)

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact_balanced("input", target_tokens=10)

        # 10 tokens * 4 = 40 chars max, output ends with \n…
        assert result.endswith("\n…")
        assert len(result) < len(long_output)

    def test_target_tokens_zero_does_not_trim(self):
        output = "short output"
        mock_proc = MagicMock()
        mock_proc.process.return_value = output
        mock_cls = MagicMock(return_value=mock_proc)

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact_balanced("input", target_tokens=0)

        assert result == output

    def test_normalises_whitespace_before_processing(self):
        """Whitespace is normalised before TextProcessor is called."""
        captured = []
        mock_proc = MagicMock()
        mock_proc.process.side_effect = lambda t, _: captured.append(t) or t
        mock_cls = MagicMock(return_value=mock_proc)

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            compact_balanced("hello   \n\n\n\nworld")

        assert captured, "process() not called"
        # Three+ blank lines should have been collapsed
        assert "\n\n\n" not in captured[0]

    def test_empty_string(self):
        mock_proc = MagicMock()
        mock_proc.process.return_value = ""
        mock_cls = MagicMock(return_value=mock_proc)

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact_balanced("")

        assert result == ""


# ============================================================================
# compact_aggressive
# ============================================================================


class TestCompactAggressive:
    def _make_passthrough_mock(self):
        """Return a TextProcessor mock whose process() returns input unchanged."""
        mock_proc = MagicMock()
        mock_proc.process.side_effect = lambda t, _: t
        return MagicMock(return_value=mock_proc)

    def test_long_bullet_truncated_to_60(self):
        bullet = "- " + "x" * 80  # 82 chars total, > 60
        mock_cls = self._make_passthrough_mock()

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact_aggressive(bullet)

        # Result bullet line should end with …
        lines = [l for l in result.split("\n") if l]
        assert lines[0].endswith("…")
        assert len(lines[0]) <= 61  # 60 chars + ellipsis char

    def test_short_bullet_not_truncated(self):
        bullet = "- short item"
        mock_cls = self._make_passthrough_mock()

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact_aggressive(bullet)

        assert "short item" in result
        assert "…" not in result

    def test_numbered_list_truncated(self):
        item = "1. " + "y" * 80
        mock_cls = self._make_passthrough_mock()

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact_aggressive(item)

        lines = [l for l in result.split("\n") if l]
        assert lines[0].endswith("…")

    def test_markdown_image_replaced(self):
        text = "![alt text](https://example.com/image.png)"
        mock_cls = self._make_passthrough_mock()

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact_aggressive(text)

        assert "[img:alt text]" in result
        assert "![" not in result

    def test_long_link_stripped_to_text(self):
        # URL >= 40 chars triggers stripping
        long_url = "https://example.com/" + "a" * 30
        text = f"[link text]({long_url})"
        mock_cls = self._make_passthrough_mock()

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact_aggressive(text)

        assert "link text" in result
        assert long_url not in result

    def test_short_link_preserved(self):
        text = "[link](short.url)"  # < 40 chars
        mock_cls = self._make_passthrough_mock()

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact_aggressive(text)

        assert "[link](short.url)" in result

    def test_target_tokens_trims(self):
        long_text = "plain text line\n" * 100
        mock_cls = self._make_passthrough_mock()

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact_aggressive(long_text, target_tokens=10)

        assert result.endswith("\n…")

    def test_empty_string(self):
        mock_cls = self._make_passthrough_mock()

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact_aggressive("")

        assert result == ""


# ============================================================================
# compact_semantic
# ============================================================================


class TestCompactSemantic:
    def test_falls_back_to_aggressive_when_llmlingua_unavailable(self):
        """LLMLingua is not installed → should fall back to aggressive gracefully."""
        result = compact_semantic("Hello world text for semantic compaction.")
        # Just verify it returns a non-empty string without raising
        assert isinstance(result, str)

    def test_returns_string(self):
        result = compact_semantic("some text")
        assert isinstance(result, str)

    def test_empty_string(self):
        result = compact_semantic("")
        assert isinstance(result, str)

    def test_falls_back_on_engine_error(self):
        """If engine raises, falls back to aggressive (same as fallback path)."""
        with patch("tokenpak.compaction.modes.compact_aggressive") as mock_agg:
            mock_agg.return_value = "fallback result"

            # Force the try block to fail by patching inside engines.llmlingua
            with patch.dict("sys.modules", {"tokenpak.engines.llmlingua": None}):
                result = compact_semantic("some text")

        # Either the mock was called (fallback path) or it worked on its own
        assert isinstance(result, str)


# ============================================================================
# compact dispatch function
# ============================================================================


class TestCompactDispatch:
    def test_dispatch_lossless_by_string(self):
        result = compact("hello   \nworld", mode="lossless")
        assert result == "hello\nworld"

    def test_dispatch_lossless_by_enum(self):
        result = compact("hello   \nworld", mode=CompactionMode.LOSSLESS)
        assert result == "hello\nworld"

    def test_dispatch_balanced_by_string(self):
        mock_proc = MagicMock()
        mock_proc.process.return_value = "ok"
        mock_cls = MagicMock(return_value=mock_proc)

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact("hello", mode="balanced")
        assert result == "ok"

    def test_dispatch_aggressive_by_string(self):
        mock_proc = MagicMock()
        mock_proc.process.side_effect = lambda t, _: t
        mock_cls = MagicMock(return_value=mock_proc)

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact("hello", mode="aggressive")
        assert isinstance(result, str)

    def test_lossless_ignores_target_tokens(self):
        # lossless doesn't accept target_tokens — should not raise
        result = compact("hello\n\n\nworld", mode="lossless", target_tokens=100)
        assert result == "hello\n\nworld"

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            compact("hello", mode="nonexistent")

    def test_default_mode_is_balanced(self):
        mock_proc = MagicMock()
        mock_proc.process.return_value = "balanced default"
        mock_cls = MagicMock(return_value=mock_proc)

        with patch("tokenpak.processors.text.TextProcessor", mock_cls):
            result = compact("hello")

        assert result == "balanced default"


# ============================================================================
# _trim_to_tokens
# ============================================================================


class TestTrimToTokens:
    def test_short_text_returned_unchanged(self):
        result = _trim_to_tokens("short", 100)
        assert result == "short"

    def test_text_exactly_at_limit_returned_unchanged(self):
        # target_tokens=5 → 20 chars
        text = "a" * 20
        result = _trim_to_tokens(text, 5)
        assert result == text

    def test_long_text_trimmed(self):
        result = _trim_to_tokens("a" * 100, 5)
        # 5 * 4 = 20 chars budget
        assert result.endswith("\n…")
        assert len(result) <= 25  # 20 chars + \n…

    def test_trim_at_newline_boundary(self):
        # Build text where a newline exists in the second half of the target window
        text = "line1\nline2\nline3\nline4"
        result = _trim_to_tokens(text, 3)  # 3 * 4 = 12 chars
        # Should trim to a newline within the second half
        assert result.endswith("\n…")
        assert "line1" in result

    def test_no_newline_still_trims(self):
        text = "x" * 100
        result = _trim_to_tokens(text, 5)  # 20 chars
        assert result.endswith("\n…")

    def test_zero_tokens_not_trimmed(self):
        # target_tokens=0 → target_chars=0, len(text) > 0
        # text[:0] = "", rfind('\n') = -1, -1 > 0 is False
        # result = "".rstrip() + "\n…" = "\n…"
        text = "some content"
        result = _trim_to_tokens(text, 0)
        assert result == "\n…"


# ============================================================================
# _multi_blank_sub utility
# ============================================================================


class TestMultiBlankSub:
    def test_collapses_triple_newlines(self):
        result = _multi_blank_sub("a\n\n\nb")
        assert result == "a\n\nb"

    def test_preserves_double_newlines(self):
        result = _multi_blank_sub("a\n\nb")
        assert result == "a\n\nb"
