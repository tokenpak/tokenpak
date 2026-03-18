"""Phase 4C — Segmentization Engine.

Classifies each message in a canonical messages list into a typed
``Segment`` so downstream telemetry can track per-segment token
consumption and compression gains.

Usage::

    from tokenpak.telemetry.segmentizer import segmentize, SegmentType

    segments = segmentize(messages, tools=tools)
    for seg in segments:
        # seg.segment_type, seg.tokens_raw
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Any

from tokenpak.telemetry.models import ANTI_PATTERN_PENALTIES, AntiPattern, StaleReason

# ---------------------------------------------------------------------------
# Segment type taxonomy
# ---------------------------------------------------------------------------


class SegmentType(str, Enum):
    """All recognised segment types for TokenPak telemetry."""

    system = "system"
    developer = "developer"
    user = "user"
    assistant_context = "assistant_context"
    memory = "memory"
    retrieval = "retrieval"
    tool_schema = "tool_schema"
    tool_output = "tool_output"
    guardrail = "guardrail"
    image = "image"
    other = "other"


# ---------------------------------------------------------------------------
# Segment dataclass (Phase 4C canonical version)
# ---------------------------------------------------------------------------


@dataclass
class Segment:
    """A single classified segment extracted from a messages payload.

    Fields
    ------
    trace_id:
        Caller-supplied identifier for the full request trace.
    segment_id:
        Deterministic UUID5 derived from *trace_id* and *order*.
    order:
        Zero-based position in the original messages list (plus one
        synthetic entry for tool_schema when ``tools`` is provided).
    segment_type:
        One of the :class:`SegmentType` values, stored as a plain string
        for easy JSON serialisation.
    raw_hash:
        SHA-256 hex digest of the raw content string.
    final_hash:
        SHA-256 of post-compression content (empty until compression runs).
    raw_len:
        Character count of the raw content string.
    final_len:
        Character count after compression (0 until compression runs).
    tokens_raw:
        Rough token estimate for the raw content (``raw_len // 4``).
    tokens_after_qmd:
        Tokens after QMD compression pass (0 until that pass runs).
    tokens_after_tp:
        Tokens after TokenPak compression pass (0 until that pass runs).
    actions:
        JSON string listing compression actions applied to this segment.
    """

    trace_id: str = ""
    segment_id: str = ""
    order: int = 0
    segment_type: str = SegmentType.other.value
    raw_hash: str = ""
    final_hash: str = ""
    raw_len: int = 0
    final_len: int = 0
    tokens_raw: int = 0
    tokens_after_qmd: int = 0
    tokens_after_tp: int = 0
    actions: str = "[]"  # JSON array of action strings
    relevance_score: float = 0.5  # 0.0-1.0; default neutral
    stale_reason: str = StaleReason.NOT_STALE.value  # Phase 7D
    anti_pattern: str = AntiPattern.NONE.value  # Phase 7E


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

# Namespace UUID used for all UUID5 derivation — fixed so segment_ids are
# reproducible across processes and machines.
_NS = uuid.UUID("b3f4a1e2-1c2d-4e5f-8a6b-7c8d9e0f1a2b")


def _make_segment_id(trace_id: str, order: int) -> str:
    """Return a deterministic UUID5 for *trace_id* + *order*."""
    key = f"{trace_id}:{order}"
    return str(uuid.uuid5(_NS, key))


def _content_to_str(content: Any) -> str:
    """Flatten *content* (str or list of blocks) to a plain string."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "image":
                    # Represent image blocks by a short sentinel so hashing
                    # still reflects identity.
                    src = block.get("source", {})
                    data_preview = str(src.get("data", ""))[:32]
                    parts.append(f"[image:{src.get('type','')}:{data_preview}]")
                else:
                    # tool_use / tool_result blocks etc.
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    # Fallback: JSON-encode whatever we got.
    return json.dumps(content, ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def _has_image_block(content: Any) -> bool:
    """Return True if *content* is a list containing at least one image block."""
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "image" for b in content)


def _content_has_str(content: Any, marker: str) -> bool:
    """Return True if any text in *content* contains *marker* (case-sensitive)."""
    text = _content_to_str(content)
    return marker in text


# ---------------------------------------------------------------------------
# Classification logic
# ---------------------------------------------------------------------------

# Compression-related markers recognised as retrieval segments.
_RETRIEVAL_MARKERS = ("TOKPAK:1", "TOKPAK:2", "TOKPAK:", "COMPRESS:")
# Memory-related markers.
_MEMORY_MARKERS = ("MEMORY.md", "[memory]", "[[memory]]", "MEMORY:", "<memory>")


def _classify(
    msg: dict[str, Any],
    *,
    is_last_assistant: bool,
    has_tools: bool,
) -> SegmentType:
    """Apply detection rules in priority order and return a :class:`SegmentType`."""
    role: str = msg.get("role", "")
    content: Any = msg.get("content", "")

    # 1. System
    if role == "system":
        return SegmentType.system

    # 2. Developer
    if role == "developer":
        return SegmentType.developer

    # 3. Retrieval — compression markers in content (checked before memory)
    content_str = _content_to_str(content)
    if any(m in content_str for m in _RETRIEVAL_MARKERS):
        return SegmentType.retrieval

    # 4. Memory markers
    if any(m in content_str for m in _MEMORY_MARKERS):
        return SegmentType.memory

    # 5. Tool output: role=="tool" OR message has tool_use_id / tool_call_id
    if role == "tool" or ("tool_use_id" in msg) or ("tool_call_id" in msg):
        return SegmentType.tool_output

    # 6. Prior assistant turns (not the final assistant message)
    if role == "assistant" and not is_last_assistant:
        return SegmentType.assistant_context

    # 7. User + image content
    if role == "user" and _has_image_block(content):
        return SegmentType.image

    # 8. Plain user
    if role == "user":
        return SegmentType.user

    # 9. Final assistant turn that wasn't captured above → other
    return SegmentType.other


# ---------------------------------------------------------------------------
# Phase 7B: Relevance scoring & coverage metrics
# ---------------------------------------------------------------------------


def extract_query_terms(query: str) -> list[str]:
    """Extract key identifiers from a query string.

    Extracts:
    - CamelCase words (e.g., MyClass, SomeError)
    - snake_case_identifiers
    - file paths (foo/bar.py, *.ts)
    - quoted strings
    - words after "find", "where is", "locate", "what is"

    Parameters
    ----------
    query:
        Query string to analyze.

    Returns
    -------
    list[str]
        List of extracted term strings (may contain duplicates).
    """
    import re

    terms: list[str] = []

    # CamelCase words
    terms.extend(re.findall(r"\b[A-Z][a-z]+(?:[A-Z][a-z]+)+\b", query))

    # snake_case identifiers
    terms.extend(re.findall(r"\b[a-z_][a-z0-9_]{2,}\b", query))

    # File paths (foo/bar.py, *.ts, etc.)
    terms.extend(re.findall(r"[\w\-\.]+/[\w\-\./]+\.\w+", query))
    terms.extend(re.findall(r"\*\.\w+", query))

    # Quoted strings
    terms.extend(re.findall(r'"([^"]+)"', query))
    terms.extend(re.findall(r"'([^']+)'", query))

    # Words after key phrases
    for pattern in [
        r"(?:find|where is|locate|what is)\s+(\w+)",
        r"(?:error|exception)\s+(\w+)",
    ]:
        terms.extend(re.findall(pattern, query, re.IGNORECASE))

    return terms


def score_segment_relevance(segment: Segment, context: dict[str, Any]) -> float:
    """Score a segment's relevance (0.0-1.0) based on type and context.

    Scoring rules:
    - system: 1.0 (always critical)
    - user (current turn): 1.0 (the actual question)
    - tool_output (most recent): 1.0 (active context)
    - memory: 0.7 (important but stable)
    - assistant_context (last 2 turns): 0.7 (recent dialogue)
    - retrieval: 0.4 (retrieved chunks, quality varies)
    - assistant_context (older turns): 0.4 (less relevant history)
    - tool_schema: 0.3 (usually boilerplate)
    - tool_output (non-recent): 0.3 (stale results)
    - other: 0.1 (background/boilerplate)

    Parameters
    ----------
    segment:
        Segment to score.
    context:
        Context dict with keys:
        - current_turn_index (int): index of the current turn
        - total_turns (int): total number of turns

    Returns
    -------
    float
        Relevance score in range [0.0, 1.0].
    """
    seg_type = segment.segment_type
    order = segment.order
    total_turns = context.get("total_turns", 1)
    current_turn_index = context.get("current_turn_index", total_turns - 1)

    # System messages are always critical
    if seg_type == SegmentType.system.value:
        return 1.0

    # User message (current turn)
    if seg_type == SegmentType.user.value:
        # If this is the current turn, it's critical
        if order >= current_turn_index:
            return 1.0
        # Older user messages: moderate relevance
        return 0.4

    # Tool outputs
    if seg_type == SegmentType.tool_output.value:
        # Most recent tool output is active context
        if order >= max(0, total_turns - 2):
            return 1.0
        return 0.3

    # Memory segments
    if seg_type == SegmentType.memory.value:
        return 0.7

    # Assistant messages
    if seg_type == SegmentType.assistant_context.value:
        # Last 2 turns: high relevance
        if order >= max(0, total_turns - 3):
            return 0.7
        return 0.4

    # Retrieval chunks
    if seg_type == SegmentType.retrieval.value:
        return 0.4

    # Tool schemas
    if seg_type == SegmentType.tool_schema.value:
        return 0.3

    # Other/unknown
    return 0.1


def compute_coverage_score(
    chunks: list[dict[str, Any]],
    query_terms: list[str],
) -> float:
    """Compute retrieval coverage confidence (0-1).

    Measures how well retrieved chunks cover the query's key terms.

    Components:
    - must_hit_factor (max 0.45): all query_terms found in at least one chunk
    - concentration_factor (max 0.25): fewer unique source paths = more focused
    - mass_factor (max 0.30): sum of top-5 chunk scores / 4.0 (clamped)

    Thresholds:
    - >= 0.75: strong coverage, use base tier
    - 0.55-0.75: ok coverage, proceed
    - < 0.55: weak, trigger second retrieval pass or tier escalation

    Parameters
    ----------
    chunks:
        Retrieved chunks, each a dict with keys:
        - text (str): chunk content
        - score (float): retrieval score
        - path (str, optional): source file/doc path
    query_terms:
        Key identifiers extracted from the query.

    Returns
    -------
    float
        Coverage score in range [0.0, 1.0].
    """
    if not chunks:
        return 0.0

    # must_hit_factor: all query terms present in at least one chunk
    all_text = " ".join(c.get("text", "").lower() for c in chunks)
    must_hit_satisfied = (
        all(term.lower() in all_text for term in query_terms) if query_terms else True
    )
    must_hit_factor = 0.45 if must_hit_satisfied else 0.0

    # concentration_factor: fewer unique source paths = more focused
    unique_files = len(set(c.get("path", f"unknown_{i}") for i, c in enumerate(chunks)))
    concentration = max(0.0, min(0.25, 1.0 - (unique_files - 1) * 0.15))

    # mass_factor: sum of top-5 chunk scores / 4.0 (clamped to 0.30)
    top5_scores = sorted((c.get("score", 0.0) for c in chunks), reverse=True)[:5]
    mass = max(0.0, min(0.30, sum(top5_scores) / 4.0))

    return must_hit_factor + concentration + mass


# ---------------------------------------------------------------------------
# Phase 7D: Stale Segment Detection
# ---------------------------------------------------------------------------

# Score penalties for stale segments — applied after relevance scoring.
STALE_SCORE_PENALTIES: dict[str, float] = {
    StaleReason.NOT_STALE.value: 0.0,
    StaleReason.OLD_TOOL_OUTPUT.value: -0.3,
    StaleReason.STALE_ASSISTANT_TURN.value: -0.2,
    StaleReason.UNREFERENCED_MEMORY.value: -0.25,
    StaleReason.DUPLICATE_CONTENT.value: -0.4,
    StaleReason.SUPERSEDED_RETRIEVAL.value: -0.35,
}


def jaccard_4gram(a: str, b: str) -> float:
    """Compute Jaccard similarity of 4-gram sets between two strings.

    Parameters
    ----------
    a:
        First string.
    b:
        Second string.

    Returns
    -------
    float
        Jaccard similarity in range [0.0, 1.0].
        Returns 1.0 if both strings are empty.
        Returns 0.0 if either string is too short for 4-grams.
    """

    def ngrams(text: str, n: int = 4) -> set[str]:
        """Yield all n-grams of length n from the token list."""
        if len(text) < n:
            return set()
        return set(text[i : i + n] for i in range(len(text) - n + 1))

    sa, sb = ngrams(a), ngrams(b)

    # Both empty strings: identical
    if not sa and not sb:
        return 1.0

    # One has 4-grams, other doesn't: no overlap
    if not sa or not sb:
        return 0.0

    return len(sa & sb) / len(sa | sb)


def _extract_file_path(segment: Segment, content_map: dict[int, str]) -> str | None:
    """Extract file path from a retrieval segment's content.

    Looks for common patterns like:
    - path: foo/bar.py
    - file: foo/bar.py
    - from foo/bar.py:
    - [foo/bar.py]

    Returns
    -------
    str | None
        Extracted path, or None if not found.
    """
    content = content_map.get(segment.order, "")
    if not content:
        return None

    # Common patterns for file path references
    patterns = [
        r"(?:path|file|source):\s*([^\s\n]+)",  # path: foo/bar.py
        r"from\s+([^\s:]+):",  # from foo/bar.py:
        r"\[([^\]]+\.\w{1,5})\]",  # [foo/bar.py]
        r"^([^\s\n]+\.\w{1,5})(?::|$)",  # foo/bar.py: at start
    ]

    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE | re.MULTILINE)
        if match:
            return match.group(1)

    return None


