# SPDX-License-Identifier: Apache-2.0
"""Legacy compatibility exports for TokenPak's public contracts.

All contract definitions live in :mod:`tokenpak.core.contracts`. This package
preserves the former :mod:`tokenpak.tip` import paths by re-exporting those
canonical objects; the legacy and canonical imports remain object-identical.
New code should import definitions from their specific
``tokenpak.core.contracts`` modules instead of this compatibility surface.

Legacy exported surface:

    capabilities        - TIP optimization capability label constants
    route_contract      - OptimizationRouteClass enum (request semantic type)
    fidelity_contract   - FidelityPolicy enum (content preservation policy)
    cache_contract      - CachePolicy dataclass + CacheMissReason constants
    compression_contract- CompressionPolicy dataclass + ProtectedSpanType
    telemetry_contract  - TelemetryPolicy dataclass + SavingsSource constants
    trace_contract      - OptimizationTrace + constituent dataclasses
    optimization_contract - OptimizationContract (top-level per-request contract)
"""

from __future__ import annotations

from tokenpak.core.contracts.cache import CacheMissReason, CachePolicy
from tokenpak.core.contracts.capabilities import (
    ALL_OPTIMIZATION_CAPABILITIES,
    MULTIPAK_CAPABILITIES,
    TIP_CACHE_PROMPT_KEY_PRESERVED,
    TIP_CACHE_PROVIDER_AWARE,
    TIP_CACHE_PROXY_MANAGED,
    TIP_CACHE_SEMANTIC_V1,
    TIP_CACHE_TTL_ORDERING,
    TIP_CAPSULES_V1,
    TIP_COMPRESSION_V1,
    TIP_CONTEXT_COVERAGE,
    TIP_CONTEXT_HANDOFF,
    TIP_CONTEXT_PACKAGE,
    TIP_CONTEXT_POLICY,
    TIP_CONTEXT_RESUME,
    TIP_FIDELITY_POLICY_V1,
    TIP_INTENT_CLASSIFICATION_V1,
    TIP_INTENT_SUGGESTION_V1,
    TIP_PAK_CAPTURE,
    TIP_PAK_HYDRATE,
    TIP_PAK_INDEX,
    TIP_PAK_PROMOTE,
    TIP_PAK_RECALL,
    TIP_ROUTE_CLASS_V1,
    TIP_TELEMETRY_ATTRIBUTION_V1,
    TIP_TOOL_SCHEMA_STABILITY_V1,
)
from tokenpak.core.contracts.compression import CompressionPolicy, ProtectedSpanType
from tokenpak.core.contracts.context import (
    AnchorBlockPosition,
    ContextLevel,
    ContextPackage,
    ContextScope,
    CoverageConfidence,
    CoverageReport,
    CoverageState,
    OrderingHints,
    PolicyDecision,
    context_level_label,
    parse_context_level,
)
from tokenpak.core.contracts.fidelity import FidelityPolicy
from tokenpak.core.contracts.optimization import OptimizationContract
from tokenpak.core.contracts.pak import (
    Pak,
    PakAnchor,
    PakAuthority,
    PakConfidence,
    PakPrivacy,
    PakPrivacyClass,
    PakRelationships,
    PakRetention,
    PakRetentionPolicy,
    PakScope,
    PakSource,
    PakSourceType,
    PakStatus,
    PakSubtype,
    all_subtypes,
    default_retention_for,
)
from tokenpak.core.contracts.route import OptimizationRouteClass
from tokenpak.core.contracts.telemetry import SavingsSource, TelemetryPolicy
from tokenpak.core.contracts.trace import (
    CacheTrace,
    CompressionTrace,
    OptimizationTrace,
    Recommendation,
    SavingsAttribution,
    StageTrace,
)

__all__ = [
    # Capability constants
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
    "ALL_OPTIMIZATION_CAPABILITIES",
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
    # Enums
    "OptimizationRouteClass",
    "FidelityPolicy",
    "CacheMissReason",
    "ProtectedSpanType",
    "SavingsSource",
    # MultiPak enums:
    "PakSubtype",
    "PakAuthority",
    "PakStatus",
    "PakConfidence",
    "PakRetention",
    "PakSourceType",
    "PakPrivacyClass",
    "ContextLevel",
    "CoverageState",
    "CoverageConfidence",
    "AnchorBlockPosition",
    # Dataclasses
    "CachePolicy",
    "CompressionPolicy",
    "TelemetryPolicy",
    "OptimizationContract",
    "StageTrace",
    "SavingsAttribution",
    "CacheTrace",
    "CompressionTrace",
    "Recommendation",
    "OptimizationTrace",
    # MultiPak dataclasses:
    "Pak",
    "PakAnchor",
    "PakPrivacy",
    "PakRelationships",
    "PakRetentionPolicy",
    "PakScope",
    "PakSource",
    "ContextPackage",
    "ContextScope",
    "CoverageReport",
    "OrderingHints",
    "PolicyDecision",
    # MultiPak helpers:
    "all_subtypes",
    "default_retention_for",
    "context_level_label",
    "parse_context_level",
]
