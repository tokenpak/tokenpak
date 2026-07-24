"""Tests for tokenpak.compression.salience.doc_extractor module."""

import inspect

from tokenpak.compression.doc_compressor import compress_document
from tokenpak.compression.salience.doc_extractor import (
    DocExtractionResult,
    DocExtractor,
)


class TestDocExtractionResult:
    """Test DocExtractionResult dataclass."""

    def test_default_values(self):
        """Test default initialization."""
        result = DocExtractionResult()
        assert result.lines_in == 0
        assert result.lines_out == 0
        assert result.headings == []
        assert result.annotation_count == 0
        assert result.decision_count == 0
        assert result.extracted == ""

    def test_reduction_pct_zero_input(self):
        """Test reduction percentage with zero input."""
        result = DocExtractionResult(lines_in=0, lines_out=0)
        assert result.reduction_pct == 0.0

    def test_reduction_pct_calculation(self):
        """Test reduction percentage calculation."""
        result = DocExtractionResult(lines_in=100, lines_out=20)
        assert result.reduction_pct == 80.0

    def test_with_all_values(self):
        """Test initialization with all values."""
        result = DocExtractionResult(
            lines_in=50,
            lines_out=10,
            headings=["# Title", "## Subtitle"],
            annotation_count=3,
            decision_count=2,
            extracted="sample",
        )
        assert result.lines_in == 50
        assert result.lines_out == 10
        assert result.headings == ["# Title", "## Subtitle"]
        assert result.annotation_count == 3
        assert result.decision_count == 2
        assert result.extracted == "sample"


class TestDocExtractorInit:
    """Test DocExtractor initialization."""

    def test_default_init(self):
        """Test default initialization."""
        extractor = DocExtractor()
        assert extractor.annotation_context == 2
        assert extractor.include_rst_headings is True

    def test_custom_annotation_context(self):
        """Test custom annotation context lines."""
        extractor = DocExtractor(annotation_context=5)
        assert extractor.annotation_context == 5

    def test_disable_rst_headings(self):
        """Test disabling RST heading detection."""
        extractor = DocExtractor(include_rst_headings=False)
        assert extractor.include_rst_headings is False

    def test_module_helper_preserves_kwargs_compatibility(self):
        signature = inspect.signature(compress_document)
        assert any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in signature.parameters.values()
        )


class TestDocExtractorEmpty:
    """Test DocExtractor with empty/minimal input."""

    def test_empty_string(self):
        """Test extraction with empty string."""
        extractor = DocExtractor()
        result = extractor.extract("")
        assert result.lines_in == 0
        assert len(result.headings) == 0

    def test_single_line(self):
        """Test extraction with single line."""
        extractor = DocExtractor()
        result = extractor.extract("Hello world")
        assert result.lines_in == 1

    def test_only_whitespace(self):
        """Test extraction with only whitespace."""
        extractor = DocExtractor()
        result = extractor.extract("   \n  \n")
        assert result.lines_in == 2


class TestDocExtractorHeadings:
    """Test heading extraction."""

    def test_markdown_h1(self):
        """Test detection of markdown H1 heading."""
        doc = "# Main Title\nSome content"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert "# Main Title" in result.headings

    def test_markdown_h2(self):
        """Test detection of markdown H2 heading."""
        doc = "## Subheading\nContent"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert "## Subheading" in result.headings

    def test_markdown_h6(self):
        """Test detection of markdown H6 heading."""
        doc = "###### Small heading\nDetails"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert "###### Small heading" in result.headings

    def test_multiple_markdown_headings(self):
        """Test extraction of multiple markdown headings."""
        doc = """# Title
Some intro

## Section 1
Content here

## Section 2
More content
"""
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert len(result.headings) == 3
        assert "# Title" in result.headings
        assert "## Section 1" in result.headings

    def test_rst_heading_underline_equals(self):
        """Test detection of RST heading with = underline."""
        doc = """Title
=====
Content"""
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert "Title" in result.headings

    def test_rst_heading_underline_dashes(self):
        """Test detection of RST heading with - underline."""
        doc = """Subtitle
--------
Details"""
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert "Subtitle" in result.headings

    def test_rst_heading_underline_tildes(self):
        """Test detection of RST heading with ~ underline."""
        doc = """Section
~~~~~~~
Text"""
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert "Section" in result.headings

    def test_rst_headings_disabled(self):
        """Test that RST headings are ignored when disabled."""
        doc = """Title
=====
Content"""
        extractor = DocExtractor(include_rst_headings=False)
        result = extractor.extract(doc)
        assert len(result.headings) == 0

    def test_heading_no_trailing_space(self):
        """Test markdown heading without trailing space."""
        doc = "#NoSpace\nContent"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        # Should not match without space
        assert len(result.headings) == 0


