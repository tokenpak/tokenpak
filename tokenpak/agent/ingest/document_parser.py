# SPDX-License-Identifier: MIT
"""tokenpak/agent/ingest/document_parser.py

Structural Document Parser — Phase 5E
======================================
Parse prose documents (markdown, HTML, plain text) into navigable
structural representations: headings, sections, tables, footnotes,
citations, and code blocks.

Supports:
  - Markdown (primary): ATX headings, setext headings, tables, code fences
  - HTML: heading tags, section/article elements, tables
  - Plain text: heuristic heading detection by capitalisation + length

Usage::

    from tokenpak.agent.ingest.document_parser import DocumentParser

    parser = DocumentParser()
    doc = parser.parse(markdown_text, fmt="markdown")
    print(doc.title)
    print(doc.heading_tree)
    for section in doc.sections:
        print(section.heading, section.section_type, section.word_count)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class DocumentSection:
    """A single section of a document."""

    heading: str
    """Section heading text."""

    level: int
    """Heading depth: 1 (H1) through 6 (H6). 0 = implicit root."""

    content: str
    """Raw prose content of this section (excludes sub-section content)."""

    subsections: List["DocumentSection"] = field(default_factory=list)
    """Nested child sections."""

    tables: List[dict] = field(default_factory=list)
    """Tables found directly in this section (not sub-sections)."""

    citations: List[str] = field(default_factory=list)
    """Citation strings found in this section."""

    code_blocks: List[str] = field(default_factory=list)
    """Fenced code blocks found in this section."""

    word_count: int = 0
    """Word count of *this* section's content only."""

    section_type: str = "general"
    """Semantic type label: overview, methodology, results, recommendations,
    legal, appendix, definitions, general."""

    def __post_init__(self) -> None:
        if self.word_count == 0 and self.content:
            self.word_count = len(self.content.split())

    def to_dict(self) -> dict:
        return {
            "heading": self.heading,
            "level": self.level,
            "section_type": self.section_type,
            "word_count": self.word_count,
            "content_preview": self.content[:200] if self.content else "",
            "subsections": [s.to_dict() for s in self.subsections],
            "tables": self.tables,
            "citations": self.citations,
            "code_blocks": [c[:120] for c in self.code_blocks],
        }


@dataclass
class DocumentStructure:
    """Full structural representation of a parsed document."""

    title: str
    """Document title (first H1 or inferred)."""

    sections: List[DocumentSection] = field(default_factory=list)
    """Top-level sections."""

    heading_tree: dict = field(default_factory=dict)
    """Navigable heading hierarchy: {heading: {subsections: [...], level: int}}."""

    metadata: dict = field(default_factory=dict)
    """Detected metadata: author, date, type, format."""

    tables: List[dict] = field(default_factory=list)
    """All tables across the document (flattened)."""

    citations: List[str] = field(default_factory=list)
    """All citations across the document (flattened)."""

    total_words: int = 0
    """Total word count across all sections."""

    def __post_init__(self) -> None:
        if not self.total_words and self.sections:
            self.total_words = _sum_words(self.sections)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "total_words": self.total_words,
            "metadata": self.metadata,
            "heading_tree": self.heading_tree,
            "sections": [s.to_dict() for s in self.sections],
            "tables": self.tables,
            "citations": self.citations,
        }


# ---------------------------------------------------------------------------
# Section type classifier
# ---------------------------------------------------------------------------

_SECTION_KEYWORDS: dict[str, list[str]] = {
    "overview": [
        "overview", "introduction", "intro", "summary", "abstract", "background",
        "synopsis", "executive summary", "purpose", "about",
    ],
    "methodology": [
        "method", "methodology", "approach", "process", "procedure", "how",
        "technique", "implementation", "design", "architecture", "workflow",
    ],
    "results": [
        "result", "finding", "outcome", "data", "analysis", "benchmark",
        "performance", "metric", "measurement", "evaluation", "test result",
        "output",
    ],
    "recommendations": [
        "recommendation", "suggest", "proposal", "action", "next step",
        "todo", "to-do", "plan", "roadmap", "future work",
    ],
    "legal": [
        "license", "copyright", "terms", "privacy", "legal", "disclaimer",
        "warranty", "liability", "intellectual property", "ip ",
    ],
    "definitions": [
        "glossary", "definition", "terminology", "term", "vocabulary",
        "abbreviation", "acronym", "key concept",
    ],
    "appendix": [
        "appendix", "annex", "supplement", "additional", "extra", "addendum",
        "index", "reference", "bibliography",
    ],
}