def _extract_memory_terms(content: str) -> set[str]:
    """Extract key terms from a memory segment for reference checking.

    Extracts words >= 4 chars, lowercased, excluding common stopwords.
    """
    stopwords = {
        "this",
        "that",
        "with",
        "from",
        "have",
        "will",
        "your",
        "what",
        "when",
        "where",
        "which",
        "there",
        "their",
        "about",
        "would",
        "could",
        "should",
        "been",
        "were",
        "more",
        "some",
        "than",
        "them",
        "then",
        "into",
        "also",
        "only",
        "other",
        "over",
        "such",
        "each",
    }
    words = re.findall(r"\b[a-zA-Z]{4,}\b", content.lower())
    return set(w for w in words if w not in stopwords)


def detect_stale(
    segments: list[Segment],
    context: dict[str, Any],
    content_map: dict[int, str] | None = None,
) -> list[Segment]:
    """Detect stale segments and apply relevance score penalties.

    Parameters
    ----------
    segments:
        List of segments to analyze.
    context:
        Context dict with keys:
        - total_turns (int): total number of turns in conversation
        - recent_user_messages (list[str]): last 3 user messages for memory ref check
    content_map:
        Optional mapping of segment.order -> raw content string.
        Needed for DUPLICATE_CONTENT and SUPERSEDED_RETRIEVAL checks.

    Returns
    -------
    list[Segment]
        Same segments with stale_reason and adjusted relevance_score.
    """
    if not segments:
        return segments

    total_turns = context.get("total_turns", len(segments))
    recent_msgs = context.get("recent_user_messages", [])
    content_map = content_map or {}

    # Build recent user message terms for memory reference check
    recent_terms: set[str] = set()
    for msg in recent_msgs:
        recent_terms.update(_extract_memory_terms(msg))

    # Track retrieval paths for superseded detection: path -> newest order
    retrieval_paths: dict[str, int] = {}
    for seg in segments:
        if seg.segment_type == SegmentType.retrieval.value:
            path = _extract_file_path(seg, content_map)
            if path:
                # Keep track of the highest order (newest) for each path
                if path not in retrieval_paths or seg.order > retrieval_paths[path]:
                    retrieval_paths[path] = seg.order

    # Track duplicate detection: we compare each segment with all others
    # But only mark the lower-relevance one as duplicate
    duplicate_marked: set[int] = set()

    for seg in segments:
        # Skip if already classified
        if seg.stale_reason != StaleReason.NOT_STALE.value:
            continue

        seg_type = seg.segment_type
        order = seg.order

        # Rule 1: OLD_TOOL_OUTPUT — tool output > 3 turns ago
        if seg_type == SegmentType.tool_output.value:
            if order < (total_turns - 3):
                seg.stale_reason = StaleReason.OLD_TOOL_OUTPUT.value

        # Rule 2: STALE_ASSISTANT_TURN — assistant > 6 turns ago
        elif seg_type == SegmentType.assistant_context.value:
            if order < (total_turns - 6):
                seg.stale_reason = StaleReason.STALE_ASSISTANT_TURN.value

        # Rule 3: UNREFERENCED_MEMORY — memory keywords not in recent messages
        elif seg_type == SegmentType.memory.value:
            content = content_map.get(order, "")
            memory_terms = _extract_memory_terms(content)
            # Check if any memory term appears in recent user messages
            if memory_terms and not (memory_terms & recent_terms):
                seg.stale_reason = StaleReason.UNREFERENCED_MEMORY.value

        # Rule 4: SUPERSEDED_RETRIEVAL — older retrieval from same file
        elif seg_type == SegmentType.retrieval.value:
            path = _extract_file_path(seg, content_map)
            if path and path in retrieval_paths:
                newest_order = retrieval_paths[path]
                if order < newest_order:
                    seg.stale_reason = StaleReason.SUPERSEDED_RETRIEVAL.value

    # Rule 5: DUPLICATE_CONTENT — check for near-duplicates (>85% Jaccard)
    # Only mark the lower-relevance one
    for i, seg_a in enumerate(segments):
        if seg_a.order in duplicate_marked:
            continue
        content_a = content_map.get(seg_a.order, "")
        if not content_a:
            continue

        for j, seg_b in enumerate(segments):
            if i >= j:  # Only check pairs once
                continue
            if seg_b.order in duplicate_marked:
                continue
            content_b = content_map.get(seg_b.order, "")
            if not content_b:
                continue

            similarity = jaccard_4gram(content_a, content_b)
            if similarity > 0.85:
                # Mark the one with lower relevance_score
                if seg_a.relevance_score < seg_b.relevance_score:
                    seg_a.stale_reason = StaleReason.DUPLICATE_CONTENT.value
                    duplicate_marked.add(seg_a.order)
                elif seg_b.relevance_score < seg_a.relevance_score:
                    seg_b.stale_reason = StaleReason.DUPLICATE_CONTENT.value
                    duplicate_marked.add(seg_b.order)
                else:
                    # Equal scores: mark the older one (lower order)
                    if seg_a.order < seg_b.order:
                        seg_a.stale_reason = StaleReason.DUPLICATE_CONTENT.value
                        duplicate_marked.add(seg_a.order)
                    else:
                        seg_b.stale_reason = StaleReason.DUPLICATE_CONTENT.value
                        duplicate_marked.add(seg_b.order)

    # Apply score penalties
    for seg in segments:
        penalty = STALE_SCORE_PENALTIES.get(seg.stale_reason, 0.0)
        if penalty != 0.0:
            seg.relevance_score = max(0.0, min(1.0, seg.relevance_score + penalty))

    return segments


