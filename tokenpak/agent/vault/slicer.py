"""TokenPak — Script-Aware Semantic Slicer for Long Content Assets.

Splits structured markdown/content documents into deterministic semantic
sub-blocks. Designed for multi-script batches, research docs, and other
long-form assets where a single monolithic block inflates token usage and
hurts retrieval precision.

Supported split strategies
--------------------------
- ``heading``: Split on markdown headings at a given level (default ``##``).
- ``script``:  Alias for heading-split tuned to "## Script N:" patterns.
- ``section``: Generic paragraph-block splitting (double blank line).

Sub-block IDs
-------------
Given a parent block ID of ``/vault/scripts.md#a1b2c3d4`` and a slice headed
"## Script 1: Intro", the child ID is::

    /vault/scripts.md#a1b2c3d4:script1

The suffix is derived **deterministically** from the slice's own content hash
prefix (8 chars), so IDs are stable across re-index runs when content is
unchanged.

Provenance
----------
Every :class:`SliceRecord` carries:
- ``parent_block_id`` — the block ID of the parent file.
- ``parent_path``     — the source file path.
- ``slice_index``     — 0-based position in the slice list.
- ``heading``         — the heading text that opened the slice (if any).
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Public constants
# ---------------------------------------------------------------------------

# Regex patterns that trigger script-aware splitting
_SCRIPT_HEADING_RE = re.compile(
    r"^(#{1,4})\s+(Script\s+\d+[:.\-].*|Scene\s+\d+[:.\-].*|Episode\s+\d+[:.\-].*|Part\s+\d+[:.\-].*|Chapter\s+\d+[:.\-].*)",
    re.IGNORECASE | re.MULTILINE,
)

# Generic heading patterns by level
_HEADING_RE = {
    1: re.compile(r"^# .+", re.MULTILINE),
    2: re.compile(r"^## .+", re.MULTILINE),
    3: re.compile(r"^### .+", re.MULTILINE),
    4: re.compile(r"^#### .+", re.MULTILINE),
}

# Minimum chars for a slice to be worth emitting (avoids empty/header-only slices)
MIN_SLICE_CHARS = 40


# ---------------------------------------------------------------------------
# SliceRecord
# ---------------------------------------------------------------------------

@dataclass
class SliceRecord:
    """A single semantic sub-block sliced from a parent document.

    Attributes:
        slice_id:         Stable unique identifier for this slice.
        parent_block_id:  Block ID of the parent file record.
        parent_path:      Source file path.
        slice_index:      0-based position in the slice list (stable ordering).
        heading:          The heading text that started this slice (empty string
                          for preamble / headingless content).
        content:          Raw text content of this slice.
        content_hash:     SHA-256 of content (hex, full).
        strategy:         Split strategy used (``heading`` | ``script`` | ``section``).
        metadata:         Arbitrary extra data (e.g. heading level, line number).
    """

    slice_id: str
    parent_block_id: str
    parent_path: str
    slice_index: int
    heading: str
    content: str
    content_hash: str
    strategy: str
    metadata: dict = field(default_factory=dict)

    @property
    def tokens_hint(self) -> int:
        """Rough token estimate (4 chars ≈ 1 token)."""
        return max(1, len(self.content) // 4)


# ---------------------------------------------------------------------------
# Split logic helpers
# ---------------------------------------------------------------------------

def _sha256_prefix(text: str, length: int = 8) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def _make_slice_id(parent_block_id: str, content: str, index: int) -> str:
    """Return a deterministic slice ID stable across re-index runs."""
    content_prefix = _sha256_prefix(content)
    return f"{parent_block_id}:s{index:03d}_{content_prefix}"


def _is_long_content(content: str, threshold_chars: int = 800) -> bool:
    """Return True if content is long enough to merit slicing."""
    return len(content) >= threshold_chars


def _split_by_heading_pattern(
    content: str,
    heading_re: re.Pattern,
) -> list[tuple[str, str]]:
    """Split *content* at every match of *heading_re*.

    Returns a list of (heading, body) pairs.  The first pair may have an
    empty heading (preamble before first heading).
    """
    matches = list(heading_re.finditer(content))
    if not matches:
        return [("", content)]

    slices: list[tuple[str, str]] = []

    # Preamble (before first heading)
    preamble = content[: matches[0].start()].strip()
    if preamble:
        slices.append(("", preamble))

    for i, m in enumerate(matches):
        heading_text = m.group(0).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        body = content[body_start:body_end].strip()
        full_slice = f"{heading_text}\n\n{body}".strip() if body else heading_text
        slices.append((heading_text, full_slice))

    return slices


def _split_by_double_newline(content: str) -> list[tuple[str, str]]:
    """Split on double blank lines; no heading extraction."""
    parts = re.split(r"\n{3,}", content)
    return [("", p.strip()) for p in parts if p.strip()]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def should_slice(content: str, path: str) -> bool:
    """Return True if *content* should be split into sub-blocks.

    Heuristics:
    - File type must be markdown/text (``path`` ends with .md / .txt / .rst).
    - Content must be long (> 800 chars).
    - Content must contain at least 2 heading-level splits OR match the
      script-heading pattern.
    """
    text_exts = {".md", ".txt", ".rst", ".adoc", ".org"}
    suffix = "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if suffix not in text_exts:
        return False
    if not _is_long_content(content):
        return False
    # Must have at least 2 heading-split boundaries
    h2_matches = list(_HEADING_RE[2].finditer(content))
    script_matches = list(_SCRIPT_HEADING_RE.finditer(content))
    return len(h2_matches) >= 2 or len(script_matches) >= 1


def detect_split_strategy(content: str) -> str:
    """Auto-detect the best split strategy for *content*.

    Returns:
        ``"script"`` if script/scene/episode headings are present,
        ``"heading"`` if generic ## headings are present,
        ``"section"`` otherwise (double-newline fallback).
    """
    if _SCRIPT_HEADING_RE.search(content):
        return "script"
    for level in (2, 3, 1):
        if len(_HEADING_RE[level].findall(content)) >= 2:
            return "heading"
    return "section"


def slice_content(
    content: str,
    parent_block_id: str,
    parent_path: str,
    strategy: Optional[str] = None,
    min_chars: int = MIN_SLICE_CHARS,
) -> List[SliceRecord]:
    """Slice *content* into deterministic semantic sub-blocks.

    Args:
        content:          Full text of the parent document.
        parent_block_id:  Block ID from the parent :class:`BlockRecord`.
        parent_path:      Source file path.
        strategy:         One of ``"script"``, ``"heading"``, ``"section"``,
                          or ``None`` for auto-detection.
        min_chars:        Skip slices shorter than this (avoids stub slices).

    Returns:
        List of :class:`SliceRecord` objects in document order.
        Returns empty list if content should not be sliced.
    """
    if strategy is None:
        strategy = detect_split_strategy(content)

    if strategy in ("script", "heading"):
        # Choose heading pattern
        if strategy == "script":
            heading_re = _SCRIPT_HEADING_RE
        else:
            # Use the most-populated heading level
            best_level = 2
            best_count = 0
            for lvl in (1, 2, 3, 4):
                cnt = len(_HEADING_RE[lvl].findall(content))
                if cnt > best_count:
                    best_count = cnt
                    best_level = lvl
            heading_re = _HEADING_RE[best_level]

        raw_slices = _split_by_heading_pattern(content, heading_re)
    else:
        raw_slices = _split_by_double_newline(content)

    records: List[SliceRecord] = []
    for idx, (heading, body) in enumerate(raw_slices):
        if len(body) < min_chars:
            continue  # Skip stubs
        content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        slice_id = _make_slice_id(parent_block_id, body, idx)
        records.append(
            SliceRecord(
                slice_id=slice_id,
                parent_block_id=parent_block_id,
                parent_path=parent_path,
                slice_index=idx,
                heading=heading,
                content=body,
                content_hash=content_hash,
                strategy=strategy,
                metadata={
                    "heading_level": len(heading.split()[0]) if heading.startswith("#") else 0,
                    "char_count": len(body),
                },
            )
        )
    return records
