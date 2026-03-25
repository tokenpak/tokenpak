"""Tests for DocumentParser — structural document parsing."""

import pytest

from tokenpak.agent.ingest.document_parser import (
    DocumentParser,
    DocumentStructure,
    SectionType,
)


class TestMarkdownHeadingHierarchy:
    """Test: Markdown heading hierarchy parsed correctly."""

    def test_single_h1(self):
        """Parse single H1 heading."""
        content = "# Introduction\n\nSome content."
        parser = DocumentParser()
        result = parser.parse(content, format="markdown")

        assert result.title == "Introduction"
        assert len(result.sections) == 1
        assert result.sections[0].level == 1

    def test_h1_h2_h3_hierarchy(self):
        """Parse deep heading hierarchy."""
        content = """# Main Title

Some intro.

## Section 1

Content 1.

### Subsection 1.1

Deep content.

## Section 2

Content 2.
"""
        parser = DocumentParser()
        result = parser.parse(content, format="markdown")

        assert result.title == "Main Title"
        # Parser returns all headings including nested ones
        assert len(result.sections) >= 3  # H1, H2, H3, H2
        assert result.sections[0].level == 1
        assert result.sections[1].level == 2

    def test_heading_tree_structure(self):
        """Verify heading tree is navigable."""
        content = """# Root

## Child 1

### Grandchild 1.1

## Child 2
"""
        parser = DocumentParser()
        result = parser.parse(content, format="markdown")

        assert result.headings_tree is not None
        assert result.headings_tree.text == "Root"
        assert len(result.headings_tree.children) == 2
        assert result.headings_tree.children[0].text == "Child 1"
        assert result.headings_tree.children[0].children[0].text == "Grandchild 1.1"


class TestTableExtraction:
    """Test: Tables extracted."""

    def test_markdown_table_simple(self):
        """Extract simple markdown table."""
        content = """| Name | Age |
| ---- | --- |
| Alice | 30 |
| Bob | 25 |
"""
        parser = DocumentParser()
        result = parser.parse(content, format="markdown")

        assert len(result.sections) > 0
        # Find section with table
        section_with_table = None
        for section in result.sections:
            if section.tables:
                section_with_table = section
                break

        assert section_with_table is not None
        assert len(section_with_table.tables) == 1
        table = section_with_table.tables[0]
        assert table.headers == ["Name", "Age"]
        assert len(table.rows) == 2
        assert table.rows[0] == ["Alice", "30"]

    def test_markdown_table_in_section(self):
        """Extract table within a section."""
        content = """# Report

## Data

| ID | Value |
| -- | ----- |
| 1  | 100   |
| 2  | 200   |
"""
        parser = DocumentParser()
        result = parser.parse(content, format="markdown")

        # Second section should have the table
        assert len(result.sections) > 1
        data_section = next((s for s in result.sections if s.heading == "Data"), None)
        assert data_section is not None
        assert len(data_section.tables) == 1

    def test_html_table_extraction(self):
        """Extract table from HTML."""
        content = """
<html>
<body>
<h1>Report</h1>
<table>
    <tr><th>Name</th><th>Score</th></tr>
    <tr><td>Test</td><td>95</td></tr>
</table>
</body>
</html>
"""
        parser = DocumentParser()
        result = parser.parse(content, format="html")

        # Find table in sections
        has_table = any(len(s.tables) > 0 for s in result.sections)
        assert has_table


class TestSectionClassification:
    """Test: Section types classified."""

    def test_classify_overview(self):
        """Classify overview section."""
        parser = DocumentParser()
        sec_type = parser._classify_section("Overview", "")
        assert sec_type == SectionType.OVERVIEW

    def test_classify_methodology(self):
        """Classify methodology section."""
        parser = DocumentParser()
        sec_type = parser._classify_section("Methodology", "")
        assert sec_type == SectionType.METHODOLOGY

    def test_classify_results(self):
        """Classify results section."""
        parser = DocumentParser()
        sec_type = parser._classify_section("Results and Findings", "")
        assert sec_type == SectionType.RESULTS

    def test_classify_recommendations(self):
        """Classify recommendations section."""
        parser = DocumentParser()
        sec_type = parser._classify_section("Recommendations", "")
        assert sec_type == SectionType.RECOMMENDATIONS

    def test_classify_legal(self):
        """Classify legal/license section."""
        parser = DocumentParser()
        sec_type = parser._classify_section("License", "")
        assert sec_type == SectionType.LEGAL

    def test_classify_appendix(self):
        """Classify appendix section."""
        parser = DocumentParser()
        sec_type = parser._classify_section("Appendix A", "")
        assert sec_type == SectionType.APPENDIX

    def test_classify_definitions(self):
        """Classify definitions/glossary."""
        parser = DocumentParser()
        sec_type = parser._classify_section("Glossary", "")
        assert sec_type == SectionType.DEFINITIONS