def _classify_section(heading: str, content: str) -> str:
    """Return a semantic section type based on heading + first line of content.

    Heading keywords take priority over content keywords to avoid false matches
    (e.g. "data" in content matching results before appendix heading matches).
    """
    heading_lower = heading.lower()
    # Pass 1: heading-only match (high priority)
    for stype, keywords in _SECTION_KEYWORDS.items():
        for kw in keywords:
            if kw in heading_lower:
                return stype
    # Pass 2: content fallback
    content_lower = content[:200].lower()
    for stype, keywords in _SECTION_KEYWORDS.items():
        for kw in keywords:
            if kw in content_lower:
                return stype
    return "general"


# ---------------------------------------------------------------------------
# Citation extractor
# ---------------------------------------------------------------------------

# Matches: [1], [Smith 2020], (Smith et al., 2020), [^1], footnote markers
_CITATION_RE = re.compile(
    r"""
    \[\^?\d+\]          # [1] or [^1] footnote
    | \[[\w\s,\.]+\d{4}\]  # [Smith 2020] or [Smith et al. 2020]
    | \([\w\s,\.\-]+,\s*\d{4}[a-z]?\)  # (Smith et al., 2020)
    | \[\d+(?:,\s*\d+)*\]  # [1,2,3]
    """,
    re.VERBOSE,
)


def _extract_citations(text: str) -> list[str]:
    return list(dict.fromkeys(_CITATION_RE.findall(text)))  # deduplicated, ordered


# ---------------------------------------------------------------------------
# Word count helpers
# ---------------------------------------------------------------------------


def _count_words(text: str) -> int:
    return len(text.split()) if text.strip() else 0


def _sum_words(sections: list[DocumentSection]) -> int:
    total = 0
    for s in sections:
        total += s.word_count
        total += _sum_words(s.subsections)
    return total


# ---------------------------------------------------------------------------
# Heading tree builder
# ---------------------------------------------------------------------------


def _build_heading_tree(sections: list[DocumentSection]) -> dict:
    """Build a navigable {heading: {level, subsections}} tree."""
    result: dict[str, Any] = {}
    for sec in sections:
        result[sec.heading] = {
            "level": sec.level,
            "section_type": sec.section_type,
            "word_count": sec.word_count,
            "subsections": _build_heading_tree(sec.subsections),
        }
    return result


# ---------------------------------------------------------------------------
# Markdown parser
# ---------------------------------------------------------------------------

# ATX heading: # Heading
_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?$")
# Setext heading: underlined with === or ---
_SETEXT_H1 = re.compile(r"^={3,}\s*$")
_SETEXT_H2 = re.compile(r"^-{3,}\s*$")
# Code fence
_CODE_FENCE_START = re.compile(r"^(`{3,}|~{3,})(\w*)")
# Markdown table row
_MD_TABLE_ROW = re.compile(r"^\|.+\|")
# Markdown table separator
_MD_TABLE_SEP = re.compile(r"^\|[\s\-:|]+\|")


