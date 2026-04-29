# SPDX-License-Identifier: Apache-2.0
"""TIP telemetry contract — savings attribution policy and source vocabulary.

``TelemetryPolicy`` describes how the optimization pipeline should record
and attribute savings. ``SavingsSource`` provides the canonical vocabulary
for savings attribution — required to prevent overclaiming TokenPak-managed
savings when provider/platform cache was responsible.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TelemetryPolicy:
    """Per-request telemetry and attribution behavior contract.

    Fields:
    - ``enabled``: master switch; when False, no telemetry is emitted.
    - ``hash_prompts``: when True (default), prompt content is hashed
      before storage. Raw prompt storage requires explicit opt-in.
    - ``attribute_savings``: when True, per-source savings are computed
      and stored in ``savings_attribution`` telemetry table.
    - ``emit_trace``: when True, full ``OptimizationTrace`` is persisted
      (may be large; gated separately from lightweight savings records).
    - ``session_id``: opaque session identifier for scoping telemetry rows.
      None means telemetry is unscoped (allowed but limits recommendations).
    """

    enabled: bool = True
    hash_prompts: bool = True
    attribute_savings: bool = True
    emit_trace: bool = False
    session_id: str | None = None

    def is_active(self) -> bool:
        return self.enabled


class SavingsSource:
    """Canonical savings attribution source vocabulary.

    Used in ``SavingsAttribution.source`` and in the ``savings_attribution``
    telemetry table. Values must be stable — downstream dashboards and
    recommendation rules key off them.

    Attribution rules:
    - Provider/platform cache hits MUST be recorded as PROVIDER_PROMPT_CACHE
      or PLATFORM_CACHE, never as TokenPak-managed savings.
    - TokenPak-managed savings MUST have a corresponding stage action
      (semantic cache hit, compression delta, capsule reduction, etc.).
    - Savings that cannot be attributed MUST use UNATTRIBUTED rather than
      being optimistically assigned to a TokenPak source.
    - Cost estimates require model pricing; if price is unavailable, report
      tokens only and mark cost_available=False in the trace.
    """

    PROVIDER_PROMPT_CACHE = "provider_prompt_cache"
    """Prompt cache hit on the provider side (e.g. Anthropic cache_control)."""

    PLATFORM_CACHE = "platform_cache"
    """Platform-level cache hit (e.g. OpenAI Responses prompt_cache_key)."""

    TOKENPAK_SEMANTIC_CACHE = "tokenpak_semantic_cache"
    """TokenPak-managed semantic cache hit (session/agent/workspace scope)."""

    TOKENPAK_CAPSULES = "tokenpak_capsules"
    """Savings from capsule compression/injection by the capsule stage."""

    TOKENPAK_COMPRESSION = "tokenpak_compression"
    """Savings from recipe-based body compression by the compression stage."""

    TOKENPAK_TOOL_SCHEMA_STABILITY = "tokenpak_tool_schema_stability"
    """Savings from tool schema normalization (stable digest → cache hit)."""

    TOKENPAK_RETRIEVAL_PRUNING = "tokenpak_retrieval_pruning"
    """Savings from pruning low-relevance retrieved context fragments."""

    UNATTRIBUTED = "unattributed"
    """Token delta observed but source cannot be determined."""

    ALL: frozenset[str] = frozenset({
        PROVIDER_PROMPT_CACHE,
        PLATFORM_CACHE,
        TOKENPAK_SEMANTIC_CACHE,
        TOKENPAK_CAPSULES,
        TOKENPAK_COMPRESSION,
        TOKENPAK_TOOL_SCHEMA_STABILITY,
        TOKENPAK_RETRIEVAL_PRUNING,
        UNATTRIBUTED,
    })

    TOKENPAK_MANAGED: frozenset[str] = frozenset({
        TOKENPAK_SEMANTIC_CACHE,
        TOKENPAK_CAPSULES,
        TOKENPAK_COMPRESSION,
        TOKENPAK_TOOL_SCHEMA_STABILITY,
        TOKENPAK_RETRIEVAL_PRUNING,
    })
    """Subset of sources credited to TokenPak (not provider/platform)."""


__all__ = ["TelemetryPolicy", "SavingsSource"]