# ---------------------------------------------------------------------------
# Phase 7E: Anti-Pattern Detection
# ---------------------------------------------------------------------------

# Common boilerplate/filler phrases that waste tokens
BOILERPLATE_PATTERNS: list[str] = [
    "i'd be happy to help",
    "i would be happy to help",
    "sure!",
    "sure,",
    "of course!",
    "of course,",
    "absolutely!",
    "certainly!",
    "great question",
    "that's a great question",
    "good question",
    "let me help you",
    "i can help you",
    "i'll help you",
    "no problem",
    "happy to assist",
    "glad to help",
]

# JSON/XML detection patterns
_JSON_PATTERN = re.compile(r"^\s*[\[{]", re.MULTILINE)
_XML_PATTERN = re.compile(r"^\s*<[a-zA-Z]", re.MULTILINE)


def detect_anti_patterns(
    segments: list[Segment],
    content_map: dict[int, str],
) -> list[Segment]:
    """Detect context-stuffing anti-patterns in segments.

    Marks each segment with an ``anti_pattern`` value and applies
    relevance score penalties.

    Parameters
    ----------
    segments:
        List of Segment objects to analyze.
    content_map:
        Dict mapping segment order → raw content string.

    Returns
    -------
    list[Segment]
        The same segments, mutated in place with anti_pattern set.
    """
    if not segments:
        return segments

    # Track system prompts for repeated detection
    system_contents: list[tuple[int, str]] = []
    for seg in segments:
        if seg.segment_type in (SegmentType.system.value, "system"):
            content = content_map.get(seg.order, "")
            if content:
                system_contents.append((seg.order, content.lower()))

    # Detect REPEATED_SYSTEM_PROMPT (Jaccard >0.9 between system segments)
    for i, (order_a, content_a) in enumerate(system_contents):
        for order_b, content_b in system_contents[i + 1 :]:
            similarity = jaccard_4gram(content_a, content_b)
            if similarity > 0.9:
                # Mark the later one as repeated
                for seg in segments:
                    if seg.order == order_b and seg.anti_pattern == AntiPattern.NONE.value:
                        seg.anti_pattern = AntiPattern.REPEATED_SYSTEM_PROMPT.value
                        break

    # Build user message content list for echo detection
    user_contents: dict[int, str] = {}
    for seg in segments:
        if seg.segment_type in (SegmentType.user.value, "user"):
            content = content_map.get(seg.order, "")
            if content:
                user_contents[seg.order] = content.lower()[:200]  # first 200 chars

    # Process each segment for other anti-patterns
    for seg in segments:
        if seg.anti_pattern != AntiPattern.NONE.value:
            continue  # already classified

        content = content_map.get(seg.order, "")
        content_lower = content.lower()

        # BOILERPLATE_FILLER: starts with filler phrase
        for phrase in BOILERPLATE_PATTERNS:
            if content_lower.lstrip().startswith(phrase):
                seg.anti_pattern = AntiPattern.BOILERPLATE_FILLER.value
                break

        if seg.anti_pattern != AntiPattern.NONE.value:
            continue

        # VERBOSE_STRUCTURED: large JSON/XML blob (>500 tokens)
        if _JSON_PATTERN.search(content) or _XML_PATTERN.search(content):
            estimated_tokens = len(content) // 4
            if estimated_tokens > 500:
                seg.anti_pattern = AntiPattern.VERBOSE_STRUCTURED.value
                continue

        # ECHO_REQUEST: tool output echoes preceding user message
        if seg.segment_type in (SegmentType.tool_output.value, "tool_output"):
            seg_content_start = content_lower[:100]
            # Find most recent preceding user message
            for user_order in sorted(user_contents.keys(), reverse=True):
                if user_order < seg.order:
                    user_content = user_contents[user_order]
                    # Check overlap
                    if seg_content_start.strip() == user_content.strip():
                        seg.anti_pattern = AntiPattern.ECHO_REQUEST.value
                    elif len(seg_content_start) > 20 and len(user_content) > 20:
                        overlap = jaccard_4gram(seg_content_start, user_content[:100])
                        if overlap > 0.7:
                            seg.anti_pattern = AntiPattern.ECHO_REQUEST.value
                    break

    # REDUNDANT_INSTRUCTION: non-system segments with >80% 4-gram overlap
    non_system: list[tuple[int, str]] = []
    for seg in segments:
        if seg.segment_type not in (SegmentType.system.value, "system"):
            content = content_map.get(seg.order, "")
            if len(content) > 50:  # only check substantial content
                non_system.append((seg.order, content.lower()))

    redundant_marked: set[int] = set()
    for i, (order_a, content_a) in enumerate(non_system):
        if order_a in redundant_marked:
            continue
        for order_b, content_b in non_system[i + 1 :]:
            if order_b in redundant_marked:
                continue
            similarity = jaccard_4gram(content_a, content_b)
            if similarity > 0.8:
                # Mark the later one as redundant
                for seg in segments:
                    if seg.order == order_b and seg.anti_pattern == AntiPattern.NONE.value:
                        seg.anti_pattern = AntiPattern.REDUNDANT_INSTRUCTION.value
                        redundant_marked.add(order_b)
                        break

    # Apply score penalties
    for seg in segments:
        pattern = AntiPattern(seg.anti_pattern) if seg.anti_pattern else AntiPattern.NONE
        penalty = ANTI_PATTERN_PENALTIES.get(pattern, 0.0)
        if penalty != 0.0:
            seg.relevance_score = max(0.0, min(1.0, seg.relevance_score + penalty))

    return segments


