"""Tests for tokenpak.agent.vault.chunk_shapes."""

from __future__ import annotations

import pytest
from tokenpak.agent.vault.chunk_shapes import (
    CHUNK_SHAPES,
    apply_shape,
    get_shape_for_intent,
    reshape_chunks,
    _shape_code_contiguous,
    _shape_fact_chunk,
    _shape_decision_summary,
    _shape_section_header,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CODE_CONTENT = """\
import os
import sys
from pathlib import Path

def my_function(x: int) -> str:
    \"\"\"Convert x to string.\"\"\"
    if x < 0:
        return f"negative: {x}"
    return str(x)

def another_function(y):
    return y * 2

class MyClass:
    def __init__(self):
        self.value = 42
"""

PROSE_CONTENT = """\
# Introduction

In this section we will discuss the architecture decisions.
This is a very important document.

## Key Facts

The system processes 10,000 requests per second.
Memory usage peaks at 2GB under load.
Response latency is 45ms at p99.

## Background

As you can see from the metrics, performance is critical.
The API was redesigned in 2024 to improve throughput.
"""

DECISION_CONTENT = """\
# Architecture Review

## Background

We evaluated three storage backends.

## Decision

We chose PostgreSQL over MongoDB because of ACID compliance.

## Rationale

Relational integrity is required for financial data.
Joins across user/transaction tables are frequent.

## Alternative Considered

Redis was considered for caching but rejected due to persistence concerns.
"""

HEADING_CONTENT = """\
# TokenPak Overview

TokenPak compresses prompts to reduce token costs. It supports multiple providers.

## Retrieval Pipeline

The retrieval pipeline uses BM25 scoring. Semantic similarity is also applied.

## Chunk Shapes

Different intents use different chunk shapes. Debug uses code_contiguous.

## Configuration

Configuration is stored in TOML format. See config.py for details.
"""


# ---------------------------------------------------------------------------
# Test 1: CHUNK_SHAPES registry
# ---------------------------------------------------------------------------

class TestChunkShapesRegistry:
    def test_all_expected_intents_present(self):
        expected = {"debug", "explain", "search", "plan", "create", "summarize"}
        assert expected == set(CHUNK_SHAPES.keys())

    def test_each_shape_has_required_fields(self):
        for intent, config in CHUNK_SHAPES.items():
            assert "shape" in config, f"{intent} missing 'shape'"
            assert "granularity" in config, f"{intent} missing 'granularity'"
            assert "max_lines" in config, f"{intent} missing 'max_lines'"
            assert isinstance(config["max_lines"], int), f"{intent} max_lines not int"
            assert config["max_lines"] > 0, f"{intent} max_lines must be positive"

    def test_debug_shape_is_code_contiguous(self):
        assert CHUNK_SHAPES["debug"]["shape"] == "code_contiguous"

    def test_search_shape_is_fact_chunk(self):
        assert CHUNK_SHAPES["search"]["shape"] == "fact_chunk"

    def test_plan_shape_is_decision_summary(self):
        assert CHUNK_SHAPES["plan"]["shape"] == "decision_summary"

    def test_granularity_values(self):
        assert CHUNK_SHAPES["debug"]["granularity"] == "function"
        assert CHUNK_SHAPES["search"]["granularity"] == "paragraph"
        assert CHUNK_SHAPES["create"]["granularity"] == "file"
        assert CHUNK_SHAPES["summarize"]["granularity"] == "heading"


# ---------------------------------------------------------------------------
# Test 2: get_shape_for_intent
# ---------------------------------------------------------------------------

class TestGetShapeForIntent:
    def test_known_intent_returns_correct_shape(self):
        config = get_shape_for_intent("debug")
        assert config["shape"] == "code_contiguous"

    def test_case_insensitive(self):
        assert get_shape_for_intent("DEBUG") == get_shape_for_intent("debug")
        assert get_shape_for_intent("Search") == get_shape_for_intent("search")

    def test_unknown_intent_returns_default(self):
        config = get_shape_for_intent("unknown_xyz")
        assert "shape" in config
        assert config["shape"] == "prose_section"

    def test_empty_string_returns_default(self):
        config = get_shape_for_intent("")
        assert "shape" in config


# ---------------------------------------------------------------------------
# Test 3: code_contiguous shape
# ---------------------------------------------------------------------------

class TestCodeContiguousShape:
    def test_includes_imports(self):
        result = _shape_code_contiguous(CODE_CONTENT, max_lines=50)
        assert "import os" in result
        assert "import sys" in result

    def test_includes_function_body(self):
        result = _shape_code_contiguous(CODE_CONTENT, max_lines=50)
        assert "my_function" in result

    def test_respects_max_lines(self):
        result = _shape_code_contiguous(CODE_CONTENT, max_lines=5)
        lines = result.splitlines()
        # May have import lines + up to max_lines body — total is bounded
        assert len(lines) <= 15  # imports (3) + blank + body (5) + some slack

    def test_no_code_content(self):
        # Prose with no function definitions — should still return something
        result = _shape_code_contiguous("just some text\nno functions here", max_lines=50)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Test 4: fact_chunk shape
# ---------------------------------------------------------------------------

class TestFactChunkShape:
    def test_strips_narrative_filler(self):
        result = _shape_fact_chunk(PROSE_CONTENT, max_lines=50)
        # "In this section we will discuss" should be stripped
        assert "in this section we will" not in result.lower()

    def test_retains_facts(self):
        result = _shape_fact_chunk(PROSE_CONTENT, max_lines=50)
        # Numbers and facts should be present
        assert "10,000" in result or "2GB" in result or "45ms" in result

    def test_respects_max_lines(self):
        result = _shape_fact_chunk(PROSE_CONTENT, max_lines=5)
        lines = [l for l in result.splitlines() if l.strip()]
        assert len(lines) <= 10  # some slack for blanks

    def test_empty_content(self):
        result = _shape_fact_chunk("", max_lines=50)
        assert result == ""


# ---------------------------------------------------------------------------
# Test 5: decision_summary shape
# ---------------------------------------------------------------------------

class TestDecisionSummaryShape:
    def test_extracts_decision_section(self):
        result = _shape_decision_summary(DECISION_CONTENT, max_lines=50)
        assert "PostgreSQL" in result

    def test_extracts_rationale_section(self):
        result = _shape_decision_summary(DECISION_CONTENT, max_lines=50)
        assert "ACID" in result or "Relational" in result

    def test_extracts_alternative_section(self):
        result = _shape_decision_summary(DECISION_CONTENT, max_lines=50)
        assert "Redis" in result

    def test_fallback_for_no_decision_markers(self):
        plain = "This is just a regular text.\nNo decisions here."
        result = _shape_decision_summary(plain, max_lines=50)
        assert "regular text" in result

    def test_respects_max_lines(self):
        result = _shape_decision_summary(DECISION_CONTENT, max_lines=3)
        assert len(result.splitlines()) <= 5


# ---------------------------------------------------------------------------
# Test 6: section_header shape
# ---------------------------------------------------------------------------

class TestSectionHeaderShape:
    def test_extracts_headings(self):
        result = _shape_section_header(HEADING_CONTENT, max_lines=50)
        assert "TokenPak Overview" in result
        assert "Retrieval Pipeline" in result

    def test_includes_topic_sentence(self):
        result = _shape_section_header(HEADING_CONTENT, max_lines=50)
        # Should include first sentence after each heading
        assert "BM25" in result or "compresses" in result

    def test_respects_max_lines(self):
        result = _shape_section_header(HEADING_CONTENT, max_lines=4)
        assert len(result.splitlines()) <= 8  # some slack

    def test_no_headings_fallback(self):
        plain = "Line 1\nLine 2\nLine 3"
        result = _shape_section_header(plain, max_lines=50)
        # Fallback returns first few lines
        assert "Line 1" in result


# ---------------------------------------------------------------------------
# Test 7: apply_shape dispatch
# ---------------------------------------------------------------------------

class TestApplyShape:
    def test_dispatches_to_correct_fn(self):
        config = {"shape": "code_contiguous", "max_lines": 50}
        result = apply_shape(CODE_CONTENT, config)
        assert "import" in result or "def " in result

    def test_unknown_shape_falls_back_to_prose(self):
        config = {"shape": "nonexistent_shape", "max_lines": 10}
        result = apply_shape("some content here", config)
        assert "some content" in result

    def test_result_is_string(self):
        for intent, config in CHUNK_SHAPES.items():
            result = apply_shape(PROSE_CONTENT, config)
            assert isinstance(result, str), f"apply_shape returned non-str for {intent}"


# ---------------------------------------------------------------------------
# Test 8: reshape_chunks integration
# ---------------------------------------------------------------------------

class TestReshapeChunks:
    def _make_results(self, content: str, n: int = 2):
        return [
            ({"block_id": f"file.py#chunk{i}", "source_path": "file.py", "content": content}, float(n - i))
            for i in range(n)
        ]

    def test_preserves_original_content(self):
        results = self._make_results(CODE_CONTENT)
        reshaped = reshape_chunks(results, intent="debug")
        for block, _ in reshaped:
            assert block["content"] == CODE_CONTENT

    def test_adds_reshaped_content(self):
        results = self._make_results(CODE_CONTENT)
        reshaped = reshape_chunks(results, intent="debug")
        for block, _ in reshaped:
            assert "reshaped_content" in block
            assert isinstance(block["reshaped_content"], str)

    def test_adds_shape_applied_key(self):
        results = self._make_results(CODE_CONTENT)
        reshaped = reshape_chunks(results, intent="debug")
        for block, _ in reshaped:
            assert block["shape_applied"] == "code_contiguous"

    def test_adds_intent_key(self):
        results = self._make_results(PROSE_CONTENT)
        reshaped = reshape_chunks(results, intent="plan")
        for block, _ in reshaped:
            assert block["intent"] == "plan"

    def test_preserves_scores(self):
        results = self._make_results(CODE_CONTENT, n=3)
        reshaped = reshape_chunks(results, intent="search")
        original_scores = [s for _, s in results]
        reshaped_scores = [s for _, s in reshaped]
        assert original_scores == reshaped_scores

    def test_empty_results(self):
        reshaped = reshape_chunks([], intent="debug")
        assert reshaped == []

    def test_unknown_intent_uses_default(self):
        results = self._make_results(PROSE_CONTENT)
        reshaped = reshape_chunks(results, intent="unknown_intent")
        for block, _ in reshaped:
            assert "reshaped_content" in block
            assert block["shape_applied"] == "prose_section"

    def test_all_intents_produce_string_output(self):
        results = self._make_results(PROSE_CONTENT)
        for intent in CHUNK_SHAPES:
            reshaped = reshape_chunks(results, intent=intent)
            for block, _ in reshaped:
                content = block["reshaped_content"]
                assert isinstance(content, str), f"Non-str output for intent={intent}"

    def test_search_intent_respects_max_lines(self):
        results = self._make_results(PROSE_CONTENT)
        reshaped = reshape_chunks(results, intent="search")
        max_lines = CHUNK_SHAPES["search"]["max_lines"]
        for block, _ in reshaped:
            lines = block["reshaped_content"].splitlines()
            assert len(lines) <= max_lines + 5  # +5 for blank-line slack

    def test_debug_code_contains_function(self):
        results = self._make_results(CODE_CONTENT)
        reshaped = reshape_chunks(results, intent="debug")
        for block, _ in reshaped:
            assert "def " in block["reshaped_content"] or "import" in block["reshaped_content"]