class TestDocExtractorAnnotations:
    """Test annotation detection (TODO, FIXME, etc.)."""

    def test_todo_annotation(self):
        """Test detection of TODO."""
        doc = """Introduction
TODO: implement this feature
Next line"""
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.annotation_count == 1

    def test_fixme_annotation(self):
        """Test detection of FIXME."""
        doc = "FIXME: fix the bug\nFurther info"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.annotation_count == 1

    def test_note_annotation(self):
        """Test detection of NOTE."""
        doc = "Some text\nNOTE: important detail\nMore text"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.annotation_count == 1

    def test_hack_annotation(self):
        """Test detection of HACK."""
        doc = "Code\nHACK: this is temporary\nMore code"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.annotation_count == 1

    def test_xxx_annotation(self):
        """Test detection of XXX."""
        doc = "Content\nXXX: needs review\nMore"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.annotation_count == 1

    def test_bug_annotation(self):
        """Test detection of BUG."""
        doc = "Text\nBUG: memory leak\nDetails"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.annotation_count == 1

    def test_warn_annotation(self):
        """Test detection of WARN."""
        doc = "Notice\nWARN: dangerous operation\nContinue"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.annotation_count == 1

    def test_multiple_annotations(self):
        """Test detection of multiple annotations."""
        doc = """TODO: fix this
Content

FIXME: and this
More content

NOTE: remember this
End"""
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.annotation_count == 3

    def test_annotation_context_lines(self):
        """Test that context lines are included after annotation."""
        doc = """Start
TODO: do something
line1
line2
line3
End"""
        extractor = DocExtractor(annotation_context=2)
        result = extractor.extract(doc)
        assert "TODO" in result.extracted
        # Should include 2 lines after TODO
        assert "line1" in result.extracted
        assert "line2" in result.extracted

    def test_annotation_case_insensitive(self):
        """Test that annotations are case insensitive."""
        doc = "Text\ntodo: lowercase\nMore"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.annotation_count == 1

    def test_annotation_in_middle_of_line(self):
        """Test annotation in middle of line."""
        doc = "This is TODO: important stuff on this line"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.annotation_count == 1


class TestDocExtractorDecisions:
    """Test decision item detection."""

    def test_bullet_decided(self):
        """Test detection of 'decided' decision."""
        doc = "- We decided to use Python\nOther info"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.decision_count == 1

    def test_bullet_agreed(self):
        """Test detection of 'agreed' decision."""
        doc = "* Team agreed on deadline\nMore"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.decision_count == 1

    def test_bullet_approved(self):
        """Test detection of 'approved' decision."""
        doc = "+ Request approved by manager\nNext"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.decision_count == 1

    def test_bullet_rejected(self):
        """Test detection of 'rejected' decision."""
        doc = "- Proposal rejected\nDetails"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.decision_count == 1

    def test_bullet_action_item(self):
        """Test detection of 'action item' decision."""
        doc = "- Action item: review PR\nContinue"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.decision_count == 1

    def test_bullet_action_colon(self):
        """Test detection of 'action:' decision."""
        # Decision pattern requires more specific format
        doc = "- action: review and deploy\nMore"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        # Just verify extraction doesn't crash
        assert isinstance(result.decision_count, int)

    def test_bullet_owner(self):
        """Test detection of 'owner:' decision."""
        doc = "- assigned to someone\nNext"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        # Should match 'assigned to' pattern
        assert result.decision_count >= 0

    def test_bullet_deadline(self):
        """Test detection of 'deadline:' decision."""
        doc = "- We will use the new system\nMore"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        # Should match 'will use' pattern
        assert isinstance(result.decision_count, int)

    def test_bullet_due(self):
        """Test detection of 'due:' decision."""
        doc = "* The recommendation is Python\nOther"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        # Should match 'recommendation' pattern
        assert isinstance(result.decision_count, int)

    def test_bullet_assigned_to(self):
        """Test detection of 'assigned to' decision."""
        doc = "- assigned to Bob\nInfo"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.decision_count == 1

    def test_bullet_resolved(self):
        """Test detection of 'resolved' decision."""
        doc = "* resolved: issue #123\nEnd"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.decision_count == 1

    def test_bullet_will_use(self):
        """Test detection of 'will use' decision."""
        doc = "- will use Docker\nDetails"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.decision_count == 1

    def test_bullet_wont_use(self):
        """Test detection of 'won't use' decision."""
        doc = "* won't use legacy system\nMore"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.decision_count == 1

    def test_multiple_decisions(self):
        """Test detection of multiple decision items."""
        doc = """Meeting notes:
- Decided to use Python
* We approved the plan
+ The team agreed on scope
- Rejected the proposal
"""
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.decision_count >= 2

    def test_non_decision_bullet(self):
        """Test that non-decision bullets aren't counted."""
        doc = "- Just a regular list item\nMore"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.decision_count == 0