# ---------------------------------------------------------------------------
# Phase 7E: Anti-pattern summaries + optional pruning
# ---------------------------------------------------------------------------


def summarize_anti_patterns(segments: list[Segment]) -> dict[str, object]:
    """Return a compact summary of anti-pattern flags in *segments*.

    Returns
    -------
    dict
        {
            "counts": {anti_pattern: count, ...},
            "top_offenders": [
                {"segment_id": str, "anti_pattern": str, "order": int},
                ...
            ],
        }
    """
    counts: dict[str, int] = {}
    offenders: list[dict[str, object]] = []

    for seg in segments:
        pattern = seg.anti_pattern or AntiPattern.NONE.value
        if pattern == AntiPattern.NONE.value:
            continue
        counts[pattern] = counts.get(pattern, 0) + 1
        offenders.append(
            {
                "segment_id": seg.segment_id,
                "anti_pattern": pattern,
                "order": seg.order,
            }
        )

    offenders.sort(key=lambda item: int(item.get("order", 0)))  # type: ignore
    return {
        "counts": counts,
        "top_offenders": offenders[:5],
    }


_PRUNABLE_ANTI_PATTERNS: set[str] = {
    AntiPattern.REPEATED_SYSTEM_PROMPT.value,
    AntiPattern.ECHO_REQUEST.value,
    AntiPattern.VERBOSE_STRUCTURED.value,
    AntiPattern.REDUNDANT_INSTRUCTION.value,
    AntiPattern.BOILERPLATE_FILLER.value,
}


