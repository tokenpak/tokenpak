# SPDX-License-Identifier: Apache-2.0
"""TIP optimization capability label constants.

These constants are the canonical string identifiers for TIP optimization
capabilities that adapters can declare and proxy stages can consume.

Usage — adapter declares supported capabilities::

    from tokenpak.tip.capabilities import (
        TIP_COMPRESSION_V1,
        TIP_CACHE_PROXY_MANAGED,
        TIP_TELEMETRY_ATTRIBUTION_V1,
    )

    class MyAdapter(FormatAdapter):
        capabilities = frozenset({
            TIP_COMPRESSION_V1,
            TIP_CACHE_PROXY_MANAGED,
            TIP_TELEMETRY_ATTRIBUTION_V1,
        })

Usage — proxy stage checks capability gate::

    if TIP_CACHE_SEMANTIC_V1 in contract.capabilities:
        # apply semantic cache stage
        ...

Relationship to core.contracts.capabilities
-------------------------------------------
``tokenpak.core.contracts.capabilities`` declares what *TokenPak-as-a-product*
self-publishes via MCP / TIP conformance headers. This module defines the
full optimization capability vocabulary that *any* adapter can declare.
The two sets overlap (e.g. ``tip.compression.v1`` appears in both) but have
different purposes: the core module is about self-declaration; this module
is the authoritative vocabulary for capability-gated proxy stages.

Label format: ``tip.<group>.<feature>[.<version>]``
External/vendor capabilities use: ``ext.<vendor>.<feature>``
"""

from __future__ import annotations

# --- Compression ---
TIP_COMPRESSION_V1: str = "tip.compression.v1"
"""Adapter supports body compression via tokenpak recipes."""

# --- Cache ---
TIP_CACHE_PROXY_MANAGED: str = "tip.cache.proxy-managed"
"""Adapter supports tokenpak-managed caching (semantic + TTL)."""

TIP_CACHE_PROVIDER_AWARE: str = "tip.cache.provider-aware"
"""Adapter is aware of and preserves provider-side cache semantics."""

TIP_CACHE_PROMPT_KEY_PRESERVED: str = "tip.cache.prompt-key-preserved"
"""Adapter preserves provider prompt cache key through normalization."""

TIP_CACHE_TTL_ORDERING: str = "tip.cache.ttl-ordering"
"""Adapter requires TTL-ordered cache_control blocks (Anthropic billing rule)."""

TIP_CACHE_SEMANTIC_V1: str = "tip.cache.semantic.v1"
"""Adapter supports tokenpak semantic cache lookup and recording."""

# --- Routing ---
TIP_ROUTE_CLASS_V1: str = "tip.route.class.v1"
"""Adapter exposes request content type for route-class-based policy."""

# --- Fidelity ---
TIP_FIDELITY_POLICY_V1: str = "tip.fidelity.policy.v1"
"""Adapter declares content preservation requirements (lossless, semantic-safe, etc.)."""

# --- Telemetry ---
TIP_TELEMETRY_ATTRIBUTION_V1: str = "tip.telemetry.attribution.v1"
"""Adapter supports per-source savings attribution in telemetry."""

# --- Intent ---
TIP_INTENT_CLASSIFICATION_V1: str = "tip.intent.classification.v1"
"""Adapter supports intent classification for route policy decisions."""

TIP_INTENT_SUGGESTION_V1: str = "tip.intent.suggestion.v1"
"""Adapter supports intent-driven suggestions (soft interventions)."""

# --- Tool schema ---
TIP_TOOL_SCHEMA_STABILITY_V1: str = "tip.tool-schema.stability.v1"
"""Adapter normalizes tool schemas to produce stable cache digests."""

# --- Capsules ---
TIP_CAPSULES_V1: str = "tip.capsules.v1"
"""Adapter supports session capsule injection and context compaction.

Note: ``capsule`` is the legacy term superseded by ``Pak`` per the glossary
(ratified 2026-05-06). This capability label is preserved for backward
compatibility; new Pak-related capabilities use the ``tip.pak.*`` and
``tip.context.*`` prefixes (see MultiPak block below)."""

# --- MultiPak (ratified 2026-05-07) ---
# Cross-platform AI context continuity. Pak capture, recall, packaging,
# handoff, and anchor hydration. OSS-side schema + capability declarations;
# the engines that consume them live in the tokenpak-paid daemon.
# These are fully additive within TIP-1.x — no version bump.

TIP_PAK_CAPTURE: str = "tip.pak.capture"
"""Component can create a Pak from a source (file, LLM response, tool output)."""

