"""Progressive disclosure middleware for vault injection.

Intercepts vault document content before injection to reduce token usage.
Serves a section map + per-section summaries by default; falls back to full
content only when the request intent signals precision is required (e.g., exact
quotes, line references, code review).

Environment:
    TOKENPAK_PROGRESSIVE_DISCLOSURE: "on" (default) or "off"

Usage::

    from tokenpak.vault.progressive_disclosure import disclose

    disclosed_content, meta = disclose(
        content=block["content"],
        request=intent_string,
        source_path=block.get("source_path", ""),
        count_tokens_fn=my_counter,
    )
    # meta["saved_tokens"] gives token reduction achieved
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_ENV_VAR = "TOKENPAK_PROGRESSIVE_DISCLOSURE"

# Number of non-empty body lines used as the per-section summary
_SUMMARY_LINES = 3

# Maximum characters shown per section summary before truncation
_SUMMARY_MAX_CHARS = 200

# Precision-indicating keywords — any match → serve full content
_PRECISION_KEYWORDS: frozenset[str] = frozenset(
    [
        "exact",
        "verbatim",
        "quote",
        "citation",
        "cite",
        "line ",  # "line 42", "line number", etc.
        "line\t",
        "lineno",
        "line_number",
        "code at",
        "code review",
        "specific line",
        "character ",
        "column ",
        "offset ",
        "diff ",
    ]
)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def is_enabled() -> bool:
    """Return True unless ``TOKENPAK_PROGRESSIVE_DISCLOSURE`` is set to off."""
    val = os.environ.get(_ENV_VAR, "on").strip().lower()
    return val not in ("off", "false", "0", "no")


def extract_section_map(content: str) -> List[Dict[str, Any]]:
    """Parse headings from document content and produce per-section summaries.

    Uses structural extraction only (headings + first N non-empty body lines).
    No LLM calls are made.

    Args:
        content: Raw document text (Markdown or plain text).

    Returns:
        List of section dicts, each containing:
            - ``section_id``: URL-slug of the title (or ``"content"`` for implicit)
            - ``title``: Heading text (or ``"(content)"`` for implicit)
            - ``depth``: Heading level 1-6 (0 for implicit)
            - ``summary``: First N non-empty lines of the section body
            - ``line_start``: Line index (0-based) where the section begins
            - ``line_end``: Line index (0-based) where the section ends
    """
    lines = content.splitlines()
    sections: List[Dict[str, Any]] = []

    current_section: Optional[Dict[str, Any]] = None
    current_body_lines: List[str] = []

    def _finalise(sec: Dict[str, Any], body: List[str], end_line: int) -> None:
        non_empty = [l for l in body if l.strip()]
        summary_text = " ".join(non_empty[:_SUMMARY_LINES]).strip()
        if len(summary_text) > _SUMMARY_MAX_CHARS:
            summary_text = summary_text[:_SUMMARY_MAX_CHARS] + "…"
        sec["summary"] = summary_text
        sec["line_end"] = end_line
        sections.append(sec)

    heading_re = re.compile(r"^(#{1,6})\s+(.+)")

    for i, line in enumerate(lines):
        m = heading_re.match(line)
        if m:
            if current_section is not None:
                _finalise(current_section, current_body_lines, i - 1)
            depth = len(m.group(1))
            title = m.group(2).strip()
            section_id = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "section"
            current_section = {
                "section_id": section_id,
                "title": title,
                "depth": depth,
                "summary": "",
                "line_start": i,
                "line_end": len(lines) - 1,
            }
            current_body_lines = []
        elif current_section is not None:
            current_body_lines.append(line)

    # Finalise the last section
    if current_section is not None:
        _finalise(current_section, current_body_lines, len(lines) - 1)

    # No headings found — create a single implicit section
    if not sections and content.strip():
        non_empty = [l for l in lines if l.strip()]
        summary_text = " ".join(non_empty[:_SUMMARY_LINES]).strip()
        if len(summary_text) > _SUMMARY_MAX_CHARS:
            summary_text = summary_text[:_SUMMARY_MAX_CHARS] + "…"
        sections.append(
            {
                "section_id": "content",
                "title": "(content)",
                "depth": 0,
                "summary": summary_text,
                "line_start": 0,
                "line_end": max(0, len(lines) - 1),
            }
        )

    return sections


def assess_intent(request: Any) -> bool:
    """Return True if the request signals precision is required.

    Checks ``request`` for keywords that indicate the caller needs exact,
    verbatim, or line-level content (e.g., quotes, code review, line
    references). If any keyword matches, full content must be served.

    Args:
        request: A plain string, or a dict with any of the keys
                 ``query``, ``intent``, ``content``, ``message``, ``text``.
                 Any other type returns False (safe default: no precision).

    Returns:
        True if precision required; False if summary is sufficient.
    """
    if isinstance(request, str):
        text = request.lower()
    elif isinstance(request, dict):
        parts: List[str] = []
        for key in ("query", "intent", "content", "message", "text"):
            val = request.get(key, "")
            if isinstance(val, str):
                parts.append(val)
        text = " ".join(parts).lower()
    else:
        return False

    return any(kw in text for kw in _PRECISION_KEYWORDS)


def render_section_map(content: str) -> str:
    """Render a compact section map from document content.

    Args:
        content: Full document text.

    Returns:
        Compact text representation of the section map with per-section
        summaries.  Returns the original *content* unchanged if no
        structure can be extracted.
    """
    sections = extract_section_map(content)
    if not sections:
        return content

    parts: List[str] = ["[section map]"]
    for sec in sections:
        indent = "  " * max(0, sec.get("depth", 1) - 1)
        parts.append(f"{indent}# {sec['title']}")
        if sec["summary"]:
            parts.append(f"{indent}  {sec['summary']}")

    return "\n".join(parts)


def disclose(
    content: str,
    request: Any = "",
    source_path: str = "",
    count_tokens_fn: Optional[Callable[[str], int]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """Apply progressive disclosure to a single document block.

    Decision logic:
    - If the middleware is disabled (env var): return content unchanged.
    - If ``assess_intent(request)`` signals precision required: return full content.
    - Otherwise: return ``render_section_map(content)`` and log token savings.
      If the rendered map is not smaller than the full content, fall back to
      full content (no point compressing already-tiny documents).

    Args:
        content: Full document content to potentially compress.
        request: Request context used by :func:`assess_intent`.
        source_path: Source identifier used in log messages only.
        count_tokens_fn: Optional callable ``(text) -> int``.  Falls back to
                         ``len(text) // 4`` if not provided.

    Returns:
        Tuple of ``(content_to_inject, metadata_dict)``.

        ``metadata_dict`` contains:
            - ``mode``: ``"bypass"`` | ``"full"`` | ``"summary"``
            - ``saved_tokens``: int (0 when mode != "summary")
            - ``full_tokens``: int (present when mode == "summary")
            - ``summary_tokens``: int (present when mode == "summary")
            - ``reason``: str (present when mode == "full" to explain why)
    """
    if not is_enabled():
        return content, {"mode": "bypass", "saved_tokens": 0}

    if count_tokens_fn is None:

        def count_tokens_fn(t: str) -> int:
            return max(1, len(t) // 4)

    if assess_intent(request):
        return content, {
            "mode": "full",
            "saved_tokens": 0,
            "reason": "precision_intent_detected",
        }

    rendered = render_section_map(content)

    full_tokens = count_tokens_fn(content)
    summary_tokens = count_tokens_fn(rendered)
    saved_tokens = max(0, full_tokens - summary_tokens)

    if saved_tokens <= 0:
        # No savings achievable (e.g., tiny or unstructured doc)
        return content, {"mode": "full", "saved_tokens": 0, "reason": "no_savings"}

    logger.debug(
        "progressive_disclosure: %s → summary_map (full=%d tok, summary=%d tok, saved=%d tok)",
        source_path or "unknown",
        full_tokens,
        summary_tokens,
        saved_tokens,
    )

    return rendered, {
        "mode": "summary",
        "full_tokens": full_tokens,
        "summary_tokens": summary_tokens,
        "saved_tokens": saved_tokens,
        "source_path": source_path,
    }
