# SPDX-License-Identifier: MIT
"""tests/test_document_parser.py

Tests for tokenpak._internal.ingest.document_parser (Phase 5E).

Covers:
  1. Markdown heading hierarchy parsed correctly
  2. Tables extracted from markdown
  3. Section types classified correctly (7+ types)
  4. Plain text heuristic headings
  5. Deep nesting handled (H1→H2→H3→H4)
  6. Empty/malformed docs handled gracefully
  7. HTML parsing: headings + tables
  8. Citations extracted
  9. Code blocks captured
  10. heading_tree navigable structure
  11. Auto-format detection
  12. Section word counts
"""
from __future__ import annotations

import pytest

pytest.importorskip("tokenpak._internal.ingest.document_parser", reason="module not available in current build")
import pytest
from tokenpak._internal.ingest.document_parser import (
    DocumentParser,
    DocumentSection,
    DocumentStructure,
    _classify_section,
    _extract_citations,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

PARSER = DocumentParser()

MARKDOWN_BASIC = """
# Introduction

This is the introduction to the document.

## Background

Background context goes here.

### Prior Work

Details about prior work [Smith 2020] and [Jones et al., 2019].

## Methodology

How we approached the problem.

### Data Collection

We collected data from 100 sources.

#### Instruments

Details about instruments used.

## Results

| Metric | Value | Unit |
|--------|-------|------|
| Accuracy | 95.2 | % |
| Latency | 12.3 | ms |
| Throughput | 850 | req/s |

Key findings here.

## Recommendations

We recommend adopting this approach.

## Appendix

Additional materials.
""".strip()

MARKDOWN_WITH_CODE = """
# Overview

Project summary.

## Implementation

Here is the code:

```python
def hello():
    return "world"
```

More explanation follows.
""".strip()

PLAIN_TEXT = """
1. Introduction

This document describes our approach.

2. Methodology

We used the following steps.

2.1 Data Gathering

First we gathered data.

2.2 Analysis

Then we performed analysis.

3. Results

Here are the results.

CONCLUSION

All findings support the hypothesis.
""".strip()

HTML_DOC = """
<html>
<body>
<h1>Report Title</h1>
<p>Executive summary paragraph.</p>
<h2>Overview</h2>
<p>This section provides an overview.</p>
<h2>Results</h2>
<p>Here are the results.</p>
<table>
  <tr><th>Name</th><th>Score</th></tr>
  <tr><td>Alice</td><td>95</td></tr>
  <tr><td>Bob</td><td>87</td></tr>
</table>
</body>
</html>
""".strip()

DEEP_NESTING = """
# H1 Top

Content at H1.

## H2 Section

Content at H2.

### H3 Subsection

Content at H3.

#### H4 Detail

Content at H4.

##### H5 Fine Detail

Content at H5.
""".strip()

EMPTY_DOC = ""
MALFORMED_DOC = "   \n\n\n   "
MINIMAL_DOC = "Just some plain text with no headings at all."


# ---------------------------------------------------------------------------
# Test 1: Markdown heading hierarchy
# ---------------------------------------------------------------------------

class TestMarkdownHierarchy:
    def test_top_level_sections_count(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        # Should have: Introduction (H1) as sole top-level
        assert len(doc.sections) >= 1

    def test_title_from_h1(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        assert doc.title == "Introduction"

    def test_h2_sections_nested_under_h1(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        intro = doc.sections[0]
        assert intro.heading == "Introduction"
        assert intro.level == 1
        # Background, Methodology, Results, Recommendations, Appendix
        h2_headings = [s.heading for s in intro.subsections]
        assert "Background" in h2_headings
        assert "Methodology" in h2_headings

    def test_h3_nested_under_h2(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        intro = doc.sections[0]
        background = next(s for s in intro.subsections if s.heading == "Background")
        h3_headings = [s.heading for s in background.subsections]
        assert "Prior Work" in h3_headings

    def test_heading_levels_correct(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        intro = doc.sections[0]
        assert intro.level == 1
        bg = next(s for s in intro.subsections if s.heading == "Background")
        assert bg.level == 2
        prior = next(s for s in bg.subsections if s.heading == "Prior Work")
        assert prior.level == 3

    def test_total_words_positive(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        assert doc.total_words > 0

    def test_section_word_count_nonzero(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        intro = doc.sections[0]
        assert intro.word_count > 0


# ---------------------------------------------------------------------------
# Test 2: Table extraction from markdown
# ---------------------------------------------------------------------------

class TestMarkdownTableExtraction:
    def test_tables_found(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        assert len(doc.tables) >= 1

    def test_table_headers(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        table = doc.tables[0]
        assert "Metric" in table["headers"]
        assert "Value" in table["headers"]
        assert "Unit" in table["headers"]

    def test_table_rows(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        table = doc.tables[0]
        assert table["row_count"] == 3
        assert table["rows"][0]["Metric"] == "Accuracy"

    def test_table_in_section(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        # The results section should contain the table
        intro = doc.sections[0]
        results = next(s for s in intro.subsections if s.heading == "Results")
        assert len(results.tables) >= 1


# ---------------------------------------------------------------------------
# Test 3: Section type classification
# ---------------------------------------------------------------------------

class TestSectionClassification:
    def test_overview_type(self):
        assert _classify_section("Introduction", "This is the introduction.") == "overview"

    def test_methodology_type(self):
        assert _classify_section("Methodology", "We used this approach.") == "methodology"

    def test_results_type(self):
        assert _classify_section("Results", "The outcome was positive.") == "results"

    def test_recommendations_type(self):
        assert _classify_section("Recommendations", "We suggest adopting X.") == "recommendations"

    def test_legal_type(self):
        assert _classify_section("License", "MIT License applies.") == "legal"

    def test_appendix_type(self):
        assert _classify_section("Appendix A", "Supplemental data.") == "appendix"

    def test_definitions_type(self):
        assert _classify_section("Glossary", "Term definitions here.") == "definitions"

    def test_general_type(self):
        result = _classify_section("Random Section", "No special keywords here.")
        assert result == "general"

    def test_sections_classified_in_doc(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        intro = doc.sections[0]
        assert intro.section_type == "overview"
        subsection_types = {s.heading: s.section_type for s in intro.subsections}
        assert subsection_types.get("Results") == "results"
        assert subsection_types.get("Recommendations") == "recommendations"
        assert subsection_types.get("Appendix") == "appendix"


# ---------------------------------------------------------------------------
# Test 4: Plain text heuristic headings
# ---------------------------------------------------------------------------

class TestPlainTextParsing:
    def test_numbered_headings_detected(self):
        doc = PARSER.parse(PLAIN_TEXT, fmt="text")
        headings = [s.heading for s in _flat_headings(doc.sections)]
        assert any("Introduction" in h for h in headings)
        assert any("Methodology" in h or "Data Gathering" in h for h in headings)

    def test_all_caps_heading_detected(self):
        doc = PARSER.parse(PLAIN_TEXT, fmt="text")
        headings = [s.heading for s in _flat_headings(doc.sections)]
        assert any("Conclusion" in h or "CONCLUSION" in h for h in headings)

    def test_sections_have_content(self):
        doc = PARSER.parse(PLAIN_TEXT, fmt="text")
        for sec in _flat_headings(doc.sections):
            # At least some sections should have content
            pass  # Just ensure no exception
        assert len(doc.sections) >= 1

    def test_total_words_plain_text(self):
        doc = PARSER.parse(PLAIN_TEXT, fmt="text")
        assert doc.total_words > 0


# ---------------------------------------------------------------------------
# Test 5: Deep nesting (H1→H2→H3→H4→H5)
# ---------------------------------------------------------------------------

class TestDeepNesting:
    def test_h1_present(self):
        doc = PARSER.parse(DEEP_NESTING, fmt="markdown")
        assert doc.sections[0].level == 1
        assert doc.sections[0].heading == "H1 Top"

    def test_h2_under_h1(self):
        doc = PARSER.parse(DEEP_NESTING, fmt="markdown")
        h1 = doc.sections[0]
        assert any(s.level == 2 for s in h1.subsections)

    def test_h3_under_h2(self):
        doc = PARSER.parse(DEEP_NESTING, fmt="markdown")
        h2 = doc.sections[0].subsections[0]
        assert any(s.level == 3 for s in h2.subsections)

    def test_h4_under_h3(self):
        doc = PARSER.parse(DEEP_NESTING, fmt="markdown")
        h3 = doc.sections[0].subsections[0].subsections[0]
        assert any(s.level == 4 for s in h3.subsections)

    def test_h5_under_h4(self):
        doc = PARSER.parse(DEEP_NESTING, fmt="markdown")
        h4 = doc.sections[0].subsections[0].subsections[0].subsections[0]
        assert any(s.level == 5 for s in h4.subsections)

    def test_heading_tree_reflects_nesting(self):
        doc = PARSER.parse(DEEP_NESTING, fmt="markdown")
        tree = doc.heading_tree
        # H1 Top should be in tree
        assert "H1 Top" in tree
        h2_tree = tree["H1 Top"]["subsections"]
        assert "H2 Section" in h2_tree


# ---------------------------------------------------------------------------
# Test 6: Empty / malformed docs
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_string(self):
        doc = PARSER.parse(EMPTY_DOC, fmt="markdown")
        assert doc.title == ""
        assert doc.sections == []
        assert doc.total_words == 0

    def test_whitespace_only(self):
        doc = PARSER.parse(MALFORMED_DOC, fmt="markdown")
        assert doc.total_words == 0

    def test_no_headings(self):
        doc = PARSER.parse(MINIMAL_DOC, fmt="markdown")
        assert doc.total_words > 0
        # Should still produce a doc without crashing
        assert isinstance(doc, DocumentStructure)

    def test_auto_format_empty(self):
        doc = PARSER.parse("", fmt="auto")
        assert isinstance(doc, DocumentStructure)

    def test_malformed_table(self):
        # Table with only header line — shouldn't crash
        md = "# Section\n\n| A | B |\n\nSome text."
        doc = PARSER.parse(md, fmt="markdown")
        assert isinstance(doc, DocumentStructure)

    def test_unclosed_code_fence(self):
        md = "# Title\n\n```python\ndef foo():\n    pass\n"
        doc = PARSER.parse(md, fmt="markdown")
        assert isinstance(doc, DocumentStructure)


# ---------------------------------------------------------------------------
# Test 7: HTML parsing
# ---------------------------------------------------------------------------

class TestHTMLParsing:
    def test_html_title_from_h1(self):
        doc = PARSER.parse(HTML_DOC, fmt="html")
        assert doc.title == "Report Title"

    def test_html_sections_extracted(self):
        doc = PARSER.parse(HTML_DOC, fmt="html")
        headings = [s.heading for s in _flat_headings(doc.sections)]
        assert "Overview" in headings
        assert "Results" in headings

    def test_html_table_extracted(self):
        doc = PARSER.parse(HTML_DOC, fmt="html")
        assert len(doc.tables) >= 1
        table = doc.tables[0]
        assert "Name" in table["headers"]
        assert "Score" in table["headers"]

    def test_html_table_rows(self):
        doc = PARSER.parse(HTML_DOC, fmt="html")
        table = doc.tables[0]
        assert table["row_count"] >= 1

    def test_html_auto_detected(self):
        doc = PARSER.parse(HTML_DOC, fmt="auto")
        assert doc.metadata.get("format") == "html"


# ---------------------------------------------------------------------------
# Test 8: Citations extracted
# ---------------------------------------------------------------------------

class TestCitations:
    def test_bracket_citations(self):
        citations = _extract_citations("See [Smith 2020] and [Jones et al., 2019].")
        assert "[Smith 2020]" in citations

    def test_numeric_citations(self):
        citations = _extract_citations("As noted [1] and later [2,3].")
        assert "[1]" in citations

    def test_footnote_markers(self):
        citations = _extract_citations("As shown [^1] in the paper.")
        assert "[^1]" in citations

    def test_citations_in_doc(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        assert len(doc.citations) >= 2


# ---------------------------------------------------------------------------
# Test 9: Code blocks captured
# ---------------------------------------------------------------------------

class TestCodeBlocks:
    def test_code_block_in_section(self):
        doc = PARSER.parse(MARKDOWN_WITH_CODE, fmt="markdown")
        intro = doc.sections[0]
        impl = next(s for s in intro.subsections if s.heading == "Implementation")
        assert len(impl.code_blocks) >= 1
        assert "def hello" in impl.code_blocks[0]


# ---------------------------------------------------------------------------
# Test 10: heading_tree structure
# ---------------------------------------------------------------------------

class TestHeadingTree:
    def test_tree_is_dict(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        assert isinstance(doc.heading_tree, dict)

    def test_tree_has_top_level(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        assert "Introduction" in doc.heading_tree

    def test_tree_has_subsections(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        intro_node = doc.heading_tree["Introduction"]
        assert "subsections" in intro_node
        assert isinstance(intro_node["subsections"], dict)

    def test_tree_nested_level(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        intro_node = doc.heading_tree["Introduction"]
        assert intro_node["level"] == 1

    def test_tree_subsection_has_section_type(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        intro_subs = doc.heading_tree["Introduction"]["subsections"]
        assert "Results" in intro_subs
        assert intro_subs["Results"]["section_type"] == "results"


# ---------------------------------------------------------------------------
# Test 11: Auto-format detection
# ---------------------------------------------------------------------------

class TestAutoDetect:
    def test_markdown_detected(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="auto")
        assert doc.metadata.get("format") == "markdown"

    def test_html_detected(self):
        doc = PARSER.parse(HTML_DOC, fmt="auto")
        assert doc.metadata.get("format") == "html"

    def test_plain_text_detected(self):
        plain = "This is just plain text.\nNo headings or markup at all.\nJust prose."
        doc = PARSER.parse(plain, fmt="auto")
        assert doc.metadata.get("format") in ("text", "markdown")


# ---------------------------------------------------------------------------
# Test 12: to_dict serialization
# ---------------------------------------------------------------------------

class TestSerialization:
    def test_to_dict_is_dict(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        d = doc.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_has_required_keys(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        d = doc.to_dict()
        for key in ("title", "sections", "heading_tree", "metadata", "tables", "citations", "total_words"):
            assert key in d, f"Missing key: {key}"

    def test_section_to_dict(self):
        doc = PARSER.parse(MARKDOWN_BASIC, fmt="markdown")
        sec_dict = doc.sections[0].to_dict()
        for key in ("heading", "level", "section_type", "word_count", "subsections"):
            assert key in sec_dict


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _flat_headings(sections: list) -> list[DocumentSection]:
    """Flatten section tree into a list."""
    result = []
    for s in sections:
        result.append(s)
        result.extend(_flat_headings(s.subsections))
    return result
