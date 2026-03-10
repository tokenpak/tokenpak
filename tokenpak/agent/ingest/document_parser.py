"""TokenPak Agent Ingest Document Parser — structural document extraction.

Parses prose documents (markdown, HTML, plain text) into navigable structural
representations: headings, sections, tables, footnotes, citations, lists, code blocks.

Usage::

    parser = DocumentParser()
    structure = parser.parse(markdown_content, format="markdown")
    for section in structure.sections:
        print(section.type, section.heading, section.content)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional


class SectionType(str, Enum):
    """Section classification types."""
    OVERVIEW = "overview"
    METHODOLOGY = "methodology"
    RESULTS = "results"
    RECOMMENDATIONS = "recommendations"
    LEGAL = "legal"
    APPENDIX = "appendix"
    DEFINITIONS = "definitions"
    INTRODUCTION = "introduction"
    CONCLUSION = "conclusion"
    BACKGROUND = "background"
    UNKNOWN = "unknown"


@dataclass
class HeadingNode:
    """A heading with its hierarchical level and position."""
    text: str
    level: int  # 1-6 for markdown
    line_number: int
    children: list[HeadingNode] = field(default_factory=list)

    def depth_path(self) -> str:
        """Return dot-separated path (e.g., '1.2.3')."""
        return str(self.level)


@dataclass
class Table:
    """Extracted table structure."""
    headers: list[str]
    rows: list[list[str]]
    line_start: int
    line_end: int

    def __str__(self) -> str:
        """Return markdown representation."""
        if not self.headers:
            return ""
        md = "| " + " | ".join(self.headers) + " |\n"
        md += "|" + "|".join(["-" * (len(h) + 1) for h in self.headers]) + "|\n"
        for row in self.rows:
            md += "| " + " | ".join(row) + " |\n"
        return md


@dataclass
class DocumentSection:
    """A parsed section of a document."""
    heading: str  # Section title
    type: SectionType  # Classified section type
    level: int  # Heading depth (1=H1, 2=H2, etc.)
    line_start: int
    line_end: int
    content: str  # Raw text content
    subsections: list[DocumentSection] = field(default_factory=list)
    tables: list[Table] = field(default_factory=list)
    lists: list[list[str]] = field(default_factory=list)
    code_blocks: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    footnotes: list[str] = field(default_factory=list)


@dataclass
class DocumentStructure:
    """Top-level parsed document."""
    title: Optional[str]
    sections: list[DocumentSection]
    headings_tree: Optional[HeadingNode] = None
    metadata: dict = field(default_factory=dict)

    def find_section(self, heading: str) -> Optional[DocumentSection]:
        """Recursively find section by heading."""
        def search(secs: list[DocumentSection]) -> Optional[DocumentSection]:
            for sec in secs:
                if sec.heading.lower() == heading.lower():
                    return sec
                result = search(sec.subsections)
                if result:
                    return result
            return None
        return search(self.sections)


class DocumentParser:
    """Parser for markdown, HTML, and plain text documents."""

    def parse(self, content: str, format: str = "markdown") -> DocumentStructure:
        """Parse document content and return structured representation."""
        if format == "markdown":
            return self._parse_markdown(content)
        elif format == "html":
            return self._parse_html(content)
        else:
            return self._parse_plaintext(content)

    def parse_file(self, path: str) -> DocumentStructure:
        """Parse a file, auto-detecting format by extension."""
        p = Path(path)
        content = p.read_text(encoding="utf-8", errors="replace")
        
        if p.suffix.lower() in (".md", ".markdown"):
            fmt = "markdown"
        elif p.suffix.lower() in (".html", ".htm"):
            fmt = "html"
        else:
            fmt = "plaintext"
        
        return self.parse(content, format=fmt)

    # ------------------------------------------------------------------
    # Markdown
    # ------------------------------------------------------------------

    def _parse_markdown(self, content: str) -> DocumentStructure:
        """Parse markdown document."""
        lines = content.splitlines()
        
        # Extract title (first H1)
        title = None
        for line in lines:
            match = re.match(r"^#\s+(.+)$", line)
            if match:
                title = match.group(1).strip()
                break
        
        # Build heading tree
        headings = self._extract_markdown_headings(lines)
        headings_tree = self._build_heading_tree(headings)
        
        # Parse sections
        sections = self._parse_sections_markdown(lines)
        
        return DocumentStructure(
            title=title,
            sections=sections,
            headings_tree=headings_tree,
        )

    def _extract_markdown_headings(self, lines: list[str]) -> list[tuple[int, str, int]]:
        """Extract all headings: (level, text, line_number)."""
        headings: list[tuple[int, str, int]] = []
        for i, line in enumerate(lines):
            match = re.match(r"^(#{1,6})\s+(.+)$", line)
            if match:
                level = len(match.group(1))
                text = match.group(2).strip()
                headings.append((level, text, i))
        return headings

    def _build_heading_tree(self, headings: list[tuple[int, str, int]]) -> Optional[HeadingNode]:
        """Build hierarchical tree from flat heading list."""
        if not headings:
            return None
        
        root = HeadingNode(text="[root]", level=0, line_number=-1)
        stack = [root]
        
        for level, text, line_num in headings:
            node = HeadingNode(text=text, level=level, line_number=line_num)
            
            # Pop stack to find parent at level-1
            while len(stack) > 1 and stack[-1].level >= level:
                stack.pop()
            
            stack[-1].children.append(node)
            stack.append(node)
        
        return root.children[0] if root.children else None

    def _parse_sections_markdown(self, lines: list[str]) -> list[DocumentSection]:
        """Parse markdown into sections."""
        sections: list[DocumentSection] = []
        headings = self._extract_markdown_headings(lines)
        
        if not headings:
            # No headings: treat entire doc as one section
            content = "\n".join(lines)
            sections.append(
                DocumentSection(
                    heading="[document]",
                    type=SectionType.UNKNOWN,
                    level=0,
                    line_start=0,
                    line_end=len(lines),
                    content=content,
                    **self._extract_markdown_details(lines),
                )
            )
            return sections
        
        # Split content by heading levels
        for i, (level, heading, line_num) in enumerate(headings):
            start = line_num
            
            # Find end (next heading of same/higher level, or EOF)
            end = len(lines)
            for j in range(i + 1, len(headings)):
                next_level, _, next_line = headings[j]
                if next_level <= level:
                    end = next_line
                    break
            
            section_lines = lines[start:end]
            content = "\n".join(section_lines[1:])  # Skip heading line
            
            section = DocumentSection(
                heading=heading,
                type=self._classify_section(heading, content),
                level=level,
                line_start=start,
                line_end=end,
                content=content,
                **self._extract_markdown_details(section_lines),
            )
            sections.append(section)
        
        return sections

    def _extract_markdown_details(self, lines: list[str]) -> dict:
        """Extract tables, lists, code blocks, citations, footnotes."""
        content = "\n".join(lines)
        
        tables = self._extract_markdown_tables(lines)
        lists = self._extract_markdown_lists(lines)
        code_blocks = self._extract_markdown_code_blocks(lines)
        citations = self._extract_citations(content)
        footnotes = self._extract_footnotes(content)
        
        return {
            "tables": tables,
            "lists": lists,
            "code_blocks": code_blocks,
            "citations": citations,
            "footnotes": footnotes,
        }

    def _extract_markdown_tables(self, lines: list[str]) -> list[Table]:
        """Extract markdown tables."""
        tables: list[Table] = []
        i = 0
        while i < len(lines):
            # Look for table header (pipes with dashes below)
            if "|" in lines[i]:
                headers_line = lines[i]
                headers = [h.strip() for h in headers_line.split("|") if h.strip()]
                
                # Check for separator line
                if i + 1 < len(lines) and all(c in "-| " for c in lines[i + 1]):
                    table_start = i
                    i += 2
                    rows = []
                    
                    while i < len(lines) and "|" in lines[i]:
                        row = [c.strip() for c in lines[i].split("|") if c.strip()]
                        if len(row) == len(headers):
                            rows.append(row)
                        i += 1
                    
                    tables.append(
                        Table(
                            headers=headers,
                            rows=rows,
                            line_start=table_start,
                            line_end=i,
                        )
                    )
                    continue
            i += 1
        
        return tables

    def _extract_markdown_lists(self, lines: list[str]) -> list[list[str]]:
        """Extract bullet/numbered lists."""
        lists: list[list[str]] = []
        current_list: list[str] = []
        
        for line in lines:
            stripped = line.lstrip()
            if re.match(r"^[-*+]\s+", stripped) or re.match(r"^\d+\.\s+", stripped):
                item = re.sub(r"^[-*+]\s+|^\d+\.\s+", "", stripped)
                current_list.append(item)
            else:
                if current_list:
                    lists.append(current_list)
                    current_list = []
        
        if current_list:
            lists.append(current_list)
        
        return lists

    def _extract_markdown_code_blocks(self, lines: list[str]) -> list[str]:
        """Extract code blocks (triple backtick)."""
        blocks: list[str] = []
        in_block = False
        current_block: list[str] = []
        
        for line in lines:
            if line.strip().startswith("```"):
                if in_block:
                    blocks.append("\n".join(current_block))
                    current_block = []
                    in_block = False
                else:
                    in_block = True
            elif in_block:
                current_block.append(line)
        
        return blocks

    # ------------------------------------------------------------------
    # HTML
    # ------------------------------------------------------------------

    def _parse_html(self, content: str) -> DocumentStructure:
        """Parse HTML document (basic regex-based extraction)."""
        # Simple regex-based heading extraction
        title = None
        title_match = re.search(r"<title>(.+?)</title>", content, re.IGNORECASE)
        if title_match:
            title = title_match.group(1).strip()
        
        h1_match = re.search(r"<h1[^>]*>(.+?)</h1>", content, re.IGNORECASE)
        if h1_match and not title:
            title = h1_match.group(1).strip()
        
        # Extract headings
        headings: list[tuple[int, str, int]] = []
        for match in re.finditer(r"<h([1-6])[^>]*>(.+?)</h\1>", content, re.IGNORECASE):
            level = int(match.group(1))
            text = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            headings.append((level, text, match.start()))
        
        headings_tree = self._build_heading_tree(headings)
        
        # Extract sections
        lines = content.splitlines()
        sections: list[DocumentSection] = []
        
        # Remove HTML tags for text extraction
        text_content = re.sub(r"<[^>]+>", "", content)
        section_lines = text_content.splitlines()
        
        if headings:
            for level, heading, _ in headings:
                section = DocumentSection(
                    heading=heading,
                    type=self._classify_section(heading, ""),
                    level=level,
                    line_start=0,
                    line_end=len(section_lines),
                    content="",
                    tables=self._extract_html_tables(content),
                )
                sections.append(section)
        else:
            sections.append(
                DocumentSection(
                    heading="[document]",
                    type=SectionType.UNKNOWN,
                    level=0,
                    line_start=0,
                    line_end=len(section_lines),
                    content=text_content,
                    tables=self._extract_html_tables(content),
                )
            )
        
        return DocumentStructure(
            title=title,
            sections=sections,
            headings_tree=headings_tree,
        )

    def _extract_html_tables(self, content: str) -> list[Table]:
        """Extract tables from HTML."""
        tables: list[Table] = []
        
        for table_match in re.finditer(r"<table[^>]*>(.+?)</table>", content, re.IGNORECASE | re.DOTALL):
            table_html = table_match.group(1)
            
            # Extract headers
            headers = []
            for th in re.finditer(r"<th[^>]*>(.+?)</th>", table_html, re.IGNORECASE | re.DOTALL):
                text = re.sub(r"<[^>]+>", "", th.group(1)).strip()
                headers.append(text)
            
            # Extract rows
            rows: list[list[str]] = []
            for tr in re.finditer(r"<tr[^>]*>(.+?)</tr>", table_html, re.IGNORECASE | re.DOTALL):
                row_html = tr.group(1)
                row: list[str] = []
                for td in re.finditer(r"<td[^>]*>(.+?)</td>", row_html, re.IGNORECASE | re.DOTALL):
                    text = re.sub(r"<[^>]+>", "", td.group(1)).strip()
                    row.append(text)
                if row:
                    rows.append(row)
            
            if headers or rows:
                tables.append(
                    Table(
                        headers=headers,
                        rows=rows,
                        line_start=0,
                        line_end=0,
                    )
                )
        
        return tables

    # ------------------------------------------------------------------
    # Plain text
    # ------------------------------------------------------------------

    def _parse_plaintext(self, content: str) -> DocumentStructure:
        """Parse plain text with heuristic heading detection."""
        lines = content.splitlines()
        
        # Heuristic: lines in ALL CAPS followed by dashes = headings
        headings: list[tuple[int, str, int]] = []
        title = None
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # Check for ALL CAPS as heading
            if stripped and stripped.isupper() and len(stripped) > 3:
                # Check if followed by dashes or is short
                if i + 1 < len(lines):
                    next_line = lines[i + 1].strip()
                    if all(c in "-=" for c in next_line) and len(next_line) > 2:
                        if not title:
                            title = stripped
                        else:
                            level = 2  # Assume second-level
                            headings.append((level, stripped, i))
            
            # Check for leading dashes/equals as heading underline
            if stripped and all(c in "-=" for c in stripped) and len(stripped) > 2:
                if i > 0:
                    prev_line = lines[i - 1].strip()
                    if prev_line and prev_line.isupper():
                        level = 1 if "=" in stripped else 2
                        if prev_line not in [h[1] for h in headings]:
                            headings.append((level, prev_line, i - 1))
        
        headings_tree = self._build_heading_tree(headings) if headings else None
        
        sections: list[DocumentSection] = []
        if headings:
            for level, heading, line_num in headings:
                section = DocumentSection(
                    heading=heading,
                    type=self._classify_section(heading, ""),
                    level=level,
                    line_start=line_num,
                    line_end=line_num + 2,
                    content="",
                )
                sections.append(section)
        else:
            sections.append(
                DocumentSection(
                    heading="[document]",
                    type=SectionType.UNKNOWN,
                    level=0,
                    line_start=0,
                    line_end=len(lines),
                    content=content,
                )
            )
        
        return DocumentStructure(
            title=title,
            sections=sections,
            headings_tree=headings_tree,
        )

    # ------------------------------------------------------------------
    # Section classification
    # ------------------------------------------------------------------

    def _classify_section(self, heading: str, content: str) -> SectionType:
        """Classify section type by heading and content."""
        h_lower = heading.lower()
        c_lower = content.lower()
        
        # Define patterns for each type
        patterns = {
            SectionType.OVERVIEW: r"\b(overview|summary|abstract|executive\s+summary)\b",
            SectionType.INTRODUCTION: r"\b(introduction|background|context)\b",
            SectionType.METHODOLOGY: r"\b(method|approach|methodology|process)\b",
            SectionType.RESULTS: r"(results?|findings?|outcomes?|outputs?)",
            SectionType.RECOMMENDATIONS: r"(recommendations?|suggestions?|proposals?|next\s+steps?)",
            SectionType.CONCLUSION: r"\b(conclusion|summary|wrap.?up)\b",
            SectionType.LEGAL: r"\b(legal|license|copyright|disclaimer|terms)\b",
            SectionType.APPENDIX: r"\b(appendix|annex|supplementary)\b",
            SectionType.DEFINITIONS: r"\b(definition|glossary|terms?\s+of)\b",
        }
        
        for sec_type, pattern in patterns.items():
            if re.search(pattern, h_lower) or re.search(pattern, c_lower[:500]):
                return sec_type
        
        return SectionType.UNKNOWN

    def _extract_citations(self, content: str) -> list[str]:
        """Extract citations (simple: [Author, Year] or [ref])."""
        matches = re.findall(r"\[([A-Za-z\s]+,\s*\d{4})\]|\[([A-Za-z0-9_-]+)\]", content)
        return [m[0] or m[1] for m in matches]

    def _extract_footnotes(self, content: str) -> list[str]:
        """Extract footnotes (simple: [^1]: text)."""
        matches = re.findall(r"\[\^(\d+)\]:\s*(.+)", content)
        return [f"[^{m[0]}]: {m[1]}" for m in matches]
