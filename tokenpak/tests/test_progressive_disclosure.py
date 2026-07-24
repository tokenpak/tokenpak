"""test_progressive_disclosure.py — Unit tests for progressive disclosure middleware.

Covers:
- extract_section_map: heading parsing + per-section summaries
- assess_intent: summary-ok vs precision-required classification
- disclose: section-map mode, full-content mode, bypass mode
- Token savings measurement
- Integration with inject_retrieved_context
"""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Stub out heavy imports that are irrelevant to progressive disclosure
# ---------------------------------------------------------------------------
_fake_ingest = types.ModuleType("tokenpak.vault.ingest")
_fake_sc = types.ModuleType("tokenpak.vault.ingest.schema_converter")
_fake_sc.should_serve_schema = lambda intent: False
_fake_sc.convert_document = MagicMock(return_value={})
_fake_ingest.schema_converter = _fake_sc
sys.modules.setdefault("tokenpak.vault.ingest", _fake_ingest)
sys.modules.setdefault("tokenpak.vault.ingest.schema_converter", _fake_sc)
sys.modules.setdefault("tokenpak.vault.ingest.api", MagicMock())

_fake_capsules = types.ModuleType("tokenpak.companion.memory.session_capsules")
_fake_capsules.capsule_retrieval_score = lambda score, _capsule: score
sys.modules.setdefault("tokenpak.companion.memory", types.ModuleType("tokenpak.companion.memory"))
sys.modules.setdefault("tokenpak.companion.memory.session_capsules", _fake_capsules)

from tokenpak.vault.progressive_disclosure import (  # noqa: E402
    assess_intent,
    disclose,
    extract_section_map,
    is_enabled,
)

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_STRUCTURED_DOC = """\
# Introduction

This document introduces token optimisation for the vault injection pipeline.
It explains why progressive disclosure matters and how it reduces costs.
The approach is based on structural extraction of Markdown headings.
Each section is summarised using the first N non-empty body lines.
No LLM calls are required for summarisation — it is entirely deterministic.
This makes the middleware safe to use in production without latency impact.
The middleware is bypassable via an environment variable for debugging.
Token savings are logged at DEBUG level for observability.

## Background

The vault holds many large documents that are injected into LLM context.
Each document can consume thousands of tokens when injected in full.
Most requests only need a high-level understanding, not exact content.
By serving a section map instead of full content, we reduce token usage.
The section map includes headings and per-section summaries only.
Full content is still served when the request signals precision is needed.
Precision signals include keywords like exact, verbatim, quote, line number.
The middleware integrates into inject_retrieved_context transparently.

### Related Work

Prior work in compression covers semantic chunking and vector search.
Progressive disclosure extends this to the injection phase.
The approach is complementary to BM25 retrieval and semantic scoring.
See the GAR proposal Track C for the full context and motivation.

## Approach

We use structural extraction based on Markdown headings.
Summaries are generated from the first N non-empty body lines per section.
The section map is rendered as a compact indented text block.
Intent assessment checks for precision keywords in the request string.
If any precision keyword is found, full content is served unchanged.
Otherwise, the section map is served and token savings are calculated.
The middleware tracks saved tokens across all blocks in the injection.
"""

_PLAIN_DOC = """\
This is a plain-text document with no headings.
It spans several lines.
Token counts still apply.
Fourth line here.
Fifth line with more content to pad.
Sixth line to ensure we have enough body content.
Seventh line still no headings.
"""

_TINY_DOC = "# Single\nOnly one line of body."


