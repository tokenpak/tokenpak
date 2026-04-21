"""Phase 4C — Segmentization Engine.

Classifies each message in a canonical messages list into a typed
``Segment`` so downstream telemetry can track per-segment token
consumption and compression gains.

Adapted from the TokenPak telemetry segmentizer; all imports are
self-contained (no tokenpak.telemetry references).

Usage::

    from tokenpak.agent.compression.segmentizer import segmentize, SegmentType

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

# ---------------------------------------------------------------------------
# Inline model enums (self-contained — no tokenpak.telemetry dependency)
# ---------------------------------------------------------------------------


class StaleReason(str, Enum):
    NOT_STALE = "not_stale"
    OLD_TOOL_OUTPUT = "old_tool_output"
    UNREFERENCED_MEMORY = "unreferenced_memory"
    SUPERSEDED_RETRIEVAL = "superseded_retrieval"
    STALE_ASSISTANT_TURN = "stale_assistant_turn"
    DUPLICATE_CONTENT = "duplicate_content"


class AntiPattern(str, Enum):
    NONE = "none"
    REPEATED_SYSTEM_PROMPT = "repeated_system_prompt"
    ECHO_REQUEST = "echo_request"
    VERBOSE_STRUCTURED = "verbose_structured"
    REDUNDANT_INSTRUCTION = "redundant_instruction"
    BOILERPLATE_FILLER = "boilerplate_filler"


ANTI_PATTERN_PENALTIES: dict[AntiPattern, float] = {
    AntiPattern.NONE: 0.0,
    AntiPattern.REPEATED_SYSTEM_PROMPT: -0.4,
    AntiPattern.ECHO_REQUEST: -0.3,
    AntiPattern.VERBOSE_STRUCTURED: -0.15,
    AntiPattern.REDUNDANT_INSTRUCTION: -0.35,
    AntiPattern.BOILERPLATE_FILLER: -0.5,
}

STALE_SCORE_PENALTIES: dict[str, float] = {
    StaleReason.NOT_STALE.value: 0.0,
    StaleReason.OLD_TOOL_OUTPUT.value: -0.3,
    StaleReason.STALE_ASSISTANT_TURN.value: -0.2,
    StaleReason.UNREFERENCED_MEMORY.value: -0.25,
    StaleReason.DUPLICATE_CONTENT.value: -0.4,
    StaleReason.SUPERSEDED_RETRIEVAL.value: -0.35,
}


# ---------------------------------------------------------------------------
# Segment type taxonomy
# ---------------------------------------------------------------------------


class SegmentType(str, Enum):
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
# Segment dataclass
# ---------------------------------------------------------------------------


@dataclass
class Segment:
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
    actions: str = "[]"
    relevance_score: float = 0.5
    stale_reason: str = StaleReason.NOT_STALE.value
    anti_pattern: str = AntiPattern.NONE.value


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_NS = uuid.UUID("b3f4a1e2-1c2d-4e5f-8a6b-7c8d9e0f1a2b")


def _make_segment_id(trace_id: str, order: int) -> str:
    return str(uuid.uuid5(_NS, f"{trace_id}:{order}"))


def _content_to_str(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "image":
                    src = block.get("source", {})
                    parts.append(f"[image:{src.get('type', '')}:{str(src.get('data', ''))[:32]}]")
                else:
                    parts.append(json.dumps(block, ensure_ascii=False))
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _estimate_tokens(text: str) -> int:
    return len(text) // 4


def _has_image_block(content: Any) -> bool:
    if not isinstance(content, list):
        return False
    return any(isinstance(b, dict) and b.get("type") == "image" for b in content)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

_RETRIEVAL_MARKERS = ("TOKPAK:1", "TOKPAK:2", "TOKPAK:", "COMPRESS:")
_MEMORY_MARKERS = ("MEMORY.md", "[memory]", "[[memory]]", "MEMORY:", "<memory>")


def _classify(msg: dict[str, Any], *, is_last_assistant: bool, has_tools: bool) -> SegmentType:
    role: str = msg.get("role", "")
    content: Any = msg.get("content", "")

    if role == "system":
        return SegmentType.system
    if role == "developer":
        return SegmentType.developer

    content_str = _content_to_str(content)
    if any(m in content_str for m in _RETRIEVAL_MARKERS):
        return SegmentType.retrieval
    if any(m in content_str for m in _MEMORY_MARKERS):
        return SegmentType.memory
    if role == "tool" or ("tool_use_id" in msg) or ("tool_call_id" in msg):
        return SegmentType.tool_output
    if role == "assistant" and not is_last_assistant:
        return SegmentType.assistant_context
    if role == "user" and _has_image_block(content):
        return SegmentType.image
    if role == "user":
        return SegmentType.user
    return SegmentType.other


# ---------------------------------------------------------------------------
# Relevance scoring
# ---------------------------------------------------------------------------


def score_segment_relevance(segment: Segment, context: dict[str, Any]) -> float:
    seg_type = segment.segment_type
    order = segment.order
    total_turns = context.get("total_turns", 1)
    current_turn_index = context.get("current_turn_index", total_turns - 1)

    if seg_type == SegmentType.system.value:
        return 1.0
    if seg_type == SegmentType.user.value:
        return 1.0 if order >= current_turn_index else 0.4
    if seg_type == SegmentType.tool_output.value:
        return 1.0 if order >= max(0, total_turns - 2) else 0.3
    if seg_type == SegmentType.memory.value:
        return 0.7
    if seg_type == SegmentType.assistant_context.value:
        return 0.7 if order >= max(0, total_turns - 3) else 0.4
    if seg_type == SegmentType.retrieval.value:
        return 0.4
    if seg_type == SegmentType.tool_schema.value:
        return 0.3
    return 0.1


# ---------------------------------------------------------------------------
# Stale detection
# ---------------------------------------------------------------------------


def jaccard_4gram(a: str, b: str) -> float:
    def ngrams(text: str, n: int = 4) -> set[str]:
        if len(text) < n:
            return set()
        return set(text[i : i + n] for i in range(len(text) - n + 1))

    sa, sb = ngrams(a), ngrams(b)
    if not sa and not sb:
        return 1.0
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def _extract_file_path(segment: Segment, content_map: dict[int, str]) -> str | None:
    content = content_map.get(segment.order, "")
    if not content:
        return None
    patterns = [
        r"(?:path|file|source):\s*([^\s\n]+)",
        r"from\s+([^\s:]+):",
        r"\[([^\]]+\.\w{1,5})\]",
        r"^([^\s\n]+\.\w{1,5})(?::|$)",
    ]
    for pattern in patterns:
        m = re.search(pattern, content, re.IGNORECASE | re.MULTILINE)
        if m:
            return m.group(1)
    return None


def _extract_memory_terms(content: str) -> set[str]:
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
    if not segments:
        return segments

    total_turns = context.get("total_turns", len(segments))
    recent_msgs = context.get("recent_user_messages", [])
    content_map = content_map or {}

    recent_terms: set[str] = set()
    for msg in recent_msgs:
        recent_terms.update(_extract_memory_terms(msg))

    retrieval_paths: dict[str, int] = {}
    for seg in segments:
        if seg.segment_type == SegmentType.retrieval.value:
            path = _extract_file_path(seg, content_map)
            if path:
                if path not in retrieval_paths or seg.order > retrieval_paths[path]:
                    retrieval_paths[path] = seg.order

    duplicate_marked: set[int] = set()

    for seg in segments:
        if seg.stale_reason != StaleReason.NOT_STALE.value:
            continue
        seg_type = seg.segment_type
        order = seg.order

        if seg_type == SegmentType.tool_output.value:
            if order < (total_turns - 3):
                seg.stale_reason = StaleReason.OLD_TOOL_OUTPUT.value
        elif seg_type == SegmentType.assistant_context.value:
            if order < (total_turns - 6):
                seg.stale_reason = StaleReason.STALE_ASSISTANT_TURN.value
        elif seg_type == SegmentType.memory.value:
            content = content_map.get(order, "")
            memory_terms = _extract_memory_terms(content)
            if memory_terms and not (memory_terms & recent_terms):
                seg.stale_reason = StaleReason.UNREFERENCED_MEMORY.value
        elif seg_type == SegmentType.retrieval.value:
            path = _extract_file_path(seg, content_map)
            if path and path in retrieval_paths:
                if order < retrieval_paths[path]:
                    seg.stale_reason = StaleReason.SUPERSEDED_RETRIEVAL.value

    for i, seg_a in enumerate(segments):
        if seg_a.order in duplicate_marked:
            continue
        content_a = content_map.get(seg_a.order, "")
        if not content_a:
            continue
        for j, seg_b in enumerate(segments):
            if i >= j:
                continue
            if seg_b.order in duplicate_marked:
                continue
            content_b = content_map.get(seg_b.order, "")
            if not content_b:
                continue
            if jaccard_4gram(content_a, content_b) > 0.85:
                if seg_a.relevance_score < seg_b.relevance_score:
                    seg_a.stale_reason = StaleReason.DUPLICATE_CONTENT.value
                    duplicate_marked.add(seg_a.order)
                elif seg_b.relevance_score < seg_a.relevance_score:
                    seg_b.stale_reason = StaleReason.DUPLICATE_CONTENT.value
                    duplicate_marked.add(seg_b.order)
                else:
                    if seg_a.order < seg_b.order:
                        seg_a.stale_reason = StaleReason.DUPLICATE_CONTENT.value
                        duplicate_marked.add(seg_a.order)
                    else:
                        seg_b.stale_reason = StaleReason.DUPLICATE_CONTENT.value
                        duplicate_marked.add(seg_b.order)

    for seg in segments:
        penalty = STALE_SCORE_PENALTIES.get(seg.stale_reason, 0.0)
        if penalty != 0.0:
            seg.relevance_score = max(0.0, min(1.0, seg.relevance_score + penalty))

    return segments


# ---------------------------------------------------------------------------
# Anti-pattern detection
# ---------------------------------------------------------------------------

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

_JSON_PATTERN = re.compile(r"^\s*[\[{]", re.MULTILINE)
_XML_PATTERN = re.compile(r"^\s*<[a-zA-Z]", re.MULTILINE)


def detect_anti_patterns(
    segments: list[Segment],
    content_map: dict[int, str],
) -> list[Segment]:
    if not segments:
        return segments

    system_contents: list[tuple[int, str]] = []
    for seg in segments:
        if seg.segment_type in (SegmentType.system.value, "system"):
            content = content_map.get(seg.order, "")
            if content:
                system_contents.append((seg.order, content.lower()))

    for i, (order_a, content_a) in enumerate(system_contents):
        for order_b, content_b in system_contents[i + 1 :]:
            if jaccard_4gram(content_a, content_b) > 0.9:
                for seg in segments:
                    if seg.order == order_b and seg.anti_pattern == AntiPattern.NONE.value:
                        seg.anti_pattern = AntiPattern.REPEATED_SYSTEM_PROMPT.value
                        break

    user_contents: dict[int, str] = {}
    for seg in segments:
        if seg.segment_type in (SegmentType.user.value, "user"):
            content = content_map.get(seg.order, "")
            if content:
                user_contents[seg.order] = content.lower()[:200]

    for seg in segments:
        if seg.anti_pattern != AntiPattern.NONE.value:
            continue
        content = content_map.get(seg.order, "")
        content_lower = content.lower()

        for phrase in BOILERPLATE_PATTERNS:
            if content_lower.lstrip().startswith(phrase):
                seg.anti_pattern = AntiPattern.BOILERPLATE_FILLER.value
                break

        if seg.anti_pattern != AntiPattern.NONE.value:
            continue

        if _JSON_PATTERN.search(content) or _XML_PATTERN.search(content):
            if len(content) // 4 > 500:
                seg.anti_pattern = AntiPattern.VERBOSE_STRUCTURED.value
                continue

        if seg.segment_type in (SegmentType.tool_output.value, "tool_output"):
            seg_start = content_lower[:100]
            for user_order in sorted(user_contents.keys(), reverse=True):
                if user_order < seg.order:
                    user_content = user_contents[user_order]
                    if seg_start.strip() == user_content.strip():
                        seg.anti_pattern = AntiPattern.ECHO_REQUEST.value
                    elif len(seg_start) > 20 and len(user_content) > 20:
                        if jaccard_4gram(seg_start, user_content[:100]) > 0.7:
                            seg.anti_pattern = AntiPattern.ECHO_REQUEST.value
                    break

    non_system: list[tuple[int, str]] = []
    for seg in segments:
        if seg.segment_type not in (SegmentType.system.value, "system"):
            content = content_map.get(seg.order, "")
            if len(content) > 50:
                non_system.append((seg.order, content.lower()))

    redundant_marked: set[int] = set()
    for i, (order_a, content_a) in enumerate(non_system):
        if order_a in redundant_marked:
            continue
        for order_b, content_b in non_system[i + 1 :]:
            if order_b in redundant_marked:
                continue
            if jaccard_4gram(content_a, content_b) > 0.8:
                for seg in segments:
                    if seg.order == order_b and seg.anti_pattern == AntiPattern.NONE.value:
                        seg.anti_pattern = AntiPattern.REDUNDANT_INSTRUCTION.value
                        redundant_marked.add(order_b)
                        break

    for seg in segments:
        pattern = AntiPattern(seg.anti_pattern) if seg.anti_pattern else AntiPattern.NONE
        penalty = ANTI_PATTERN_PENALTIES.get(pattern, 0.0)
        if penalty != 0.0:
            seg.relevance_score = max(0.0, min(1.0, seg.relevance_score + penalty))

    return segments


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def segmentize(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    *,
    trace_id: str = "",
) -> list[Segment]:
    """Classify *messages* into an ordered list of :class:`Segment` objects."""
    if not messages:
        return []

    for i, msg in enumerate(messages):
        if msg.get("role") == "assistant":
            pass

    has_tools = bool(tools)
    segments: list[Segment] = []
    content_map: dict[int, str] = {}

    last_msg_idx = len(messages) - 1
    scoring_context = {
        "total_turns": len(messages),
        "current_turn_index": last_msg_idx,
    }

    for order, msg in enumerate(messages):
        role = msg.get("role", "")
        content = msg.get("content", "")
        is_last_assistant = (role == "assistant") and (order == last_msg_idx)

        seg_type = _classify(msg, is_last_assistant=is_last_assistant, has_tools=has_tools)
        content_str = _content_to_str(content)
        content_map[order] = content_str
        raw_hash = _sha256(content_str)
        raw_len = len(content_str)
        tokens_raw = _estimate_tokens(content_str)

        seg = Segment(
            trace_id=trace_id,
            segment_id=_make_segment_id(trace_id, order),
            order=order,
            segment_type=seg_type.value,
            raw_hash=raw_hash,
            raw_len=raw_len,
            tokens_raw=tokens_raw,
        )
        seg.relevance_score = score_segment_relevance(seg, scoring_context)
        segments.append(seg)

    if has_tools:
        tools_str = json.dumps(tools, ensure_ascii=False)
        order = len(messages)
        seg = Segment(
            trace_id=trace_id,
            segment_id=_make_segment_id(trace_id, order),
            order=order,
            segment_type=SegmentType.tool_schema.value,
            raw_hash=_sha256(tools_str),
            raw_len=len(tools_str),
            tokens_raw=_estimate_tokens(tools_str),
        )
        seg.relevance_score = score_segment_relevance(seg, scoring_context)
        segments.append(seg)
        content_map[order] = tools_str

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
    detect_anti_patterns(segments, content_map)

    if os.getenv("TOKENPAK_PRUNE_ANTIPATTERNS", "").strip().lower() in {"1", "true", "yes", "on"}:
        _prunable = {
            AntiPattern.REPEATED_SYSTEM_PROMPT.value,
            AntiPattern.ECHO_REQUEST.value,
            AntiPattern.VERBOSE_STRUCTURED.value,
            AntiPattern.REDUNDANT_INSTRUCTION.value,
            AntiPattern.BOILERPLATE_FILLER.value,
        }
        pruned = [s for s in segments if s.anti_pattern not in _prunable]
        if pruned:
            segments = pruned

    return segments