def _parse_markdown(text: str) -> DocumentStructure:
    """Parse markdown text into DocumentStructure."""
    lines = text.splitlines()
    # ---- First pass: identify headings, code fences, table blocks ----
    # Each element: ("heading", level, text, line_no) | ("content", text, line_no)
    tokens: list[tuple] = []
    i = 0
    in_fence = False
    fence_char = ""
    fence_lang = ""
    fence_lines: list[str] = []
    fence_start = 0

    while i < len(lines):
        line = lines[i]

        # Code fence toggle
        fm = _CODE_FENCE_START.match(line)
        if fm and not in_fence:
            in_fence = True
            fence_char = fm.group(1)[0]
            fence_lang = fm.group(2)
            fence_lines = []
            fence_start = i
            i += 1
            continue
        if in_fence:
            if line.startswith(fence_char * 3):
                tokens.append(("code_block", "\n".join(fence_lines), fence_lang, fence_start))
                in_fence = False
                fence_lines = []
            else:
                fence_lines.append(line)
            i += 1
            continue

        # ATX heading
        m = _ATX_HEADING.match(line)
        if m:
            tokens.append(("heading", len(m.group(1)), m.group(2).strip(), i))
            i += 1
            continue

        # Setext heading
        if i + 1 < len(lines):
            next_line = lines[i + 1]
            if _SETEXT_H1.match(next_line) and line.strip():
                tokens.append(("heading", 1, line.strip(), i))
                i += 2
                continue
            if _SETEXT_H2.match(next_line) and line.strip() and not line.startswith("-"):
                tokens.append(("heading", 2, line.strip(), i))
                i += 2
                continue

        tokens.append(("content", line, i))
        i += 1

    # If fence still open at EOF
    if in_fence and fence_lines:
        tokens.append(("code_block", "\n".join(fence_lines), fence_lang, fence_start))

    # ---- Second pass: group tokens into sections ----
    sections: list[DocumentSection] = _group_into_sections(tokens)

    # ---- Metadata ----
    title = _infer_title(sections, text)
    all_tables = _collect_tables(sections)
    all_citations = _collect_citations(sections)
    metadata = _extract_md_metadata(text)
    metadata["format"] = "markdown"

    doc = DocumentStructure(
        title=title,
        sections=sections,
        heading_tree=_build_heading_tree(sections),
        metadata=metadata,
        tables=all_tables,
        citations=all_citations,
    )
    doc.total_words = _sum_words(sections)
    return doc


def _group_into_sections(tokens: list[tuple]) -> list[DocumentSection]:
    """Convert flat token list into nested DocumentSection hierarchy."""
    # Build flat list of (level, heading, content_tokens) first
    flat: list[dict] = []  # {level, heading, content_lines, code_blocks}

    current: dict | None = None
    pending_lines: list[str] = []
    pending_code: list[str] = []
    pending_tables: list[dict] = []
    pre_heading_lines: list[str] = []
    pre_heading_tables: list[dict] = []
    pre_heading_code: list[str] = []
    seen_heading = False

    def flush() -> None:
        if current is not None:
            raw = "\n".join(pending_lines).strip()
            # Merge inline markdown tables with HTML-extracted tables
            md_tables = _extract_md_tables(raw)
            content = _strip_md_tables(raw)
            current["content"] = content
            current["tables"] = md_tables + list(pending_tables)
            current["code_blocks"] = list(pending_code)
            flat.append(current)

    for tok in tokens:
        if tok[0] == "heading":
            if not seen_heading and (pending_lines or pre_heading_lines):
                # Capture content before first heading as implicit intro
                pre_heading_lines = list(pending_lines)
                pre_heading_tables = list(pending_tables)
                pre_heading_code = list(pending_code)
            flush()
            seen_heading = True
            current = {"level": tok[1], "heading": tok[2]}
            pending_lines = []
            pending_code = []
            pending_tables = []
        elif tok[0] == "code_block":
            pending_code.append(tok[1])
        elif tok[0] == "table":
            # HTML table token (dict)
            pending_tables.append(tok[1])
        else:  # content
            pending_lines.append(tok[1] if isinstance(tok[1], str) else str(tok[1]))

    # Flush last section
    flush()

    # If there was content before the first heading, prepend an implicit section
    if pre_heading_lines:
        raw = "\n".join(pre_heading_lines).strip()
        if raw:
            flat.insert(0, {
                "level": 1,
                "heading": raw.splitlines()[0][:80].strip() if raw else "Preamble",
                "content": "\n".join(raw.splitlines()[1:]).strip(),
                "tables": pre_heading_tables,
                "code_blocks": pre_heading_code,
            })
    elif not seen_heading and pending_lines:
        # No headings at all — wrap entire content as single implicit section
        raw = "\n".join(pending_lines).strip()
        if raw:
            flat.append({
                "level": 1,
                "heading": "Document",
                "content": raw,
                "tables": list(pending_tables),
                "code_blocks": list(pending_code),
            })

    return _nest_sections(flat)


