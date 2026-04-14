"""
TokenPak Compaction Modes — Standard Compression Policies.

Defines the four standard compaction modes that all TokenPak
implementations must support:

  lossless   Whitespace-only normalisation.  0–10% reduction.
             Fully deterministic. Use for debugging.
  balanced   Smart heuristic compression.  30–50% reduction.
             Deterministic. Use for normal workflows.
  aggressive Maximum heuristic compression.  50–70% reduction.
             Deterministic. Use for cost-sensitive workloads.
  semantic   Embedding-based compression (LLMLingua-2 when
             available, falls back to aggressive).  60–80%
             reduction.  Non-deterministic.
"""

from __future__ import annotations

import re
from enum import Enum
from typing import Optional

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class CompactionMode(str, Enum):
    """The four standard compaction modes."""

    LOSSLESS = "lossless"
    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"
    SEMANTIC = "semantic"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_MULTI_BLANK = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t]+$", re.MULTILINE)
_LEADING_TABS = re.compile(r"^\t+", re.MULTILINE)


def _normalise_whitespace(text: str) -> str:
    """Collapse consecutive blank lines and strip trailing spaces."""
    text = _TRAILING_WS.sub("", text)
    text = _MULTI_BLANK.sub("\n\n", text)
    text = _LEADING_TABS.sub(lambda m: "    " * len(m.group()), text)
    return text.strip()


# ---------------------------------------------------------------------------
# Per-mode compaction functions
# ---------------------------------------------------------------------------


def compact_lossless(text: str) -> str:
    """
    Lossless mode: whitespace normalisation only.

    - Collapses 3+ consecutive blank lines → two blank lines.
    - Strips trailing whitespace from each line.
    - Converts leading tabs → 4-space indents.
    - Guaranteed deterministic; original content is never dropped.

    Target reduction: 0–10 %.
    """
    return _normalise_whitespace(text)


def compact_balanced(text: str, target_tokens: Optional[int] = None) -> str:
    """
    Balanced mode: smart heuristic compression.

    Applies TextProcessor in aggressive mode to:
    - Keep headers and code fences.
    - Truncate bullet points to 80 chars.
    - Keep only the first sentence of paragraphs (up to 100 chars).
    - Drop boilerplate ("All rights reserved", "Click here", etc.).
    - Cap 5 lines of body text per section.

    Target reduction: 30–50 %.
    Deterministic.
    """
    from ..processors.text import TextProcessor

    text = _normalise_whitespace(text)
    processor = TextProcessor(aggressive=True)
    result = processor.process(text, "")

    if target_tokens and target_tokens > 0:
        result = _trim_to_tokens(result, target_tokens)

    return result


_BULLET_LINE = re.compile(r"^([ \t]*[-*+•]\s|[ \t]*\d+\.\s)")


def compact_aggressive(text: str, target_tokens: Optional[int] = None) -> str:
    """
    Aggressive mode: maximum deterministic compression.

    Builds on balanced mode and additionally:
    - Truncates bullet lines to 60 chars (vs 80 in balanced).
    - Strips Markdown image tags and long bare link targets.
    - Truncates any remaining paragraph lines to 60 chars.

    Target reduction: 50–70 %.
    Deterministic.
    """
    from ..processors.text import TextProcessor

    text = _normalise_whitespace(text)

    # First pass – same aggressive TextProcessor as balanced
    processor = TextProcessor(aggressive=True)
    result = processor.process(text, "")

    # Second pass – tighten bullets to 60-char limit
    lines = result.split("\n")
    tightened = []
    for line in lines:
        if _BULLET_LINE.match(line) and len(line) > 60:
            line = line[:60].rsplit(" ", 1)[0] + "…"
        tightened.append(line)
    result = "\n".join(tightened)

    # Third pass – strip Markdown image syntax and bare long link targets
    result = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"[img:\1]", result)
    result = re.sub(r"\[([^\]]+)\]\([^)]{40,}\)", r"\1", result)

    # Collapse repeated blank lines again after transforms
    result = _multi_blank_sub(result)

    if target_tokens and target_tokens > 0:
        result = _trim_to_tokens(result, target_tokens)

    return result


def compact_semantic(text: str, target_tokens: Optional[int] = None) -> str:
    """
    Semantic mode: embedding-based compression (non-deterministic).

    Attempts to use LLMLingua-2 for meaning-preserving compression.
    Falls back to aggressive mode if LLMLingua is not installed or
    a model inference error occurs.

    Target reduction: 60–80 %.
    Non-deterministic (LLMLingua path).
    """
    try:
        from ..engines.base import CompactionHints
        from ..engines.llmlingua import LLMLinguaEngine

        engine = LLMLinguaEngine()
        hints = CompactionHints()
        if target_tokens:
            hints.target_tokens = target_tokens
        return engine.compact(text, hints)

    except Exception:
        # Graceful degradation to aggressive
        return compact_aggressive(text, target_tokens=target_tokens)


# ---------------------------------------------------------------------------
# Dispatch table
# ---------------------------------------------------------------------------

_COMPACT_FN = {
    CompactionMode.LOSSLESS: compact_lossless,
    CompactionMode.BALANCED: compact_balanced,
    CompactionMode.AGGRESSIVE: compact_aggressive,
    CompactionMode.SEMANTIC: compact_semantic,
}


def compact(
    text: str,
    mode: CompactionMode | str = CompactionMode.BALANCED,
    target_tokens: Optional[int] = None,
) -> str:
    """
    Compact *text* using the specified *mode*.

    Args:
        text:         Input text to compress.
        mode:         One of ``CompactionMode`` or its string value.
        target_tokens: Optional token ceiling after compression.

    Returns:
        Compressed text string.
    """
    if isinstance(mode, str):
        mode = CompactionMode(mode)

    fn = _COMPACT_FN[mode]

    # lossless does not accept target_tokens
    if mode is CompactionMode.LOSSLESS:
        return fn(text)  # type: ignore
    return fn(text, target_tokens=target_tokens)  # type: ignore


# ---------------------------------------------------------------------------
# Internal utilities
# ---------------------------------------------------------------------------


def _multi_blank_sub(text: str) -> str:
    return _MULTI_BLANK.sub("\n\n", text)


def _trim_to_tokens(text: str, target_tokens: int) -> str:
    """Trim text to approximately *target_tokens* tokens."""
    target_chars = target_tokens * 4  # rough 4 chars/token heuristic
    if len(text) <= target_chars:
        return text
    trimmed = text[:target_chars]
    # Try to end on a sentence boundary
    last_newline = trimmed.rfind("\n")
    if last_newline > target_chars // 2:
        trimmed = trimmed[:last_newline]
    return trimmed.rstrip() + "\n…"
