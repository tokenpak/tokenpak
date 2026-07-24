# SPDX-License-Identifier: Apache-2.0
"""TIP optimization trace — per-request observability record.

``OptimizationTrace`` is the top-level record emitted for every request
that passes through the optimization pipeline. It aggregates per-stage
decisions, savings, cache behavior, compression behavior, and recommendations.

Design constraints:
- No raw prompt content — all text references are hashes or digests.
- Every stage emits a ``StageTrace`` whether applied or skipped; silence
  is not acceptable for audit/recommendation purposes.
- ``SavingsAttribution`` follows ``SavingsSource`` vocabulary strictly:
  provider/platform savings are never credited to TokenPak.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass
class StageTrace:
    """Record of one optimization stage's decision for a request.

    Fields:
    - ``name``: stage identifier (e.g. ``"semantic_cache"``, ``"compression"``).
    - ``applied``: True if the stage mutated the request or served from cache.
    - ``skip_reason``: populated when ``applied=False`` (required if skipped).
    - ``tokens_before``: input token count before this stage (if measurable).
    - ``tokens_after``: output token count after this stage (if measurable).
    - ``latency_ms``: wall-clock time for this stage in milliseconds.
    - ``metadata``: stage-specific detail (cache hit score, recipe used, etc.).
    """

    name: str
    applied: bool
    skip_reason: Optional[str] = None
    tokens_before: Optional[int] = None
    tokens_after: Optional[int] = None
    latency_ms: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def tokens_saved(self) -> Optional[int]:
        if self.tokens_before is not None and self.tokens_after is not None:
            return max(0, self.tokens_before - self.tokens_after)
        return None


@dataclass
class SavingsAttribution:
    """Per-source token savings record for one request.

    Sources follow ``SavingsSource`` vocabulary. Multiple records may exist
    for one request (one per contributing source).
    """

    source: str
    raw_tokens: int = 0
    sent_tokens: int = 0
    saved_tokens: int = 0
    estimated_cost_saved: Optional[float] = None
    credited_to_tokenpak: bool = False
    cost_available: bool = False
    notes: Optional[str] = None

    def __post_init__(self) -> None:
        from tokenpak.tip.telemetry_contract import SavingsSource

        if self.source not in SavingsSource.ALL:
            raise ValueError(
                f"Unknown savings source {self.source!r}. Use a SavingsSource constant."
            )
        self.credited_to_tokenpak = self.source in SavingsSource.TOKENPAK_MANAGED


@dataclass
class CacheTrace:
    """Cache behavior summary for one request."""

    lookup_attempted: bool = False
    hit: bool = False
    scope: Optional[str] = None
    miss_reason: Optional[str] = None
    similarity_score: Optional[float] = None
    cache_key_digest: Optional[str] = None
    provider_cache_hit: bool = False
    provider_prompt_cache_key: Optional[str] = None


@dataclass
class CompressionTrace:
    """Compression behavior summary for one request."""

    attempted: bool = False
    applied: bool = False
    recipe_ids: List[str] = field(default_factory=list)
    tokens_before: Optional[int] = None
    tokens_after: Optional[int] = None
    bypass_reason: Optional[str] = None
    protected_spans_detected: int = 0

    def compression_ratio(self) -> Optional[float]:
        if self.tokens_before and self.tokens_after and self.tokens_before > 0:
            return self.tokens_after / self.tokens_before
        return None


@dataclass
class Recommendation:
    """A single actionable recommendation derived from telemetry.

    Recommendations are ranked by ``impact`` (high/medium/low) and must
    reference the telemetry signal that triggered them.
    """

    impact: str
    title: str
    detail: str
    action: Optional[str] = None
    related_source: Optional[str] = None


@dataclass
class OptimizationTrace:
    """Top-level per-request optimization trace.

    Produced by every request through the optimization pipeline (Component B).
    Stored in the ``optimization_traces`` telemetry table.
    """

    request_id: str
    model: str
    platform: Optional[str] = None
    adapter_format: str = "unknown"
    route_class: str = "unknown"
    fidelity_tier: str = "unknown"

    stages: List[StageTrace] = field(default_factory=list)
    savings: List[SavingsAttribution] = field(default_factory=list)
    cache: CacheTrace = field(default_factory=CacheTrace)
    compression: CompressionTrace = field(default_factory=CompressionTrace)
    recommendations: List[Recommendation] = field(default_factory=list)

    total_raw_tokens: int = 0
    total_sent_tokens: int = 0
    total_saved_tokens: int = 0
    status: str = "ok"

    def tokenpak_saved_tokens(self) -> int:
        """Total tokens saved by TokenPak-managed stages only."""
        from tokenpak.tip.telemetry_contract import SavingsSource

        return sum(
            s.saved_tokens for s in self.savings if s.source in SavingsSource.TOKENPAK_MANAGED
        )

    def add_stage(self, stage: StageTrace) -> None:
        self.stages.append(stage)

    def add_savings(self, attribution: SavingsAttribution) -> None:
        self.savings.append(attribution)
        if attribution.credited_to_tokenpak:
            self.total_saved_tokens += attribution.saved_tokens


__all__ = [
    "StageTrace",
    "SavingsAttribution",
    "CacheTrace",
    "CompressionTrace",
    "Recommendation",
    "OptimizationTrace",
]