def _nest_sections(flat: list[dict]) -> list[DocumentSection]:
    """Turn flat list of {level, heading, content, tables, code_blocks} into tree."""
    if not flat:
        return []

    root_sections: list[DocumentSection] = []
    stack: list[DocumentSection] = []

    for item in flat:
        level = item.get("level", 1)
        content = item.get("content", "")
        code_blocks = item.get("code_blocks", [])
        citations = _extract_citations(content)
        section_type = _classify_section(item["heading"], content)

        sec = DocumentSection(
            heading=item["heading"],
            level=level,
            content=content,
            tables=item.get("tables", []),
            citations=citations,
            code_blocks=code_blocks,
            word_count=_count_words(content),
            section_type=section_type,
        )

        # Pop stack until we find a parent
        while stack and stack[-1].level >= level:
            stack.pop()

        if stack:
            stack[-1].subsections.append(sec)
        else:
            root_sections.append(sec)

        stack.append(sec)

    return root_sections


def _extract_md_tables(text: str) -> list[dict]:
    """Extract markdown pipe tables from text, return list of dicts."""
    tables = []
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        if _MD_TABLE_ROW.match(lines[i]):
            # Collect table block
            block = []
            while i < len(lines) and _MD_TABLE_ROW.match(lines[i]):
                block.append(lines[i])
                i += 1
            table = _parse_md_table_block(block)
            if table:
                tables.append(table)
        else:
            i += 1
    return tables


def _parse_md_table_block(block: list[str]) -> dict | None:
    """Parse a markdown table block into a dict with headers + rows."""
    if len(block) < 2:
        return None
    # First line = headers
    header_line = block[0]
    headers = [c.strip() for c in header_line.strip("|").split("|")]
    headers = [h for h in headers if h]
    if not headers:
        return None
    # Second line = separator — skip
    rows = []
    for line in block[2:]:
        cells = [c.strip() for c in line.strip("|").split("|")]
        # Pad or trim to match headers
        cells = cells[: len(headers)]
        while len(cells) < len(headers):
            cells.append("")
        row = dict(zip(headers, cells))
        rows.append(row)
    return {"headers": headers, "rows": rows, "row_count": len(rows)}


def _strip_md_tables(text: str) -> str:
    """Remove markdown table lines from text."""
    lines = text.splitlines()
    out = []
    for line in lines:
        if not _MD_TABLE_ROW.match(line):
            out.append(line)
    return "\n".join(out)


def _infer_title(sections: list[DocumentSection], raw: str) -> str:
    """Infer document title from first H1 or filename-like first line."""
    for sec in sections:
        if sec.level == 1:
            return sec.heading
    # Try first non-empty line
    for line in raw.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return stripped[:120]
    return "Untitled"


def _collect_tables(sections: list[DocumentSection]) -> list[dict]:
    result = []
    for sec in sections:
        result.extend(sec.tables)
        result.extend(_collect_tables(sec.subsections))
    return result


def _collect_citations(sections: list[DocumentSection]) -> list[str]:
    result = []
    seen: set[str] = set()
    for sec in sections:
        for c in sec.citations:
            if c not in seen:
                seen.add(c)
                result.append(c)
        for c in _collect_citations(sec.subsections):
            if c not in seen:
                seen.add(c)
                result.append(c)
    return result


def _extract_md_metadata(text: str) -> dict:
    """Extract YAML front matter or simple metadata from markdown."""
    meta: dict[str, str] = {}
    lines = text.splitlines()
    if not lines:
        return meta
    # YAML front matter between ---
    if lines[0].strip() == "---":
        for j, line in enumerate(lines[1:], start=1):
            if line.strip() == "---":
                break
            if ":" in line:
                k, _, v = line.partition(":")
                meta[k.strip().lower()] = v.strip().strip('"').strip("'")
    return meta


# ---------------------------------------------------------------------------
# HTML parser
# ---------------------------------------------------------------------------