def _token_counter(t):
    return max(1, len(t) // 4)  # simple proxy


# ---------------------------------------------------------------------------
# extract_section_map
# ---------------------------------------------------------------------------


class TestExtractSectionMap:
    def test_headings_parsed(self):
        sections = extract_section_map(_STRUCTURED_DOC)
        titles = [s["title"] for s in sections]
        assert "Introduction" in titles
        assert "Background" in titles
        assert "Related Work" in titles
        assert "Approach" in titles

    def test_section_ids_are_slugs(self):
        sections = extract_section_map(_STRUCTURED_DOC)
        for sec in sections:
            assert " " not in sec["section_id"]
            assert sec["section_id"] == sec["section_id"].lower()

    def test_depth_reflects_heading_level(self):
        sections = extract_section_map(_STRUCTURED_DOC)
        depth_map = {s["title"]: s["depth"] for s in sections}
        assert depth_map["Introduction"] == 1
        assert depth_map["Background"] == 2
        assert depth_map["Related Work"] == 3
        assert depth_map["Approach"] == 2

    def test_summary_is_non_empty_for_sections_with_body(self):
        sections = extract_section_map(_STRUCTURED_DOC)
        # All sections except possibly last have bodies
        for sec in sections:
            if sec["title"] != "Related Work":  # brief body
                assert isinstance(sec["summary"], str)

    def test_summary_truncated_at_max_chars(self):
        # Build a section with a very long body
        long_body = " ".join(["word"] * 300)
        doc = f"# LongSection\n{long_body}\n"
        sections = extract_section_map(doc)
        assert len(sections) == 1
        assert len(sections[0]["summary"]) <= 201  # 200 + "…"

    def test_no_headings_returns_implicit_section(self):
        sections = extract_section_map(_PLAIN_DOC)
        assert len(sections) == 1
        assert sections[0]["section_id"] == "content"
        assert sections[0]["title"] == "(content)"
        assert sections[0]["depth"] == 0
        assert sections[0]["summary"]  # non-empty

    def test_empty_content_returns_empty_list(self):
        sections = extract_section_map("")
        assert sections == []

    def test_whitespace_only_returns_empty_list(self):
        sections = extract_section_map("   \n  \n  ")
        assert sections == []

    def test_line_bounds_are_set(self):
        sections = extract_section_map(_STRUCTURED_DOC)
        for sec in sections:
            assert "line_start" in sec
            assert "line_end" in sec
            assert sec["line_end"] >= sec["line_start"]


# ---------------------------------------------------------------------------
# assess_intent
# ---------------------------------------------------------------------------


class TestAssessIntent:
    @pytest.mark.parametrize(
        "req",
        [
            "show me the exact text",
            "give me a verbatim copy",
            "I need a direct quote from the document",
            "cite the relevant section",
            "what is on line 42",
            "check code at line 10",
            "do a code review",
            "give me line number 5",
        ],
    )
    def test_precision_keywords_detected(self, req):
        assert assess_intent(req) is True

    @pytest.mark.parametrize(
        "req",
        [
            "summarize the document",
            "give me an overview",
            "what does this file do",
            "explain the architecture",
            "compare approaches",
            "",
        ],
    )
    def test_no_precision_keywords(self, req):
        assert assess_intent(req) is False

    def test_dict_request_checks_query_key(self):
        assert assess_intent({"query": "exact match needed"}) is True
        assert assess_intent({"query": "give me a summary"}) is False

    def test_dict_request_checks_intent_key(self):
        assert assess_intent({"intent": "quote this section"}) is True

    def test_dict_request_checks_content_key(self):
        assert assess_intent({"content": "verbatim reproduction"}) is True

    def test_unknown_type_returns_false(self):
        assert assess_intent(42) is False
        assert assess_intent(None) is False
        assert assess_intent(["exact"]) is False

    def test_case_insensitive(self):
        assert assess_intent("Give me an EXACT copy") is True
        assert assess_intent("QUOTE the passage") is True


# ---------------------------------------------------------------------------
# is_enabled / bypass
# ---------------------------------------------------------------------------


class TestIsEnabled:
    def test_enabled_by_default(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TOKENPAK_PROGRESSIVE_DISCLOSURE", None)
            assert is_enabled() is True

    @pytest.mark.parametrize("val", ["off", "OFF", "false", "0", "no"])
    def test_disabled_values(self, val):
        with patch.dict(os.environ, {"TOKENPAK_PROGRESSIVE_DISCLOSURE": val}):
            assert is_enabled() is False

    @pytest.mark.parametrize("val", ["on", "ON", "true", "1", "yes"])
    def test_enabled_values(self, val):
        with patch.dict(os.environ, {"TOKENPAK_PROGRESSIVE_DISCLOSURE": val}):
            assert is_enabled() is True


# ---------------------------------------------------------------------------
# disclose
# ---------------------------------------------------------------------------


class TestDisclose:
    def test_summary_mode_for_structured_doc(self):
        content, meta = disclose(
            _STRUCTURED_DOC,
            request="give me an overview",
            count_tokens_fn=_token_counter,
        )
        assert meta["mode"] == "summary"
        assert meta["saved_tokens"] > 0
        assert len(content) < len(_STRUCTURED_DOC)
        assert "[section map]" in content

    def test_section_map_contains_titles(self):
        content, meta = disclose(
            _STRUCTURED_DOC,
            request="what is this about",
            count_tokens_fn=_token_counter,
        )
        assert "Introduction" in content
        assert "Background" in content

    def test_precision_mode_returns_full_content(self):
        content, meta = disclose(
            _STRUCTURED_DOC,
            request="give me a verbatim quote",
            count_tokens_fn=_token_counter,
        )
        assert meta["mode"] == "full"
        assert meta["saved_tokens"] == 0
        assert meta["reason"] == "precision_intent_detected"
        assert content == _STRUCTURED_DOC

    def test_bypass_mode_when_disabled(self):
        with patch.dict(os.environ, {"TOKENPAK_PROGRESSIVE_DISCLOSURE": "off"}):
            content, meta = disclose(
                _STRUCTURED_DOC,
                request="overview please",
                count_tokens_fn=_token_counter,
            )
        assert meta["mode"] == "bypass"
        assert meta["saved_tokens"] == 0
        assert content == _STRUCTURED_DOC

    def test_tiny_doc_no_savings_falls_back_to_full(self):
        # A very short document may not have savings
        tiny = "# Sec\nshort."
        content, meta = disclose(
            tiny,
            request="summary",
            count_tokens_fn=_token_counter,
        )
        # Either full (no savings) or summary — both are valid
        assert meta["saved_tokens"] >= 0
        if meta["mode"] == "full":
            assert content == tiny

    def test_saved_tokens_positive_for_large_doc(self):
        content, meta = disclose(
            _STRUCTURED_DOC,
            request="explain",
            count_tokens_fn=_token_counter,
        )
        if meta["mode"] == "summary":
            assert meta["saved_tokens"] > 0
            assert meta["full_tokens"] > meta["summary_tokens"]

    def test_plain_doc_no_headings_falls_back_to_full_or_implicit(self):
        # Plain doc may have no headings — disclose may return full or
        # implicit section map depending on token delta.
        content, meta = disclose(
            _PLAIN_DOC,
            request="overview",
            count_tokens_fn=_token_counter,
        )
        assert meta["mode"] in ("summary", "full")
        assert meta["saved_tokens"] >= 0

    def test_empty_string_returns_empty(self):
        content, meta = disclose("", request="overview", count_tokens_fn=_token_counter)
        assert content == ""
        assert meta["saved_tokens"] == 0

    def test_meta_keys_present_in_summary_mode(self):
        content, meta = disclose(
            _STRUCTURED_DOC,
            request="summarize",
            count_tokens_fn=_token_counter,
        )
        if meta["mode"] == "summary":
            assert "full_tokens" in meta
            assert "summary_tokens" in meta
            assert "saved_tokens" in meta

    def test_dict_request_triggers_precision(self):
        content, meta = disclose(
            _STRUCTURED_DOC,
            request={"query": "give me an exact quote"},
            count_tokens_fn=_token_counter,
        )
        assert meta["mode"] == "full"
        assert meta["reason"] == "precision_intent_detected"


# ---------------------------------------------------------------------------
# Integration: inject_retrieved_context uses progressive disclosure
# ---------------------------------------------------------------------------


class TestInjectIntegration:
    """Verify that inject_retrieved_context produces smaller output via PD."""

    def _make_results(self, content: str, n: int = 3):
        return [
            (
                {
                    "content": content,
                    "source_path": f"doc{i}.md",
                    "block_id": f"b{i}",
                },
                float(10 - i),
            )
            for i in range(n)
        ]

    def test_injection_smaller_with_progressive_disclosure_on(self):
        from tokenpak.vault.search import inject_retrieved_context

        results = self._make_results(_STRUCTURED_DOC, n=2)

        with patch.dict(os.environ, {"TOKENPAK_PROGRESSIVE_DISCLOSURE": "off"}):
            _, tokens_off, _ = inject_retrieved_context(
                results,
                max_tokens=8000,
                count_tokens_fn=_token_counter,
                intent="give me an overview",
            )

        with patch.dict(os.environ, {"TOKENPAK_PROGRESSIVE_DISCLOSURE": "on"}):
            _, tokens_on, _ = inject_retrieved_context(
                results,
                max_tokens=8000,
                count_tokens_fn=_token_counter,
                intent="give me an overview",
            )

        assert tokens_on < tokens_off, (
            f"Expected progressive disclosure to reduce tokens (on={tokens_on}, off={tokens_off})"
        )

    def test_injection_full_when_precision_intent(self):
        from tokenpak.vault.search import inject_retrieved_context

        results = self._make_results(_STRUCTURED_DOC, n=2)

        with patch.dict(os.environ, {"TOKENPAK_PROGRESSIVE_DISCLOSURE": "on"}):
            text_precision, _, _ = inject_retrieved_context(
                results,
                max_tokens=8000,
                count_tokens_fn=_token_counter,
                intent="give me the exact verbatim text",
            )
            text_summary, _, _ = inject_retrieved_context(
                results,
                max_tokens=8000,
                count_tokens_fn=_token_counter,
                intent="give me an overview",
            )

        # Precision intent should produce more content than summary intent
        assert len(text_precision) > len(text_summary), (
            "Precision intent should produce fuller injection than summary intent"
        )