def _should_prune_antipatterns() -> bool:
    value = os.getenv("TOKENPAK_PRUNE_ANTIPATTERNS", "").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _prune_antipattern_segments(segments: list[Segment]) -> list[Segment]:
    if not segments:
        return segments
    kept = [seg for seg in segments if seg.anti_pattern not in _PRUNABLE_ANTI_PATTERNS]
    return kept or segments


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def segmentize(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    trace_id: str = "",
) -> list[Segment]:
    """Classify *messages* into an ordered list of :class:`Segment` objects.

    Parameters
    ----------
    messages:
        Canonical messages list — each entry must have at least a ``"role"``
        key.  The ``"content"`` key may be a string or a list of content
        blocks (Anthropic / OpenAI style).
    tools:
        Optional list of tool-definition dicts.  When present, a synthetic
        ``tool_schema`` segment is appended at the end.
    trace_id:
        Caller-supplied trace identifier used to derive deterministic
        ``segment_id`` values.  Defaults to the empty string, which is
        valid (all segments will still have stable, reproducible IDs).

    Returns
    -------
    list[Segment]
        One segment per message, plus one extra ``tool_schema`` segment if
        *tools* is non-empty, ordered by their ``order`` field.
    """
    if not messages:
        return []

    # Pre-compute the index of the *last* assistant message so prior ones
    # become ``assistant_context``.
    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant":
            pass

    has_tools = bool(tools)
    segments: list[Segment] = []
    content_map: dict[int, str] = {}  # order -> raw content string

    last_msg_idx = len(messages) - 1

    # Context for relevance scoring
    scoring_context = {
        "total_turns": len(messages),
        "current_turn_index": last_msg_idx,
    }

    for order, msg in enumerate(messages):
        role = msg.get("role", "")
        content = msg.get("content", "")
        # A "final" assistant turn is one that has no messages following it.
        # If any message comes after this assistant message (regardless of role),
        # it is a "prior" turn and should become assistant_context.
        is_last_assistant = (role == "assistant") and (order == last_msg_idx)

        seg_type = _classify(
            msg,
            is_last_assistant=is_last_assistant,
            has_tools=has_tools,
        )

        content_str = _content_to_str(content)
        content_map[order] = content_str  # for stale detection
        raw_hash = _sha256(content_str)
        raw_len = len(content_str)
        tokens_raw = _estimate_tokens(content_str)

        seg = Segment(
            trace_id=trace_id,
            segment_id=_make_segment_id(trace_id, order),
            order=order,
            segment_type=seg_type.value,
            raw_hash=raw_hash,
            final_hash="",
            raw_len=raw_len,
            final_len=0,
            tokens_raw=tokens_raw,
            tokens_after_qmd=0,
            tokens_after_tp=0,
            actions="[]",
        )
        # Populate relevance score
        seg.relevance_score = score_segment_relevance(seg, scoring_context)
        segments.append(seg)

    # Synthetic tool_schema segment for the tools list itself.
    if has_tools:
        tools_str = json.dumps(tools, ensure_ascii=False)
        order = len(messages)  # appended after all messages
        seg = Segment(
            trace_id=trace_id,
            segment_id=_make_segment_id(trace_id, order),
            order=order,
            segment_type=SegmentType.tool_schema.value,
            raw_hash=_sha256(tools_str),
            final_hash="",
            raw_len=len(tools_str),
            final_len=0,
            tokens_raw=_estimate_tokens(tools_str),
            tokens_after_qmd=0,
            tokens_after_tp=0,
            actions="[]",
        )
        # Populate relevance score
        seg.relevance_score = score_segment_relevance(seg, scoring_context)
        segments.append(seg)
        content_map[order] = tools_str

    # Phase 7D: Detect stale segments and apply score penalties
    # Build context for stale detection
    recent_user_messages: list[str] = []
    for msg in reversed(messages):
        if msg.get("role") == "user":
            recent_user_messages.append(_content_to_str(msg.get("content", "")))
            if len(recent_user_messages) >= 3:
                break

    stale_context = {
        "total_turns": len(segments),
        "recent_user_messages": recent_user_messages,
    }

    detect_stale(segments, stale_context, content_map)

    # Phase 7E: Anti-pattern detection (stacks with staleness penalties)
    detect_anti_patterns(segments, content_map)

    if _should_prune_antipatterns():
        segments = _prune_antipattern_segments(segments)

    return segments