class _HTMLStructureParser(HTMLParser):
    """Pull headings, tables, and paragraph content from HTML."""

    HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
    BLOCK_TAGS = {"p", "li", "td", "th", "blockquote", "pre", "code"}
    TABLE_STRUCT_TAGS = {"table", "tr", "thead", "tbody", "tfoot", "th", "td"}

    def __init__(self) -> None:
        super().__init__()
        self._tokens: list[tuple] = []
        self._current_tag: str = ""
        self._current_text: list[str] = []
        self._in_table = False
        self._table_rows: list[list[str]] = []
        self._current_row: list[str] = []
        self._current_cell: list[str] = []
        self._table_headers: list[str] = []
        self._in_header_row = False
        self._depth = 0
        self._tag_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        self._tag_stack.append(tag)
        self._current_tag = tag
        if tag in self.HEADING_TAGS:
            self._current_text = []
        elif tag == "table":
            self._in_table = True
            self._table_rows = []
            self._table_headers = []
            self._current_cell = []
        elif tag == "tr":
            self._current_row = []
            self._current_cell = []
        elif tag in ("td", "th"):
            self._current_cell = []
        elif tag in ("p", "li", "blockquote"):
            self._current_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag in self.HEADING_TAGS:
            level = int(tag[1])
            text = "".join(self._current_text).strip()
            if text:
                self._tokens.append(("heading", level, text, 0))
            self._current_text = []
        elif tag == "table":
            if self._in_table:
                table = _html_table_to_dict(self._table_headers, self._table_rows)
                if table:
                    self._tokens.append(("table", table, 0))
            self._in_table = False
            self._table_rows = []
            self._table_headers = []
        elif tag == "tr":
            if self._current_row:
                self._table_rows.append(list(self._current_row))
            self._current_row = []
        elif tag in ("td", "th"):
            cell_text = "".join(self._current_cell).strip()
            self._current_row.append(cell_text)
            self._current_cell = []
        elif tag in ("p", "li", "blockquote"):
            text = "".join(self._current_text).strip()
            if text:
                self._tokens.append(("content", text, 0))
            self._current_text = []
        if self._tag_stack and self._tag_stack[-1] == tag:
            self._tag_stack.pop()

    def handle_data(self, data: str) -> None:
        clean = data.strip()
        if not clean:
            return
        if self._tag_stack and self._tag_stack[-1] in self.HEADING_TAGS:
            self._current_text.append(data)
        elif self._in_table and self._tag_stack and self._tag_stack[-1] in ("td", "th"):
            self._current_cell.append(data)
        elif self._tag_stack and self._tag_stack[-1] in ("p", "li", "blockquote"):
            self._current_text.append(data)

    def get_tokens(self) -> list[tuple]:
        return self._tokens


def _html_table_to_dict(
    headers: list[str], rows: list[list[str]]
) -> dict | None:
    """Convert header list + rows list into normalized dict form."""
    if not rows:
        return None
    # If first row looks like headers, treat it as such
    if not headers and rows:
        headers = rows[0]
        rows = rows[1:]
    if not headers:
        return None
    result_rows = []
    for row in rows:
        row_padded = row[: len(headers)]
        while len(row_padded) < len(headers):
            row_padded.append("")
        result_rows.append(dict(zip(headers, row_padded)))
    return {"headers": headers, "rows": result_rows, "row_count": len(result_rows)}


def _parse_html(text: str) -> DocumentStructure:
    """Parse HTML text into DocumentStructure."""
    parser = _HTMLStructureParser()
    parser.feed(text)
    tokens = parser.get_tokens()

    # Convert table tokens into code_block-style tokens embedded in content
    sections = _group_into_sections(tokens)
    title = _infer_title(sections, text)
    all_tables = _collect_tables(sections)
    all_citations = _collect_citations(sections)

    doc = DocumentStructure(
        title=title,
        sections=sections,
        heading_tree=_build_heading_tree(sections),
        metadata={"format": "html"},
        tables=all_tables,
        citations=all_citations,
    )
    doc.total_words = _sum_words(sections)
    return doc


# ---------------------------------------------------------------------------
# Plain text heuristic parser
# ---------------------------------------------------------------------------

# Plain text heading heuristics:
# - Short line (≤80 chars) that is ALL CAPS
# - Short line followed by a blank line with title-case words
# - Numbered headings: "1.", "1.1", "1.1.1"
_NUMBERED_HEADING = re.compile(r"^(\d+(?:\.\d+)*)\s+(.+)$")
_ALL_CAPS_LINE = re.compile(r"^[A-Z][A-Z\s\d\-:,]+$")


