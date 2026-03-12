"""
salience.doc_extractor — Extract high-signal content from documentation.

Strategy
--------
1. Keep all headings (Markdown ``#`` / RST underline style).
2. Keep TODO / FIXME / NOTE / HACK / XXX annotated lines + 2 lines context.
3. Keep decision items: lines starting with bullet + keywords
   (decided, agreed, approved, rejected, chosen, action, owner, deadline).
4. Return a compact outline showing only what matters for review.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import List, Set, Tuple


# ── constants ─────────────────────────────────────────────────────────────

ANNOTATION_CONTEXT_LINES: int = 2   # lines after each TODO/FIXME/etc.

# ── patterns ──────────────────────────────────────────────────────────────

_HEADING_RE = re.compile(r"^#{1,6}\s+\S", re.MULTILINE)

# RST-style headings: a non-whitespace line followed by a line of ===, ---, ~~~, ^^^
_RST_HEADING_RE = re.compile(r"^(\S[^\n]*)\n([=\-~^]{3,})\s*$", re.MULTILINE)

_ANNOTATION_RE = re.compile(
    r"\b(?P<tag>TODO|FIXME|NOTE|HACK|XXX|BUG|WARN(?:ING)?)\b",
    re.IGNORECASE,
)

_DECISION_RE = re.compile(
    r"""
    ^\s*[-*+]\s+.*\b
    (?:decided|agreed|approved|rejected|chosen|action\s+item|action:|
       owner:|deadline:|due:|assigned\s+to|resolved|conclusion|
       recommendation|will\s+use|won['']t\s+use|going\s+forward)
    \b
    """,
    re.VERBOSE | re.IGNORECASE | re.MULTILINE,
)


@dataclass
class DocExtractionResult:
    lines_in: int = 0
    lines_out: int = 0
    headings: List[str] = field(default_factory=list)
    annotation_count: int = 0
    decision_count: int = 0
    extracted: str = ""

    @property
    def reduction_pct(self) -> float:
        if self.lines_in == 0:
            return 0.0
        return round((1 - self.lines_out / self.lines_in) * 100, 1)


class DocExtractor:
    """
    Extract high-signal content from documentation / markdown text.

    Parameters
    ----------
    annotation_context : int
        Lines of context to keep after each TODO/FIXME/NOTE/etc.
    include_rst_headings : bool
        Also detect RST-style headings (underline-based).
    """

    def __init__(
        self,
        annotation_context: int = ANNOTATION_CONTEXT_LINES,
        include_rst_headings: bool = True,
    ) -> None:
        self.annotation_context = annotation_context
        self.include_rst_headings = include_rst_headings

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract(self, text: str) -> DocExtractionResult:
        """Return a :class:`DocExtractionResult` for *text*."""
        lines = text.splitlines()
        result = DocExtractionResult(lines_in=len(lines))

        keep: Set[int] = set()

        # 1. Headings
        heading_indices: List[int] = []
        for idx, line in enumerate(lines):
            if _HEADING_RE.match(line):
                keep.add(idx)
                heading_indices.append(idx)
                result.headings.append(line.strip())

        # RST headings: line N is heading text, line N+1 is underline
        if self.include_rst_headings:
            for m in _RST_HEADING_RE.finditer(text):
                char_pos = m.start()
                idx = text[:char_pos].count("\n")
                keep.add(idx)
                keep.add(idx + 1)
                result.headings.append(m.group(1).strip())

        # 2. Annotations (TODO / FIXME / NOTE / etc.)
        for idx, line in enumerate(lines):
            if _ANNOTATION_RE.search(line):
                result.annotation_count += 1
                keep.add(idx)
                for offset in range(1, self.annotation_context + 1):
                    target = idx + offset
                    if target < len(lines):
                        keep.add(target)

        # 3. Decision items
        for idx, line in enumerate(lines):
            if _DECISION_RE.match(line):
                result.decision_count += 1
                keep.add(idx)

        # Build output
        sorted_keep = sorted(keep)
        output_lines: List[str] = [
            f"[doc-salience] {result.lines_in} lines  "
            f"headings={len(result.headings)}  "
            f"annotations={result.annotation_count}  "
            f"decisions={result.decision_count}",
            "",
        ]

        prev_idx = -2
        for idx in sorted_keep:
            if idx - prev_idx > 1:
                output_lines.append("…")
            output_lines.append(lines[idx])
            prev_idx = idx

        result.extracted = "\n".join(output_lines)
        result.lines_out = len(output_lines)
        return result