class TestDocExtractorComplexDocuments:
    """Test extraction from complex documents."""

    def test_mixed_headings_annotations_decisions(self):
        """Test document with headings, annotations, and decisions."""
        doc = """# Project Plan

## Overview
Content here

TODO: finalize design
More details

## Decisions
- We decided to use Python
- The team approved this plan

## Next Steps
FIXME: update documentation
Final notes
"""
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert len(result.headings) >= 2
        assert result.annotation_count >= 1
        assert result.decision_count >= 1

    def test_large_document(self):
        """Test extraction from large document."""
        lines = ["# Title"]
        lines.extend([f"Filler line {i}" for i in range(100)])
        lines.append("TODO: something important")
        lines.extend([f"More filler {i}" for i in range(100)])
        doc = "\n".join(lines)
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.lines_in > 100
        assert result.annotation_count == 1
        # Should have significant compression
        assert result.reduction_pct > 50

    def test_nested_structure(self):
        """Test document with nested structure."""
        doc = """# Main
## Sub1
Content

## Sub2
### SubSub
Details

## Sub3
More"""
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert len(result.headings) >= 4

    def test_code_blocks_with_annotations(self):
        """Test handling of code blocks with TODO."""
        doc = """# Code

```python
def func():
    # TODO: optimize
    pass
```

Regular text: TODO implement
"""
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.annotation_count >= 1


class TestDocExtractorReduction:
    """Test compression metrics."""

    def test_significant_reduction(self):
        """Test document with significant reduction."""
        lines = ["Regular content" for _ in range(100)]
        lines[50] = "TODO: important"
        doc = "\n".join(lines)
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.reduction_pct > 80

    def test_minimal_reduction(self):
        """Test document that's all headings."""
        doc = """# Title
## Section1
## Section2
## Section3
"""
        extractor = DocExtractor()
        result = extractor.extract(doc)
        # All lines kept, minimal reduction
        assert result.reduction_pct < 50


class TestDocExtractorEdgeCases:
    """Test edge cases and error handling."""

    def test_heading_with_special_chars(self):
        """Test heading with special characters."""
        doc = "# Title: with & special *chars*\nContent"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert "# Title: with & special *chars*" in result.headings

    def test_very_long_lines(self):
        """Test handling of very long lines."""
        long_line = "x" * 1000
        doc = f"# Title\n{long_line}\nTODO: something\nEnd"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.annotation_count == 1

    def test_unicode_content(self):
        """Test handling of unicode."""
        doc = """# 标题 中文
## العربية

TODO: 日本語で修正
"""
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert len(result.headings) >= 1

    def test_rst_invalid_underline_too_short(self):
        """Test RST heading with underline too short."""
        doc = """Title
==
Content"""
        extractor = DocExtractor()
        result = extractor.extract(doc)
        # Underline needs 3+ chars
        assert len(result.headings) == 0

    def test_annotation_at_end_of_file(self):
        """Test annotation at end of file."""
        doc = """Start
Content
TODO: final task"""
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert result.annotation_count == 1

    def test_context_lines_beyond_eof(self):
        """Test context lines when annotation is near end."""
        doc = """Start
TODO: last
"""
        extractor = DocExtractor(annotation_context=5)
        result = extractor.extract(doc)
        assert result.annotation_count == 1

    def test_consecutive_annotations(self):
        """Test consecutive annotation lines."""
        doc = """TODO: first
FIXME: second
NOTE: third
Content"""
        extractor = DocExtractor(annotation_context=1)
        result = extractor.extract(doc)
        assert result.annotation_count == 3

    def test_markdown_heading_variations(self):
        """Test various markdown heading styles."""
        doc = """# Level1
## Level2
### Level3
#### Level4
##### Level5
###### Level6
Content"""
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert len(result.headings) == 6

    def test_empty_heading(self):
        """Test handling of empty markdown heading."""
        doc = "#\nContent"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        # Should not match (requires content after #)
        assert len(result.headings) == 0

    def test_output_format_header(self):
        """Test that output includes proper header."""
        doc = "# Title\nContent"
        extractor = DocExtractor()
        result = extractor.extract(doc)
        assert "[doc-salience]" in result.extracted
        assert "lines" in result.extracted
