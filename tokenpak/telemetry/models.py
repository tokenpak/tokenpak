"""Telemetry data models — Phase 4A canonical implementation.

This module owns the four core storage-layer dataclasses used throughout
the TokenPak telemetry pipeline:

- :class:`TelemetryEvent`  — per-request lifecycle event
- :class:`Segment`          — storage record for a classified message segment
- :class:`Usage`            — token-usage provenance record
- :class:`Cost`             — cost computation result

NOTE: The ``Segment`` here is the *storage* record (written to the DB).
The *working* ``Segment`` produced during segmentisation lives in
``tokenpak.telemetry.segmentizer``.  They share the same field names so
conversion is a direct ``asdict()`` / ``**kwargs`` call.

Backward-compatibility shim classes for Phase 4B are retained at the
bottom of this file under the ``_compat`` namespace to avoid breaking
any existing imports.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Phase 7D: Stale Segment Detection
# ---------------------------------------------------------------------------


class StaleReason(str, Enum):
    """Reason why a segment is considered stale."""

    NOT_STALE = "not_stale"
    OLD_TOOL_OUTPUT = "old_tool_output"  # tool output > N turns ago
    UNREFERENCED_MEMORY = "unreferenced_memory"  # memory not referenced recently
    SUPERSEDED_RETRIEVAL = "superseded_retrieval"  # retrieval chunk from edited file
    STALE_ASSISTANT_TURN = "stale_assistant_turn"  # old assistant response
    DUPLICATE_CONTENT = "duplicate_content"  # near-duplicate of another segment


# ---------------------------------------------------------------------------
# Phase 7E: Anti-Pattern Detection
# ---------------------------------------------------------------------------


class AntiPattern(str, Enum):
    """Common context-stuffing anti-patterns that waste tokens."""

    NONE = "none"
    REPEATED_SYSTEM_PROMPT = "repeated_system_prompt"  # system prompt appears >1x
    ECHO_REQUEST = "echo_request"  # tool output repeats user query
    VERBOSE_STRUCTURED = "verbose_structured"  # large JSON/XML blob (>500 tokens)
    REDUNDANT_INSTRUCTION = "redundant_instruction"  # same instruction in multiple segments
    BOILERPLATE_FILLER = "boilerplate_filler"  # "I'd be happy to help", "Sure!", etc.


# Anti-pattern relevance score penalties
ANTI_PATTERN_PENALTIES: dict[AntiPattern, float] = {
    AntiPattern.NONE: 0.0,
    AntiPattern.REPEATED_SYSTEM_PROMPT: -0.4,
    AntiPattern.ECHO_REQUEST: -0.3,
    AntiPattern.VERBOSE_STRUCTURED: -0.15,
    AntiPattern.REDUNDANT_INSTRUCTION: -0.35,
    AntiPattern.BOILERPLATE_FILLER: -0.5,
}


# ---------------------------------------------------------------------------
# Phase 4A canonical dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TelemetryEvent:
    """Top-level lifecycle event for a single LLM request/response cycle.

    Parameters
    ----------
    trace_id:
        Globally unique identifier for the full conversation trace.
    request_id:
        Identifier for this specific request within the trace.
    event_type:
        Lifecycle phase: ``"request_start"``, ``"request_end"``,
        ``"error"``, ``"cache_hit"``, ``"retry"``, …
    ts:
        Unix timestamp (float seconds) at which the event was recorded.
    provider:
        Lower-case provider name: ``"anthropic"``, ``"openai"``,
        ``"gemini"``, ``"unknown"``.
    model:
        Model identifier as reported by the provider.
    agent_id:
        Optional identifier for the agent / worker that issued the call.
    api:
        API endpoint used (e.g. ``"anthropic-messages"``, ``"openai-responses"``).
    stop_reason:
        Provider-reported stop reason (e.g. ``"end_turn"``, ``"max_tokens"``).
    session_id:
        Session identifier from which this event originated.
    duration_ms:
        Request duration in milliseconds.
    status:
        Outcome: ``"ok"``, ``"error"``, ``"timeout"``, ``"cancelled"``.
    error_class:
        Exception class name when ``status == "error"``; ``None`` otherwise.
    payload:
        Arbitrary JSON-serialisable dict for additional event metadata.
    """

    trace_id: str = ""
    request_id: str = ""
    event_type: str = ""
    ts: float = 0.0
    provider: str = ""
    model: str = ""
    agent_id: str = ""
    api: str = ""
    stop_reason: str = ""
    session_id: str = ""
    duration_ms: float = 0.0
    status: str = "ok"
    error_class: Optional[str] = None
    payload: dict[str, Any] = field(default_factory=dict)
    # PRD fields
    span_id: str = ""
    node_id: str = ""

    def payload_json(self) -> str:
        """Return :attr:`payload` serialised as a JSON string."""
        return json.dumps(self.payload, default=str)


@dataclass
class Segment:
    """Storage record for a classified message segment.

    Mirrors the fields produced by :func:`tokenpak.telemetry.segmentizer.segmentize`
    so that rows can be inserted directly after a segmentisation pass.

    Parameters
    ----------
    trace_id:
        Parent trace identifier.
    segment_id:
        Deterministic UUID5 derived from *trace_id* + *order*.
    order:
        Zero-based position in the original messages list.
    segment_type:
        Segment taxonomy value (see ``SegmentType`` in segmentizer).
    raw_hash:
        SHA-256 hex digest of the raw content string.
    final_hash:
        SHA-256 of post-compression content (empty until compression runs).
    raw_len:
        Character count of the raw content.
    final_len:
        Character count after compression (0 until compression runs).
    tokens_raw:
        Rough token estimate for the raw content.
    tokens_after_qmd:
        Tokens after the QMD compression pass.
    tokens_after_tp:
        Tokens after the TokenPak compression pass.
    actions:
        JSON string listing compression actions applied to this segment.
    """

    trace_id: str = ""
    segment_id: str = ""
    order: int = 0
    segment_type: str = ""
    raw_hash: str = ""
    final_hash: str = ""
    raw_len: int = 0
    final_len: int = 0
    tokens_raw: int = 0
    tokens_after_qmd: int = 0
    tokens_after_tp: int = 0
    actions: str = "[]"
    relevance_score: float = 0.5  # 0.0-1.0; default neutral
    # PRD fields
    segment_source: str = ""
    content_type: str = "text"
    raw_len_chars: int = 0
    raw_len_bytes: int = 0
    final_len_chars: int = 0
    final_len_bytes: int = 0
    debug_ref: Optional[str] = None


@dataclass
class Usage:
    """Token-usage provenance record written to persistent storage.

    Parameters
    ----------
    trace_id:
        Parent trace identifier.
    usage_source:
        How token counts were obtained (``"provider_reported"``,
        ``"proxy_estimate"``, ``"token_counted"``, ``"unknown"``).
    confidence:
        Reliability of the counts (``"high"``, ``"medium"``, ``"low"``).
    input_billed:
        Input tokens actually billed by the provider.
    output_billed:
        Output tokens actually billed.
    input_est:
        Estimated input tokens (non-zero when not provider-reported).
    output_est:
        Estimated output tokens.
    cache_read:
        Tokens served from the provider's prompt-cache (read hit).
    cache_write:
        Tokens written into the provider's prompt-cache.
    total_tokens:
        Total tokens reported by provider (sum of all token types).
    """

    trace_id: str = ""
    usage_source: str = "unknown"
    confidence: str = "low"
    input_billed: int = 0
    output_billed: int = 0
    input_est: int = 0
    output_est: int = 0
    cache_read: int = 0
    cache_write: int = 0
    total_tokens: int = 0
    # PRD fields
    total_tokens_billed: int = 0
    total_tokens_est: int = 0
    provider_usage_raw: str = "{}"


@dataclass
class Cost:
    """Cost computation result for a single LLM call.

    Parameters
    ----------
    trace_id:
        Parent trace identifier.
    cost_input:
        Provider-reported cost for input tokens (USD).
    cost_output:
        Provider-reported cost for output tokens (USD).
    cost_cache_read:
        Provider-reported cost for cache read tokens (USD).
    cost_cache_write:
        Provider-reported cost for cache write tokens (USD).
    cost_total:
        Total provider-reported cost (USD).
    cost_source:
        Source of cost data: ``"provider"``, ``"estimated"``, or ``"unknown"``.
    baseline_cost:
        What the call would have cost without compression (USD).
    savings_total:
        Total savings = ``baseline_cost - cost_total`` (USD).
    savings_qmd:
        Savings attributable to the QMD pass (USD).
    savings_tp:
        Savings attributable to the TokenPak compression pass (USD).
    """

    trace_id: str = ""
    # Provider-reported cost breakdown (new schema)
    cost_input: float = 0.0
    cost_output: float = 0.0
    cost_cache_read: float = 0.0
    cost_cache_write: float = 0.0
    cost_total: float = 0.0
    cost_source: str = "provider"
    # Baseline / savings (for compression proof)
    pricing_version: str = "v1"
    baseline_input_tokens: int = 0
    actual_input_tokens: int = 0
    output_tokens: int = 0
    baseline_cost: float = 0.0
    actual_cost: float = 0.0
    savings_total: float = 0.0
    savings_qmd: float = 0.0
    savings_tp: float = 0.0

    @property
    def savings_pct(self) -> float:
        """Percentage savings relative to baseline (0–100).  Returns 0 when
        baseline is zero to avoid division-by-zero."""
        if self.baseline_cost == 0.0:
            return 0.0
        return (self.savings_total / self.baseline_cost) * 100.0


# ---------------------------------------------------------------------------
# Phase 7C: Context Capsule
# ---------------------------------------------------------------------------


@dataclass
class ContextCapsule:
    """Structured wrapper for a compressed context payload.

    Produced by the Context Composer before prompt injection. Contains the
    final compressed content plus metadata about budget usage, segment
    inclusion/exclusion, compression stats, and provenance.
    """

    capsule_id: str = ""
    session_id: str = ""
    agent_id: str = ""
    created_at: str = ""
    budget_tokens: int = 0
    actual_tokens: int = 0
    headroom_tokens: int = 0
    segments_included: list[str] = field(default_factory=list)
    segments_dropped: list[str] = field(default_factory=list)
    drop_reason: dict[str, str] = field(default_factory=dict)
    raw_tokens_before: int = 0
    compression_ratio: float = 0.0
    savings_pct: float = 0.0
    provenance: list[str] = field(default_factory=list)
    retrieval_chunks: list[str] = field(default_factory=list)
    coverage_score: float = 0.0
    content: str = ""

    @property
    def is_over_budget(self) -> bool:
        """Return True if actual_tokens exceeds budget_tokens."""
        return self.actual_tokens > self.budget_tokens

    @property
    def efficiency_score(self) -> float:
        """Budget utilization efficiency (0-1).

        Returns 1.0 if we exactly hit the budget, <1.0 if under-budget.
        Capped at 1.0 even if over-budget (no penalty for exceeding).
        """
        if self.budget_tokens == 0:
            return 0.0
        return min(1.0, self.actual_tokens / self.budget_tokens)
