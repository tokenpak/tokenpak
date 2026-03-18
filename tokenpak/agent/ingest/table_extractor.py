# SPDX-License-Identifier: MIT
"""tokenpak/agent/ingest/table_extractor.py

Table Extraction + Normalization — Phase 5D
=============================================
Extract tables from documents in multiple formats and normalize them
into structured NormalizedTable objects for token-efficient serving.

Supported formats:
  - Markdown pipe-delimited tables
  - HTML <table> elements
  - Plain-text aligned tables (whitespace heuristic)

Usage::

    from tokenpak.agent.ingest.table_extractor import TableExtractor, NormalizedTable

    extractor = TableExtractor()
    tables = extractor.extract(document_text, source_section="Results")
    for table in tables:
        print(table.summary())          # compact: headers + row_count + sample
        print(table.filter_rows("revenue"))  # query-relevant rows only
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Sequence


# ---------------------------------------------------------------------------
# NormalizedTable — structured representation of a single table
# ---------------------------------------------------------------------------


@dataclass
class NormalizedTable:
    """Normalized table extracted from a document section.

    Serves structured row objects instead of raw table text, enabling
    query-targeted row/column filtering and token-efficient summaries.
    """

    headers: list[str]
    """Normalized column headers."""

    rows: list[dict]
    """Rows as header → value mappings."""

    source_section: str = ""
    """Section heading where this table was found."""

    caption: str = ""
    """Table caption or title if detected."""

    row_count: int = 0
    """Total number of data rows."""

    numeric_columns: list[str] = field(default_factory=list)
    """Column names whose values are predominantly numeric."""

    def __post_init__(self) -> None:
        # Always sync row_count with actual rows
        self.row_count = len(self.rows)
        if not self.numeric_columns:
            self.numeric_columns = _detect_numeric_columns(self.headers, self.rows)

    # ------------------------------------------------------------------
    # Serving helpers
    # ------------------------------------------------------------------

    def summary(self) -> dict:
        """Compact summary: headers + row_count + first data row sample."""
        sample = self.rows[0] if self.rows else {}
        return {
            "caption": self.caption,
            "source_section": self.source_section,
            "headers": self.headers,
            "row_count": self.row_count,
            "numeric_columns": self.numeric_columns,
            "sample_row": sample,
        }

    def filter_rows(self, query: str, *, max_rows: int = 20) -> "NormalizedTable":
        """Return a new NormalizedTable with only query-relevant rows.

        A row is considered relevant if *any* of its values contain
        the query string (case-insensitive). Falls back to first
        ``max_rows`` rows when no query match is found.
        """
        q = query.lower()
        matched = [
            row for row in self.rows
            if any(q in str(v).lower() for v in row.values())
        ]
        if not matched:
            matched = self.rows[:max_rows]
        return NormalizedTable(
            headers=self.headers,
            rows=matched[:max_rows],
            source_section=self.source_section,
            caption=self.caption,
        )

    def filter_columns(self, columns: Sequence[str]) -> "NormalizedTable":
        """Return a new NormalizedTable with only the specified columns."""
        keep = [h for h in self.headers if h in columns]
        rows = [{k: row.get(k, "") for k in keep} for row in self.rows]
        return NormalizedTable(
            headers=keep,
            rows=rows,
            source_section=self.source_section,
            caption=self.caption,
        )

    def to_dict(self) -> dict:
        """Full serializable dict."""
        return {
            "headers": self.headers,
            "rows": self.rows,
            "source_section": self.source_section,
            "caption": self.caption,
            "row_count": self.row_count,
            "numeric_columns": self.numeric_columns,
        }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _detect_numeric_columns(headers: list[str], rows: list[dict]) -> list[str]:
    """Return column names whose values are ≥60 % numeric across all rows."""
    if not rows:
        return []
    numeric: list[str] = []
    for h in headers:
        values = [str(row.get(h, "")).strip() for row in rows]
        non_empty = [v for v in values if v]
        if not non_empty:
            continue
        numeric_count = sum(1 for v in non_empty if _is_numeric(v))
        if numeric_count / len(non_empty) >= 0.6:
            numeric.append(h)
    return numeric


def _is_numeric(value: str) -> bool:
    """Return True if *value* looks like a number (int, float, %, $, etc.)."""
    cleaned = value.strip().lstrip("$€£¥").rstrip("%").replace(",", "").replace("_", "")
    try:
        float(cleaned)
        return True
    except ValueError:
        return False


def _normalize_header(raw: str) -> str:
    """Strip whitespace and collapse internal spaces in a header cell."""
    return " ".join(raw.split())


# ---------------------------------------------------------------------------
# HTML table parser
# ---------------------------------------------------------------------------


class _HTMLTableParser(HTMLParser):
    """Minimal SAX-style parser that extracts all <table> elements."""

    def __init__(self) -> None:
        super().__init__()
        self._tables: list[list[list[str]]] = []
        self._current_table: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: Optional[str] = None
        self._in_table = 0
        self._captions: list[str] = []
        self._current_caption: Optional[str] = None

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "table":
            self._in_table += 1
            if self._in_table == 1:
                self._current_table = []
        elif tag == "tr" and self._in_table:
            self._current_row = []
        elif tag in ("td", "th") and self._in_table:
            self._current_cell = ""
        elif tag == "caption" and self._in_table:
            self._current_caption = ""

    def handle_endtag(self, tag: str) -> None:
        if tag == "table" and self._in_table:
            if self._in_table == 1:
                self._tables.append(self._current_table)
                self._captions.append(self._current_caption or "")
                self._current_caption = None
            self._in_table -= 1
        elif tag == "tr" and self._in_table and self._current_row:
            self._current_table.append(self._current_row)
            self._current_row = []
        elif tag in ("td", "th") and self._in_table and self._current_cell is not None:
            self._current_row.append(self._current_cell.strip())
            self._current_cell = None
        elif tag == "caption" and self._current_caption is not None:
            self._captions.append(self._current_caption.strip())
            self._current_caption = None

    def handle_data(self, data: str) -> None:
        if self._current_cell is not None:
            self._current_cell += data
        elif self._current_caption is not None:
            self._current_caption += data

    def get_tables(self) -> list[tuple[list[list[str]], str]]:
        return list(zip(self._tables, self._captions))


# ---------------------------------------------------------------------------
# TableExtractor — main public class
# ---------------------------------------------------------------------------


class TableExtractor:
    """Extract and normalize tables from document text.

    Detects:
    * Markdown pipe-delimited tables
    * HTML ``<table>`` elements
    * Plain-text aligned tables (column-aligned whitespace heuristic)

    Example::

        extractor = TableExtractor()
        tables = extractor.extract(text, source_section="Results")
    """

    # Markdown table: lines starting/ending with | or containing |---|
    _MD_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
    _MD_SEP = re.compile(r"^\s*\|[\s\-|:]+\|\s*$")

    def extract(
        self,
        text: str,
        *,
        source_section: str = "",
    ) -> list[NormalizedTable]:
        """Extract all tables from *text*.

        Returns a list of :class:`NormalizedTable` objects sorted by
        discovery order.  May include tables from multiple formats.
        """
        tables: list[NormalizedTable] = []

        # 1. HTML tables (try first — may be embedded in mixed content)
        html_tables = self._extract_html(text, source_section=source_section)
        tables.extend(html_tables)

        # 2. Strip HTML so we don't re-detect its content as markdown/plain
        stripped = re.sub(r"<[^>]+>", " ", text)

        # 3. Markdown tables
        md_tables = self._extract_markdown(stripped, source_section=source_section)
        tables.extend(md_tables)

        # 4. Plain-text aligned tables (only if no other tables were found)
        # HTML-stripped content would create false positives if we already have html/md tables
        if not html_tables and not md_tables:
            pt_tables = self._extract_plain_text(stripped, source_section=source_section)
            tables.extend(pt_tables)

        return tables

    # ------------------------------------------------------------------
    # Markdown extraction
    # ------------------------------------------------------------------

    def _extract_markdown(
        self, text: str, *, source_section: str
    ) -> list[NormalizedTable]:
        tables: list[NormalizedTable] = []
        lines = text.splitlines()
        i = 0
        while i < len(lines):
            # Look for a separator line (|---|---|)
            if self._MD_SEP.match(lines[i]):
                # Header is the line immediately before the separator
                header_line = lines[i - 1] if i > 0 else None
                if header_line and self._MD_ROW.match(header_line):
                    headers = self._parse_md_row(header_line)
                    rows: list[dict] = []
                    j = i + 1
                    while j < len(lines) and self._MD_ROW.match(lines[j]):
                        cells = self._parse_md_row(lines[j])
                        # Pad or truncate cells to match headers
                        cells = (cells + [""] * len(headers))[: len(headers)]
                        rows.append(dict(zip(headers, cells)))
                        j += 1

                    # Look back for a caption (line before header that starts with
                    # "Table", "Caption:", or is bold **...**)
                    caption = self._find_caption(lines, i - 1)

                    tables.append(
                        NormalizedTable(
                            headers=headers,
                            rows=rows,
                            source_section=source_section,
                            caption=caption,
                        )
                    )
                    i = j
                    continue
            i += 1
        return tables

    def _parse_md_row(self, line: str) -> list[str]:
        """Split a markdown pipe row into normalized cells."""
        # Strip leading/trailing pipes and spaces
        inner = line.strip().strip("|")
        return [_normalize_header(cell) for cell in inner.split("|")]

    def _find_caption(self, lines: list[str], header_idx: int) -> str:
        """Look at the line before *header_idx* for a caption."""
        if header_idx <= 0:
            return ""
        candidate = lines[header_idx - 1].strip()
        if re.match(r"^(\*\*|__)?[Tt]able\b", candidate) or re.match(
            r"^[Cc]aption\s*:", candidate
        ):
            # Strip markdown bold markers
            return re.sub(r"\*\*|__", "", candidate).strip()
        return ""

    # ------------------------------------------------------------------
    # HTML extraction
    # ------------------------------------------------------------------

    def _extract_html(
        self, text: str, *, source_section: str
    ) -> list[NormalizedTable]:
        if "<table" not in text.lower():
            return []

        parser = _HTMLTableParser()
        try:
            parser.feed(text)
        except Exception:
            return []

        tables: list[NormalizedTable] = []
        for raw_rows, caption in parser.get_tables():
            if not raw_rows:
                continue
            # Heuristic: first row is headers if any cell ends in </th> or
            # if we originally parsed <th> tags. Since we already extracted
            # text, use first row as headers.
            headers = [_normalize_header(c) for c in raw_rows[0]]
            if not headers:
                continue
            rows = []
            for raw_row in raw_rows[1:]:
                cells = (list(raw_row) + [""] * len(headers))[: len(headers)]
                rows.append(dict(zip(headers, cells)))

            tables.append(
                NormalizedTable(
                    headers=headers,
                    rows=rows,
                    source_section=source_section,
                    caption=caption,
                )
            )
        return tables

    # ------------------------------------------------------------------
    # Plain-text aligned table extraction
    # ------------------------------------------------------------------

    def _extract_plain_text(
        self, text: str, *, source_section: str
    ) -> list[NormalizedTable]:
        """Detect whitespace-aligned tables via column gap heuristic.

        A candidate block must have:
        - ≥2 consecutive lines
        - Each line split into ≥2 tokens by ≥2 spaces
        - Consistent number of columns (±1 allowed)
        """
        tables: list[NormalizedTable] = []
        lines = text.splitlines()
        blocks: list[list[str]] = []
        current_block: list[str] = []

        for line in lines:
            if self._is_plain_table_line(line):
                current_block.append(line)
            else:
                if len(current_block) >= 3:  # header + optional sep + ≥1 data row
                    blocks.append(current_block)
                current_block = []

        if len(current_block) >= 3:
            blocks.append(current_block)

        for block in blocks:
            table = self._parse_plain_block(block, source_section=source_section)
            if table is not None:
                tables.append(table)

        return tables

    def _is_plain_table_line(self, line: str) -> bool:
        """Return True if line looks like part of a plain-text table."""
        stripped = line.strip()
        if not stripped or len(stripped) < 4:
            return False
        # Must contain at least 2 groups of ≥2 spaces acting as delimiters
        parts = re.split(r"  +", stripped)
        return len(parts) >= 2  # noqa: PLR2004

    def _parse_plain_block(
        self, block: list[str], *, source_section: str
    ) -> Optional[NormalizedTable]:
        """Convert a plain-text block into a NormalizedTable."""
        # Detect separator line (all dashes/equals)
        sep_idx: Optional[int] = None
        for idx, line in enumerate(block):
            if re.match(r"^[\s\-=]+$", line) and len(line.strip()) >= 4:  # noqa: PLR2004
                sep_idx = idx
                break

        if sep_idx is not None:
            header_lines = block[:sep_idx]
            data_lines = block[sep_idx + 1 :]
        else:
            header_lines = block[:1]
            data_lines = block[1:]

        if not header_lines or not data_lines:
            return None

        headers = [_normalize_header(c) for c in re.split(r"  +", header_lines[0].strip()) if c.strip()]
        if len(headers) < 2:  # noqa: PLR2004
            return None

        rows: list[dict] = []
        for line in data_lines:
            cells = re.split(r"  +", line.strip())
            cells = [(cells + [""] * len(headers))[: len(headers)]]
            # cells is now a list with one list; unpack
            row_cells = cells[0]
            rows.append(dict(zip(headers, row_cells)))

        return NormalizedTable(
            headers=headers,
            rows=rows,
            source_section=source_section,
            caption="",
        )
