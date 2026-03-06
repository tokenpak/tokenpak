"""Tests for CompressionRuleEngine (Phase 7C).

Covers all 5 RecipeType rules with ≥15 test cases using synthetic segments
modelled on real tool_output / retrieval / memory / assistant_context data.
"""
from __future__ import annotations

import pytest

from tokenpak.agent.compression.recipes import (
    CompressionRuleEngine,
    ContentSegment,
    RecipeType,
    PHRASE_MAP,
    _count_tokens,
    _TRUNCATE_TAIL_TOKEN_THRESHOLD,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_segment(content: str, seg_type: str = "tool_output") -> ContentSegment:
    return ContentSegment(raw_content=content, segment_type=seg_type)


def engine() -> CompressionRuleEngine:
    return CompressionRuleEngine()


# ---------------------------------------------------------------------------
# 1. RecipeType enum
# ---------------------------------------------------------------------------


def test_recipe_type_values():
    assert RecipeType.WHITESPACE_COLLAPSE.value == "whitespace_collapse"
    assert RecipeType.LIST_DEDUP.value == "list_dedup"
    assert RecipeType.PHRASE_SUBSTITUTION.value == "phrase_substitution"
    assert RecipeType.TRUNCATE_TAIL.value == "truncate_tail"
    assert RecipeType.BOILERPLATE_STRIP.value == "boilerplate_strip"


def test_recipe_type_all_five_exist():
    assert len(RecipeType) == 5


# ---------------------------------------------------------------------------
# 2. select_recipes — segment type → recipe mapping
# ---------------------------------------------------------------------------


def test_select_recipes_tool_output_small():
    seg = make_segment("short content", "tool_output")
    recipes = engine().select_recipes(seg)
    assert RecipeType.WHITESPACE_COLLAPSE in recipes
    assert RecipeType.TRUNCATE_TAIL not in recipes  # under threshold


def test_select_recipes_tool_output_large():
    # > 2000 tokens → 8004 chars = 2001 tokens
    big = "x " * 4002  # 8004 chars → 2001 tokens
    seg = make_segment(big, "tool_output")
    recipes = engine().select_recipes(seg)
    assert RecipeType.WHITESPACE_COLLAPSE in recipes
    assert RecipeType.TRUNCATE_TAIL in recipes


def test_select_recipes_retrieval():
    seg = make_segment("- item A\n- item B\n", "retrieval")
    recipes = engine().select_recipes(seg)
    assert RecipeType.LIST_DEDUP in recipes
    assert RecipeType.PHRASE_SUBSTITUTION in recipes


def test_select_recipes_memory():
    seg = make_segment("some memory block", "memory")
    recipes = engine().select_recipes(seg)
    assert RecipeType.BOILERPLATE_STRIP in recipes


def test_select_recipes_assistant_context():
    seg = make_segment("assistant context text", "assistant_context")
    recipes = engine().select_recipes(seg)
    assert RecipeType.BOILERPLATE_STRIP in recipes


def test_select_recipes_no_cross_contamination():
    """TRUNCATE_TAIL should not appear for retrieval segments."""
    big = "x " * 4001
    seg = make_segment(big, "retrieval")
    recipes = engine().select_recipes(seg)
    assert RecipeType.TRUNCATE_TAIL not in recipes


# ---------------------------------------------------------------------------
# 3. Whitespace collapse
# ---------------------------------------------------------------------------


def test_whitespace_collapse_five_newlines_to_two():
    content = "line A\n\n\n\n\nline B"
    seg = make_segment(content, "tool_output")
    result = engine().apply_recipes(seg, [RecipeType.WHITESPACE_COLLAPSE])
    assert "\n\n\n" not in result.raw_content
    assert "line A" in result.raw_content
    assert "line B" in result.raw_content


def test_whitespace_collapse_three_newlines_to_two():
    content = "a\n\n\nb"
    result = engine().apply_recipes(make_segment(content), [RecipeType.WHITESPACE_COLLAPSE])
    assert result.raw_content == "a\n\nb"


def test_whitespace_collapse_trailing_spaces_stripped():
    content = "hello   \nworld   \n"
    result = engine().apply_recipes(make_segment(content), [RecipeType.WHITESPACE_COLLAPSE])
    for line in result.raw_content.split("\n"):
        assert line == line.rstrip()


def test_whitespace_collapse_mid_line_spaces():
    content = "word1    word2    word3"
    result = engine().apply_recipes(make_segment(content), [RecipeType.WHITESPACE_COLLAPSE])
    assert "    " not in result.raw_content


def test_whitespace_collapse_preserves_indentation():
    content = "def foo():\n    return 1"
    result = engine().apply_recipes(make_segment(content), [RecipeType.WHITESPACE_COLLAPSE])
    # Leading 4-space indentation should be preserved (only mid-line extra spaces collapse)
    assert "    return 1" in result.raw_content


def test_whitespace_collapse_reduces_tokens():
    content = "a\n\n\n\nb\n\n\n\nc"
    seg = make_segment(content)
    result = engine().apply_recipes(seg, [RecipeType.WHITESPACE_COLLAPSE])
    assert result.raw_tokens <= seg.raw_tokens


# ---------------------------------------------------------------------------
# 4. List dedup
# ---------------------------------------------------------------------------


def test_list_dedup_removes_exact_duplicate():
    content = "- apple\n- banana\n- apple\n- cherry"
    result = engine().apply_recipes(
        make_segment(content, "retrieval"), [RecipeType.LIST_DEDUP]
    )
    lines = [l for l in result.raw_content.split("\n") if l.strip()]
    apple_lines = [l for l in lines if "apple" in l.lower()]
    assert len(apple_lines) == 1


def test_list_dedup_case_insensitive():
    content = "- Apple\n- APPLE\n- apple"
    result = engine().apply_recipes(
        make_segment(content, "retrieval"), [RecipeType.LIST_DEDUP]
    )
    lines = [l for l in result.raw_content.split("\n") if l.strip()]
    assert len(lines) == 1


def test_list_dedup_removes_three_duplicates():
    content = "- foo\n- bar\n- foo\n- baz\n- foo"
    result = engine().apply_recipes(
        make_segment(content, "retrieval"), [RecipeType.LIST_DEDUP]
    )
    lines = [l for l in result.raw_content.split("\n") if l.strip()]
    foo_lines = [l for l in lines if "foo" in l.lower()]
    assert len(foo_lines) == 1


def test_list_dedup_preserves_first_occurrence_order():
    content = "- banana\n- apple\n- cherry\n- apple\n- banana"
    result = engine().apply_recipes(
        make_segment(content, "retrieval"), [RecipeType.LIST_DEDUP]
    )
    lines = [l.strip() for l in result.raw_content.split("\n") if l.strip()]
    assert lines.index("- banana") < lines.index("- apple")
    assert lines.index("- apple") < lines.index("- cherry")


def test_list_dedup_numbered_lists():
    content = "1. step one\n2. step two\n3. step one"
    result = engine().apply_recipes(
        make_segment(content, "retrieval"), [RecipeType.LIST_DEDUP]
    )
    lines = [l for l in result.raw_content.split("\n") if l.strip()]
    step_one_lines = [l for l in lines if "step one" in l.lower()]
    assert len(step_one_lines) == 1


def test_list_dedup_non_list_lines_untouched():
    content = "intro text\n- item\n\n- item\noutro text"
    result = engine().apply_recipes(
        make_segment(content, "retrieval"), [RecipeType.LIST_DEDUP]
    )
    assert "intro text" in result.raw_content
    assert "outro text" in result.raw_content


# ---------------------------------------------------------------------------
# 5. Phrase substitution
# ---------------------------------------------------------------------------


def test_phrase_substitution_replaces_five_phrases():
    phrases = list(PHRASE_MAP.keys())[:5]
    replacements = [PHRASE_MAP[p] for p in phrases]
    content = " ".join(phrases)
    result = engine().apply_recipes(
        make_segment(content, "retrieval"), [RecipeType.PHRASE_SUBSTITUTION]
    )
    for phrase, replacement in zip(phrases, replacements):
        assert phrase.lower() not in result.raw_content.lower()
        assert replacement in result.raw_content


def test_phrase_substitution_case_insensitive():
    content = "THE FOLLOWING steps are needed"
    result = engine().apply_recipes(
        make_segment(content, "retrieval"), [RecipeType.PHRASE_SUBSTITUTION]
    )
    assert "THE FOLLOWING" not in result.raw_content
    assert ":" in result.raw_content


def test_phrase_substitution_reduces_length():
    content = "for more information please read the following documents as mentioned above"
    seg = make_segment(content, "retrieval")
    result = engine().apply_recipes(seg, [RecipeType.PHRASE_SUBSTITUTION])
    assert len(result.raw_content) < len(content)


# ---------------------------------------------------------------------------
# 6. Truncate tail
# ---------------------------------------------------------------------------


def test_truncate_tail_caps_at_80_percent():
    # 2001 tokens → ~8004 chars
    big = "x" * 8004
    seg = make_segment(big, "tool_output")
    result = engine().apply_recipes(seg, [RecipeType.TRUNCATE_TAIL])
    assert result.raw_content.endswith("[...truncated...]")
    kept_chars = len(result.raw_content) - len("\n[...truncated...]")
    # Should be ~80% of original
    assert kept_chars <= int(len(big) * 0.81)
    assert kept_chars >= int(len(big) * 0.79)


def test_truncate_tail_adds_marker():
    big = "word " * 4001  # > 2000 tokens
    result = engine().apply_recipes(make_segment(big), [RecipeType.TRUNCATE_TAIL])
    assert "[...truncated...]" in result.raw_content


def test_truncate_tail_skips_small_segments():
    small = "short content under 2000 tokens"
    result = engine().apply_recipes(make_segment(small), [RecipeType.TRUNCATE_TAIL])
    assert "[...truncated...]" not in result.raw_content
    assert result.raw_content == small


def test_truncate_tail_boundary_at_threshold():
    # exactly at threshold: 2000 tokens = 8000 chars
    at_limit = "a" * (8000)  # exactly 2000 tokens
    result = engine().apply_recipes(make_segment(at_limit), [RecipeType.TRUNCATE_TAIL])
    # Should NOT truncate (> threshold required)
    assert "[...truncated...]" not in result.raw_content


def test_truncate_tail_one_over_threshold():
    over_limit = "a" * (8004)  # 2001 tokens
    result = engine().apply_recipes(make_segment(over_limit), [RecipeType.TRUNCATE_TAIL])
    assert "[...truncated...]" in result.raw_content


# ---------------------------------------------------------------------------
# 7. Boilerplate strip
# ---------------------------------------------------------------------------


def test_boilerplate_strip_removes_copyright():
    content = "# Copyright 2024 Acme Corp\nimport os\n"
    result = engine().apply_recipes(
        make_segment(content, "memory"), [RecipeType.BOILERPLATE_STRIP]
    )
    assert "Copyright" not in result.raw_content
    assert "import os" in result.raw_content


def test_boilerplate_strip_removes_license_comment():
    content = "# Licensed under Apache 2.0\ndef foo(): pass\n"
    result = engine().apply_recipes(
        make_segment(content, "memory"), [RecipeType.BOILERPLATE_STRIP]
    )
    assert "Licensed" not in result.raw_content
    assert "def foo" in result.raw_content


def test_boilerplate_strip_removes_installation_section():
    content = (
        "# My Library\n\n"
        "## Installation\n\npip install mylib\nsome extra notes\n\n"
        "## Usage\n\nimport mylib\n"
    )
    result = engine().apply_recipes(
        make_segment(content, "assistant_context"), [RecipeType.BOILERPLATE_STRIP]
    )
    assert "## Installation" not in result.raw_content
    assert "## Usage" in result.raw_content


def test_boilerplate_strip_api_rate_limit_third_occurrence():
    eng = CompressionRuleEngine()
    content_template = "## Note\nAPI rate limits apply to all endpoints.\nSome other info."

    # First two occurrences should pass through
    r1 = eng.apply_recipes(make_segment(content_template, "memory"), [RecipeType.BOILERPLATE_STRIP])
    assert "API rate limits" in r1.raw_content

    r2 = eng.apply_recipes(make_segment(content_template, "memory"), [RecipeType.BOILERPLATE_STRIP])
    assert "API rate limits" in r2.raw_content

    # Third should be stripped
    r3 = eng.apply_recipes(make_segment(content_template, "memory"), [RecipeType.BOILERPLATE_STRIP])
    assert "API rate limits" not in r3.raw_content


# ---------------------------------------------------------------------------
# 8. Token recount accuracy
# ---------------------------------------------------------------------------


def test_token_recount_after_whitespace_collapse():
    content = "a\n\n\n\n\n\n\nb"
    seg = make_segment(content)
    result = engine().apply_recipes(seg, [RecipeType.WHITESPACE_COLLAPSE])
    assert result.raw_tokens == _count_tokens(result.raw_content)


def test_token_recount_after_truncate():
    big = "z" * 8004
    seg = make_segment(big)
    result = engine().apply_recipes(seg, [RecipeType.TRUNCATE_TAIL])
    assert result.raw_tokens == _count_tokens(result.raw_content)
    assert result.raw_tokens < seg.raw_tokens


# ---------------------------------------------------------------------------
# 9. Determinism (same input → same output)
# ---------------------------------------------------------------------------


def test_deterministic_whitespace_collapse():
    content = "a\n\n\n\nb\n\n\n\nc   \nd"
    seg = make_segment(content)
    e = CompressionRuleEngine()
    r1 = e.apply_recipes(seg, [RecipeType.WHITESPACE_COLLAPSE])
    r2 = e.apply_recipes(seg, [RecipeType.WHITESPACE_COLLAPSE])
    assert r1.raw_content == r2.raw_content


def test_deterministic_list_dedup():
    content = "- a\n- b\n- a\n- c\n- b"
    seg = make_segment(content, "retrieval")
    e = CompressionRuleEngine()
    r1 = e.apply_recipes(seg, [RecipeType.LIST_DEDUP])
    r2 = e.apply_recipes(seg, [RecipeType.LIST_DEDUP])
    assert r1.raw_content == r2.raw_content


def test_deterministic_truncate_tail():
    big = "word " * 4001
    seg = make_segment(big)
    e = CompressionRuleEngine()
    r1 = e.apply_recipes(seg, [RecipeType.TRUNCATE_TAIL])
    r2 = e.apply_recipes(seg, [RecipeType.TRUNCATE_TAIL])
    assert r1.raw_content == r2.raw_content


# ---------------------------------------------------------------------------
# 10. apply_recipes with multiple rules (pipeline order)
# ---------------------------------------------------------------------------


def test_apply_multi_recipe_pipeline():
    """Verify apply_recipes with [WHITESPACE_COLLAPSE, PHRASE_SUBSTITUTION] works."""
    content = "for more information:\n\n\n\nsee the following docs"
    seg = make_segment(content, "retrieval")
    result = engine().apply_recipes(
        seg, [RecipeType.WHITESPACE_COLLAPSE, RecipeType.PHRASE_SUBSTITUTION]
    )
    assert "\n\n\n" not in result.raw_content
    assert "for more information" not in result.raw_content.lower()


def test_apply_recipes_empty_list_passthrough():
    content = "untouched content"
    seg = make_segment(content)
    result = engine().apply_recipes(seg, [])
    assert result.raw_content == content