def _parse_plain_text(text: str) -> DocumentStructure:
    """Parse plain text using heuristic heading detection."""
    lines = text.splitlines()
    tokens: list[tuple] = []
    i = 0

    while i < len(lines):
        line = lines[i].rstrip()

        # Numbered heading: "1. Introduction" or "1.1 Background"
        nm = _NUMBERED_HEADING.match(line)
        if nm and len(line) <= 100:
            # Level determined by depth of numbering
            depth = nm.group(1).count(".") + 1
            level = min(depth, 6)
            tokens.append(("heading", level, nm.group(2).strip(), i))
            i += 1
            continue

        # ALL-CAPS short line (likely a heading)
        if _ALL_CAPS_LINE.match(line.strip()) and 3 <= len(line.strip()) <= 80:
            tokens.append(("heading", 2, line.strip().title(), i))
            i += 1
            continue

        # Short line followed by blank line + title-case pattern
        next_blank = (i + 1 < len(lines) and not lines[i + 1].strip())
        if (
            line.strip()
            and len(line.strip()) <= 80
            and next_blank
            and _looks_like_title(line.strip())
            and i > 0
            and not lines[i - 1].strip()
        ):
            tokens.append(("heading", 2, line.strip(), i))
            i += 1
            continue

        tokens.append(("content", line, i))
        i += 1

    sections = _group_into_sections(tokens)
    title = _infer_title(sections, text)
    all_tables = _collect_tables(sections)
    all_citations = _collect_citations(sections)

    doc = DocumentStructure(
        title=title,
        sections=sections,
        heading_tree=_build_heading_tree(sections),
        metadata={"format": "text"},
        tables=all_tables,
        citations=all_citations,
    )
    doc.total_words = _sum_words(sections)
    return doc


def _looks_like_title(line: str) -> bool:
    """Heuristic: does this line look like a section title?"""
    words = line.split()
    if not words:
        return False
    # Most words should be title-case or short connectors
    title_words = sum(
        1
        for w in words
        if w[0].isupper() or w.lower() in ("a", "an", "the", "of", "in", "and", "or", "to")
    )
    return title_words / len(words) >= 0.6


# ---------------------------------------------------------------------------
# Public API: DocumentParser
# ---------------------------------------------------------------------------


class DocumentParser:
    """Parse prose documents into :class:`DocumentStructure`.

    Supports markdown, HTML, and plain text. Format is auto-detected
    when ``fmt`` is ``"auto"`` (default).

    Example::

        parser = DocumentParser()
        doc = parser.parse(content, fmt="markdown")
        print(doc.heading_tree)
        for sec in doc.sections:
            print(sec.heading, sec.section_type)
    """

    def parse(self, text: str, fmt: str = "auto") -> DocumentStructure:
        """Parse *text* and return a :class:`DocumentStructure`.

        Parameters
        ----------
        text:
            Raw document content.
        fmt:
            ``"markdown"``, ``"html"``, ``"text"``, or ``"auto"`` (default).
            When ``"auto"``, format is detected from content.
        """
        if not text or not text.strip():
            return self._empty_doc(fmt)

        resolved_fmt = fmt if fmt != "auto" else self._detect_format(text)

        if resolved_fmt == "html":
            return _parse_html(text)
        elif resolved_fmt == "text":
            return _parse_plain_text(text)
        else:
            return _parse_markdown(text)

    # ------------------------------------------------------------------
    # Format detection
    # ------------------------------------------------------------------

    def _detect_format(self, text: str) -> str:
        """Detect document format from content."""
        stripped = text.strip()
        if stripped.startswith("<!") or stripped.startswith("<html") or re.search(
            r"<(h[1-6]|p|div|table|section)\b", stripped[:2000], re.IGNORECASE
        ):
            return "html"
        if re.search(r"^#{1,6}\s", stripped, re.MULTILINE):
            return "markdown"
        if re.search(r"^\|.+\|", stripped, re.MULTILINE):
            return "markdown"
        if "```" in stripped or "~~~" in stripped:
            return "markdown"
        return "text"

    # ------------------------------------------------------------------
    # Empty doc
    # ------------------------------------------------------------------

    def _empty_doc(self, fmt: str) -> DocumentStructure:
        return DocumentStructure(
            title="",
            sections=[],
            heading_tree={},
            metadata={"format": fmt},
            tables=[],
            citations=[],
            total_words=0,
        )