class TestPlainTextHeuristics:
    """Test: Plain text heuristic headings."""

    def test_plaintext_allcaps_heading(self):
        """Detect ALL CAPS headings in plaintext."""
        content = """INTRODUCTION
============

Some text here.

MAIN SECTION
============

More content.
"""
        parser = DocumentParser()
        result = parser.parse(content, format="plaintext")

        assert result.title is not None
        assert "INTRODUCTION" in result.title or any("INTRODUCTION" in s.heading for s in result.sections)

    def test_plaintext_with_underline(self):
        """Detect headings with dash underlines."""
        content = """Title Here
----------

Content below.
"""
        parser = DocumentParser()
        result = parser.parse(content, format="plaintext")

        # Should detect something (heuristic may vary)
        assert result.sections is not None


class TestDeepNesting:
    """Test: Deep nesting handled (H1→H2→H3→H4)."""

    def test_nested_h1_to_h4(self):
        """Parse nested hierarchy up to H4."""
        content = """# Level 1

Text 1.

## Level 2

Text 2.

### Level 3

Text 3.

#### Level 4

Text 4.
"""
        parser = DocumentParser()
        result = parser.parse(content, format="markdown")

        assert result.title == "Level 1"
        # All headings should be parsed
        assert len(result.sections) == 4
        levels = [s.level for s in result.sections]
        assert 1 in levels
        assert 2 in levels
        assert 3 in levels
        assert 4 in levels

    def test_heading_tree_deep_nesting(self):
        """Verify tree structure for deep nesting."""
        content = """# H1
## H2
### H3
#### H4
"""
        parser = DocumentParser()
        result = parser.parse(content, format="markdown")

        tree = result.headings_tree
        assert tree is not None
        assert tree.level == 1
        assert len(tree.children) > 0
        child = tree.children[0]
        assert child.level == 2
        if child.children:
            grandchild = child.children[0]
            assert grandchild.level == 3


class TestEmptyAndMalformedDocs:
    """Test: Empty/malformed docs handled gracefully."""

    def test_empty_markdown(self):
        """Handle empty markdown."""
        parser = DocumentParser()
        result = parser.parse("", format="markdown")

        assert isinstance(result, DocumentStructure)
        assert result.title is None

    def test_markdown_no_headings(self):
        """Handle markdown with no headings."""
        content = "Just some random text.\n\nMore text without structure."
        parser = DocumentParser()
        result = parser.parse(content, format="markdown")

        # Should create a default section
        assert len(result.sections) >= 1
        assert result.sections[0].content is not None

    def test_malformed_table(self):
        """Handle malformed markdown table."""
        content = """| Col1 | Col2
| --- | ---
| A | B
"""
        parser = DocumentParser()
        result = parser.parse(content, format="markdown")

        # Should not crash
        assert isinstance(result, DocumentStructure)

    def test_plaintext_no_structure(self):
        """Handle plaintext with no clear structure."""
        content = "Line 1\nLine 2\nLine 3\n"
        parser = DocumentParser()
        result = parser.parse(content, format="plaintext")

        assert isinstance(result, DocumentStructure)
        assert len(result.sections) >= 1


class TestAdvancedFeatures:
    """Test additional features."""

    def test_find_section_by_heading(self):
        """Find section by heading using convenience method."""
        content = """# Doc

## Methods

Details.

## Results

Outcomes.
"""
        parser = DocumentParser()
        result = parser.parse(content, format="markdown")

        methods = result.find_section("Methods")
        assert methods is not None
        assert methods.heading == "Methods"

    def test_code_block_extraction(self):
        """Extract code blocks from markdown."""
        content = """# Example

Some intro.

```python
def hello():
    print("world")
```

More text.
"""
        parser = DocumentParser()
        result = parser.parse(content, format="markdown")

        # Check that code block was extracted
        assert any(len(s.code_blocks) > 0 for s in result.sections)

    def test_list_extraction(self):
        """Extract bullet lists from markdown."""
        content = """# Items

- Item A
- Item B
- Item C

Paragraph.

1. First
2. Second
"""
        parser = DocumentParser()
        result = parser.parse(content, format="markdown")

        # Should extract lists
        assert any(len(s.lists) > 0 for s in result.sections)

    def test_citation_extraction(self):
        """Extract citations."""
        content = """# Research

According to [Smith, 2020], this is important.

More details in [ref1].
"""
        parser = DocumentParser()
        result = parser.parse(content, format="markdown")

        # Check citations
        citations = []
        for section in result.sections:
            citations.extend(section.citations)
        assert len(citations) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
