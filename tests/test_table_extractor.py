# SPDX-License-Identifier: MIT
"""tests/test_table_extractor.py

Tests for tokenpak.agent.ingest.table_extractor (Phase 5D).

Covers:
  - Markdown table extraction + normalization
  - HTML table extraction + caption
  - Numeric column detection
  - filter_rows / filter_columns / summary / to_dict helpers
  - Plain-text aligned table extraction
  - Multiple tables in one document
  - Edge cases: empty table, single-column, query fallback
"""
from __future__ import annotations

import pytest

from tokenpak.agent.ingest.table_extractor import (
    NormalizedTable,
    TableExtractor,
    _detect_numeric_columns,
    _is_numeric,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MARKDOWN_DOC = """\
## Results

| Quarter | Revenue | Growth |
|---------|---------|--------|
| Q1 2025 | $1.2M   | 12%    |
| Q2 2025 | $1.5M   | 25%    |
| Q3 2025 | $1.8M   | 20%    |
"""

HTML_DOC = """\
<table>
  <caption>Sales by Region</caption>
  <tr><th>Region</th><th>Sales</th><th>Units</th></tr>
  <tr><td>North</td><td>500000</td><td>1200</td></tr>
  <tr><td>South</td><td>320000</td><td>800</td></tr>
  <tr><td>East</td><td>410000</td><td>950</td></tr>
</table>
"""

MULTI_TABLE_DOC = """\
| Name | Score |
|------|-------|
| Alice | 95    |
| Bob   | 82    |

Some prose in between.

| City | Population | Area |
|------|------------|------|
| NYC  | 8336817    | 302  |
| LA   | 3979576    | 503  |
"""

PLAIN_TEXT_DOC = """\
Name  Age  City
----  ---  ----
Alice  30  London
Bob    25  Paris
Carol  35  Berlin
"""


# ---------------------------------------------------------------------------
# Test 1: Markdown table extraction
# ---------------------------------------------------------------------------

class TestMarkdownExtraction:
    def test_basic_extraction(self):
        extractor = TableExtractor()
        tables = extractor.extract(MARKDOWN_DOC, source_section="Results")
        assert len(tables) == 1
        t = tables[0]
        assert t.headers == ["Quarter", "Revenue", "Growth"]
        assert t.row_count == 3
        assert t.source_section == "Results"

    def test_row_contents(self):
        extractor = TableExtractor()
        tables = extractor.extract(MARKDOWN_DOC)
        rows = tables[0].rows
        assert rows[0]["Quarter"] == "Q1 2025"
        assert rows[1]["Revenue"] == "$1.5M"
        assert rows[2]["Growth"] == "20%"

    def test_multiple_tables(self):
        extractor = TableExtractor()
        tables = extractor.extract(MULTI_TABLE_DOC)
        assert len(tables) == 2
        assert tables[0].headers == ["Name", "Score"]
        assert tables[1].headers == ["City", "Population", "Area"]


# ---------------------------------------------------------------------------
# Test 2: HTML table extraction
# ---------------------------------------------------------------------------

class TestHTMLExtraction:
    def test_basic_extraction(self):
        extractor = TableExtractor()
        tables = extractor.extract(HTML_DOC, source_section="Appendix")
        assert len(tables) == 1
        t = tables[0]
        assert "Region" in t.headers
        assert "Sales" in t.headers
        assert t.row_count == 3

    def test_caption_extracted(self):
        extractor = TableExtractor()
        tables = extractor.extract(HTML_DOC)
        assert tables[0].caption == "Sales by Region"

    def test_source_section_propagated(self):
        extractor = TableExtractor()
        tables = extractor.extract(HTML_DOC, source_section="Appendix")
        assert tables[0].source_section == "Appendix"

    def test_numeric_columns_detected(self):
        extractor = TableExtractor()
        tables = extractor.extract(HTML_DOC)
        assert "Sales" in tables[0].numeric_columns
        assert "Units" in tables[0].numeric_columns


# ---------------------------------------------------------------------------
# Test 3: NormalizedTable serving helpers
# ---------------------------------------------------------------------------

class TestNormalizedTableHelpers:
    @pytest.fixture
    def table(self):
        return NormalizedTable(
            headers=["Region", "Sales", "Units"],
            rows=[
                {"Region": "North", "Sales": "500000", "Units": "1200"},
                {"Region": "South", "Sales": "320000", "Units": "800"},
                {"Region": "East",  "Sales": "410000", "Units": "950"},
            ],
            source_section="Appendix",
            caption="Sales by Region",
        )

    def test_filter_rows_match(self, table):
        result = table.filter_rows("North")
        assert result.row_count == 1
        assert result.rows[0]["Region"] == "North"

    def test_filter_rows_fallback(self, table):
        """No match → returns first max_rows rows."""
        result = table.filter_rows("Nonexistent", max_rows=2)
        assert result.row_count == 2

    def test_filter_columns(self, table):
        slim = table.filter_columns(["Region", "Sales"])
        assert slim.headers == ["Region", "Sales"]
        assert "Units" not in slim.rows[0]

    def test_summary_keys(self, table):
        s = table.summary()
        for key in ("caption", "source_section", "headers", "row_count", "numeric_columns", "sample_row"):
            assert key in s

    def test_summary_row_count(self, table):
        assert table.summary()["row_count"] == 3

    def test_to_dict(self, table):
        d = table.to_dict()
        assert d["row_count"] == 3
        assert d["headers"] == ["Region", "Sales", "Units"]
        assert len(d["rows"]) == 3


# ---------------------------------------------------------------------------
# Test 4: Numeric column detection
# ---------------------------------------------------------------------------

class TestNumericDetection:
    def test_is_numeric_int(self):
        assert _is_numeric("42")
        assert _is_numeric("1,234")

    def test_is_numeric_float(self):
        assert _is_numeric("3.14")
        assert _is_numeric("1_000.5")

    def test_is_numeric_currency(self):
        assert _is_numeric("$1.50")
        assert _is_numeric("€3,200")

    def test_is_numeric_percent(self):
        assert _is_numeric("12%")

    def test_is_numeric_text(self):
        assert not _is_numeric("Alice")
        assert not _is_numeric("Q1 2025")
        assert not _is_numeric("")

    def test_detect_numeric_columns(self):
        headers = ["Name", "Score", "Rank"]
        rows = [
            {"Name": "Alice", "Score": "95", "Rank": "1"},
            {"Name": "Bob",   "Score": "82", "Rank": "2"},
            {"Name": "Carol", "Score": "78", "Rank": "3"},
        ]
        result = _detect_numeric_columns(headers, rows)
        assert "Score" in result
        assert "Rank" in result
        assert "Name" not in result

    def test_mixed_numeric_threshold(self):
        """Column with <60% numeric values should NOT be flagged."""
        headers = ["Value"]
        rows = [
            {"Value": "100"},
            {"Value": "n/a"},
            {"Value": "text"},
            {"Value": "text2"},
        ]
        result = _detect_numeric_columns(headers, rows)
        # Only 1/4 = 25% numeric → should NOT be in result
        assert "Value" not in result


# ---------------------------------------------------------------------------
# Test 5: Plain-text table extraction
# ---------------------------------------------------------------------------

class TestPlainTextExtraction:
    def test_basic_extraction(self):
        extractor = TableExtractor()
        tables = extractor.extract(PLAIN_TEXT_DOC, source_section="Staff")
        # Should extract at least 1 plain-text table
        assert len(tables) >= 1

    def test_plain_text_headers(self):
        extractor = TableExtractor()
        tables = extractor.extract(PLAIN_TEXT_DOC)
        if tables:
            assert "Name" in tables[0].headers

    def test_plain_text_rows(self):
        extractor = TableExtractor()
        tables = extractor.extract(PLAIN_TEXT_DOC)
        if tables:
            assert tables[0].row_count >= 1


# ---------------------------------------------------------------------------
# Test 6: Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_no_table_in_text(self):
        extractor = TableExtractor()
        tables = extractor.extract("Just some prose with no tables here.")
        assert tables == []

    def test_empty_string(self):
        extractor = TableExtractor()
        tables = extractor.extract("")
        assert tables == []

    def test_normalized_table_row_count_sync(self):
        """row_count always matches actual rows length."""
        t = NormalizedTable(
            headers=["A", "B"],
            rows=[{"A": "x", "B": "y"}, {"A": "p", "B": "q"}],
            row_count=99,  # intentionally wrong — __post_init__ should fix
        )
        assert t.row_count == 2

    def test_filter_rows_empty_table(self):
        t = NormalizedTable(headers=["X"], rows=[], row_count=0)
        result = t.filter_rows("anything")
        assert result.row_count == 0

    def test_numeric_columns_empty_rows(self):
        t = NormalizedTable(headers=["X", "Y"], rows=[])
        assert t.numeric_columns == []
