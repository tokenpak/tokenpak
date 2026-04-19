"""
TokenPak — Intent-Specific Chunk Shapes + Granularity
======================================================

Different intents retrieve different chunk types at different granularity levels:

- debug   → contiguous code blocks at function granularity
- explain → prose sections with full context
- search  → compact fact chunks at paragraph granularity
- plan    → decision summaries with rationale
- create  → full template/example files
- summarize → section headers and summaries

Usage::

    from tokenpak.agent.vault.chunk_shapes import (
        CHUNK_SHAPES,
        reshape_chunks,
        get_shape_for_intent,
    )

    reshaped = reshape_chunks(bm25_results, intent="debug")
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Shape Registry
# ---------------------------------------------------------------------------

CHUNK_SHAPES: Dict[str, Dict[str, Any]] = {
    "debug": {
        "shape": "code_contiguous",
        "granularity": "function",
        "max_lines": 100,
        "description": "Contiguous code blocks at function boundaries, includes imports",
    },
    "explain": {
        "shape": "prose_section",
        "granularity": "section",
        "max_lines": 200,
        "description": "Full prose sections with surrounding context",
    },
    "search": {
        "shape": "fact_chunk",
        "granularity": "paragraph",
        "max_lines": 50,
        "description": "Compact fact-dense paragraphs, narrative stripped",
    },
    "plan": {
        "shape": "decision_summary",
        "granularity": "section",
        "max_lines": 150,
        "description": "Decisions and rationale, implementation details stripped",
    },
    "create": {
        "shape": "template_example",
        "granularity": "file",
        "max_lines": 300,
        "description": "Full examples with structure intact",
    },
    "summarize": {
        "shape": "section_header",
        "granularity": "heading",
        "max_lines": 100,
        "description": "Section headings and topic sentences only",
    },
}

# Fallback shape when intent is unknown
_DEFAULT_SHAPE = {
    "shape": "prose_section",
    "granularity": "section",
    "max_lines": 150,
}

# ---------------------------------------------------------------------------
# Regex helpers
# ---------------------------------------------------------------------------

# Python / JS / TS / Go / Rust function/class boundary detection
_FUNC_BOUNDARY_RE = re.compile(
    r"^(?:(?:async\s+)?def\s+\w+|class\s+\w+|func(?:tion)?\s+\w+|"
    r"(?:pub\s+)?fn\s+\w+|(?:export\s+)?(?:default\s+)?(?:async\s+)?function)",
    re.MULTILINE,
)

# Import lines (Python / JS / TS / Go)
_IMPORT_RE = re.compile(
    r"^(?:import\s+|from\s+\S+\s+import|require\(|use\s+\S+;)",
    re.MULTILINE,
)

# Markdown / RST headings
_HEADING_RE = re.compile(r"^#{1,6}\s+.+|^.+\n[=\-]{3,}$", re.MULTILINE)

# Prose paragraph break (two+ blank lines or heading)
_PARAGRAPH_BREAK_RE = re.compile(r"\n{2,}")

# Decision / rationale markers
_DECISION_RE = re.compile(
    r"(?:^|\n)(?:#+\s+)?(?:Decision|Rationale|Why|Chosen|Rejected|"
    r"Alternative|Trade-?off|Pros?|Cons?|Resolution)(?:[:\s]|$)",
    re.IGNORECASE | re.MULTILINE,
)

# Narrative filler (strips "In this section we will...")
_NARRATIVE_RE = re.compile(
    r"\b(?:in this (?:section|chapter|document)|we will|let(?:'s| us)|"
    r"as you can see|note that|it is worth noting|please note)\b[^.]*\.",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Shape implementations
# ---------------------------------------------------------------------------


def _shape_code_contiguous(content: str, max_lines: int) -> str:
    """Extract a contiguous code block up to *max_lines*.

    Strategy:
    1. Collect any leading import lines.
    2. Find the first function/class definition.
    3. Include everything from that definition to *max_lines*.
    """
    lines = content.splitlines()

    import_lines: List[str] = []
    body_start = 0

    for i, line in enumerate(lines):
        if _IMPORT_RE.match(line):
            import_lines.append(line)
            body_start = i + 1
        elif import_lines and not line.strip():
            # Allow one blank line between imports and body
            body_start = i + 1
        else:
            if import_lines:
                break

    # Find first function/class boundary in the body
    body_lines = lines[body_start:]
    func_start = 0
    for i, line in enumerate(body_lines):
        if _FUNC_BOUNDARY_RE.match(line):
            func_start = i
            break

    selected = body_lines[func_start : func_start + max_lines]

    result_parts: List[str] = []
    if import_lines:
        result_parts.append("\n".join(import_lines))
    result_parts.append("\n".join(selected))

    return "\n\n".join(result_parts)


def _shape_fact_chunk(content: str, max_lines: int) -> str:
    """Extract fact-dense content, stripping narrative filler.

    Strategy:
    1. Remove obvious narrative phrases.
    2. Split into paragraphs, pick the densest ones (fewest filler words).
    3. Return up to *max_lines*.
    """
    # Strip narrative filler
    cleaned = _NARRATIVE_RE.sub("", content)

    # Split into paragraphs
    paragraphs = [p.strip() for p in _PARAGRAPH_BREAK_RE.split(cleaned) if p.strip()]

    # Score by information density (prefer shorter, concrete paragraphs)
    def _density(para: str) -> float:
        words = para.split()
        if not words:
            return 0.0
        # Prefer paragraphs with identifiers, numbers, punctuation
        signal = sum(
            1 for w in words
            if re.search(r"[A-Z_][A-Z_]{2,}|[a-z_]{2,}[A-Z]|\d+", w)
        )
        return signal / len(words)

    ranked = sorted(paragraphs, key=_density, reverse=True)

    result_lines: List[str] = []
    for para in ranked:
        para_lines = para.splitlines()
        if len(result_lines) + len(para_lines) > max_lines:
            remaining = max_lines - len(result_lines)
            result_lines.extend(para_lines[:remaining])
            break
        result_lines.extend(para_lines)
        result_lines.append("")  # blank line between paragraphs

    return "\n".join(result_lines).strip()


def _shape_decision_summary(content: str, max_lines: int) -> str:
    """Extract decisions and rationale, strip implementation details.

    Strategy:
    1. Scan for decision/rationale markers (headings that match).
    2. Collect those heading sections (heading + body until next heading).
    3. Fall back to full content if no markers found.
    """
    lines = content.splitlines()
    result_lines: List[str] = []

    # First pass: identify all heading sections and mark which are decision-relevant
    sections: List[Tuple[int, int, bool]] = []  # (start, end, is_decision)
    heading_starts: List[int] = []

    for i, line in enumerate(lines):
        if _HEADING_RE.match(line):
            heading_starts.append(i)

    for idx, start in enumerate(heading_starts):
        end = heading_starts[idx + 1] if idx + 1 < len(heading_starts) else len(lines)
        is_decision = bool(_DECISION_RE.search(lines[start]))
        sections.append((start, end, is_decision))

    for start, end, is_decision in sections:
        if is_decision:
            result_lines.extend(lines[start:end])
            result_lines.append("")

    if not result_lines:
        # No decision markers found — return first max_lines
        return "\n".join(lines[:max_lines])

    return "\n".join(result_lines[:max_lines]).strip()


def _shape_section_header(content: str, max_lines: int) -> str:
    """Extract section headings and topic sentences.

    Strategy:
    1. Find all headings.
    2. For each heading, include the first non-blank sentence following it.
    3. Cap at *max_lines*.
    """
    lines = content.splitlines()
    result_lines: List[str] = []
    i = 0

    while i < len(lines) and len(result_lines) < max_lines:
        line = lines[i]
        if _HEADING_RE.match(line):
            result_lines.append(line)
            # Find next non-blank line as topic sentence
            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1
            if j < len(lines):
                # Take first sentence only
                topic = lines[j]
                sentence_end = topic.find(". ")
                if sentence_end > 0:
                    topic = topic[: sentence_end + 1]
                result_lines.append(topic)
                result_lines.append("")
            i = j + 1
        else:
            i += 1

    if not result_lines:
        # No headings — return first few lines
        return "\n".join(lines[:max_lines])

    return "\n".join(result_lines).strip()


def _shape_template_example(content: str, max_lines: int) -> str:
    """Return the full content up to *max_lines*, preserving structure."""
    lines = content.splitlines()
    return "\n".join(lines[:max_lines])


def _shape_prose_section(content: str, max_lines: int) -> str:
    """Return the content as-is up to *max_lines* (default prose behavior)."""
    lines = content.splitlines()
    return "\n".join(lines[:max_lines])


# ---------------------------------------------------------------------------
# Shape dispatch table
# ---------------------------------------------------------------------------

_SHAPE_FN = {
    "code_contiguous": _shape_code_contiguous,
    "fact_chunk": _shape_fact_chunk,
    "decision_summary": _shape_decision_summary,
    "section_header": _shape_section_header,
    "template_example": _shape_template_example,
    "prose_section": _shape_prose_section,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_shape_for_intent(intent: str) -> Dict[str, Any]:
    """Return the shape config for *intent*, falling back to prose_section.

    Args:
        intent: One of the registered intent keys (debug, explain, search,
                plan, create, summarize) or an unknown string.

    Returns:
        Shape config dict with keys: shape, granularity, max_lines.
    """
    return CHUNK_SHAPES.get(intent.lower(), _DEFAULT_SHAPE)


def apply_shape(content: str, shape_config: Dict[str, Any]) -> str:
    """Apply a shape transformation to *content*.

    Args:
        content: Raw chunk content.
        shape_config: Shape config dict (from :data:`CHUNK_SHAPES` or
                      :func:`get_shape_for_intent`).

    Returns:
        Reshaped content string. May be shorter than the original.
    """
    shape = shape_config.get("shape", "prose_section")
    max_lines = shape_config.get("max_lines", 150)
    fn = _SHAPE_FN.get(shape, _shape_prose_section)
    return fn(content, max_lines)


def reshape_chunks(
    results: List[Tuple[Dict[str, Any], float]],
    intent: str,
) -> List[Tuple[Dict[str, Any], float]]:
    """Reshape retrieval results according to the intent's chunk shape.

    Called after BM25 (and optional multi-signal) scoring to slice/reshape
    each chunk for the target intent.

    Args:
        results: List of (block_dict, score) tuples from the retrieval pipeline.
        intent: Intent string — one of: debug, explain, search, plan, create,
                summarize. Unknown intents fall back to prose_section.

    Returns:
        New list of (reshaped_block_dict, score) tuples. Each block_dict gets
        a ``reshaped_content`` key with the shaped content, plus a
        ``shape_applied`` key recording the shape name used. The original
        ``content`` key is preserved.
    """
    shape_config = get_shape_for_intent(intent)
    reshaped: List[Tuple[Dict[str, Any], float]] = []

    for block, score in results:
        content = block.get("content", "")
        shaped = apply_shape(content, shape_config)

        new_block = dict(block)
        new_block["reshaped_content"] = shaped
        new_block["shape_applied"] = shape_config["shape"]
        new_block["intent"] = intent

        reshaped.append((new_block, score))

    return reshaped