TIP_PAK_INDEX: str = "tip.pak.index"
"""Component indexes Paks for retrieval (FTS, optionally embeddings)."""

TIP_PAK_RECALL: str = "tip.pak.recall"
"""Component retrieves Paks matching a query within scope, ranked."""

TIP_PAK_HYDRATE: str = "tip.pak.hydrate"
"""Component restores exact source snippets (anchors) for a Pak when summaries
are insufficient. Hydration events are policy-gated and audit-logged."""

TIP_PAK_PROMOTE: str = "tip.pak.promote"
"""Component can promote an Interaction Pak to Decision authority."""

TIP_CONTEXT_PACKAGE: str = "tip.context.package"
"""Component builds a Context Package from selected Paks for the current request."""

TIP_CONTEXT_HANDOFF: str = "tip.context.handoff"
"""Component builds a target-specific Handoff Pak for another tool/platform."""

TIP_CONTEXT_RESUME: str = "tip.context.resume"
"""Component continues a workflow from a prior Handoff Pak."""

TIP_CONTEXT_COVERAGE: str = "tip.context.coverage"
"""Component scores Context Coverage (complete / partial / low_confidence /
missing_required_context / blocked_by_policy / not_found) on every package."""

TIP_CONTEXT_POLICY: str = "tip.context.policy"
"""Component evaluates privacy / scope / budget policy on a candidate package
before it is delivered. Cross-project leakage gate, sensitive-Pak block list,
hydration budget all enforced here."""

# Convenience set for adapters that implement the full MultiPak surface.
MULTIPAK_CAPABILITIES: frozenset[str] = frozenset(
    {
        TIP_PAK_CAPTURE,
        TIP_PAK_INDEX,
        TIP_PAK_RECALL,
        TIP_PAK_HYDRATE,
        TIP_PAK_PROMOTE,
        TIP_CONTEXT_PACKAGE,
        TIP_CONTEXT_HANDOFF,
        TIP_CONTEXT_RESUME,
        TIP_CONTEXT_COVERAGE,
        TIP_CONTEXT_POLICY,
    }
)

# --- Aggregated set of all optimization vocabulary labels ---
ALL_OPTIMIZATION_CAPABILITIES: frozenset[str] = frozenset(
    {
        TIP_COMPRESSION_V1,
        TIP_CACHE_PROXY_MANAGED,
        TIP_CACHE_PROVIDER_AWARE,
        TIP_CACHE_PROMPT_KEY_PRESERVED,
        TIP_CACHE_TTL_ORDERING,
        TIP_CACHE_SEMANTIC_V1,
        TIP_ROUTE_CLASS_V1,
        TIP_FIDELITY_POLICY_V1,
        TIP_TELEMETRY_ATTRIBUTION_V1,
        TIP_INTENT_CLASSIFICATION_V1,
        TIP_INTENT_SUGGESTION_V1,
        TIP_TOOL_SCHEMA_STABILITY_V1,
        TIP_CAPSULES_V1,
        *MULTIPAK_CAPABILITIES,
    }
)

__all__ = [
    "TIP_COMPRESSION_V1",
    "TIP_CACHE_PROXY_MANAGED",
    "TIP_CACHE_PROVIDER_AWARE",
    "TIP_CACHE_PROMPT_KEY_PRESERVED",
    "TIP_CACHE_TTL_ORDERING",
    "TIP_CACHE_SEMANTIC_V1",
    "TIP_ROUTE_CLASS_V1",
    "TIP_FIDELITY_POLICY_V1",
    "TIP_TELEMETRY_ATTRIBUTION_V1",
    "TIP_INTENT_CLASSIFICATION_V1",
    "TIP_INTENT_SUGGESTION_V1",
    "TIP_TOOL_SCHEMA_STABILITY_V1",
    "TIP_CAPSULES_V1",
    # MultiPak:
    "TIP_PAK_CAPTURE",
    "TIP_PAK_INDEX",
    "TIP_PAK_RECALL",
    "TIP_PAK_HYDRATE",
    "TIP_PAK_PROMOTE",
    "TIP_CONTEXT_PACKAGE",
    "TIP_CONTEXT_HANDOFF",
    "TIP_CONTEXT_RESUME",
    "TIP_CONTEXT_COVERAGE",
    "TIP_CONTEXT_POLICY",
    "MULTIPAK_CAPABILITIES",
    "ALL_OPTIMIZATION_CAPABILITIES",
]
