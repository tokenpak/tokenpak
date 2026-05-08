# SPDX-License-Identifier: Apache-2.0
"""TIP Optimization Contracts — protocol-level vocabulary for the optimization layer.

This package defines the shared types, constants, and contracts that
sit at the TIP/Protocol layer of the optimization architecture:

    TIP/Protocol layer  (this package)
         ↓
    Proxy layer         (tokenpak/proxy/optimization/)
         ↓
    Adapter layer       (tokenpak/proxy/adapters/, tokenpak/agent/adapters/)

Nothing in this package should import from proxy or adapter modules.
Downstream consumers import from here; this package imports only from
tokenpak.core (for version/header contracts) and the standard library.

Exported surface:

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

from tokenpak.tip.capabilities import (
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
    ALL_OPTIMIZATION_CAPABILITIES,
)
from tokenpak.tip.cache_contract import CacheMissReason, CachePolicy
from tokenpak.tip.compression_contract import CompressionPolicy, ProtectedSpanType
from tokenpak.tip.context_package import (
    ContextLevel,
    ContextPackage,
    ContextScope,
    CoverageConfidence,
    CoverageReport,
    CoverageState,
    PolicyDecision,
    context_level_label,
    parse_context_level,
)
from tokenpak.tip.fidelity_contract import FidelityPolicy
from tokenpak.tip.optimization_contract import OptimizationContract
from tokenpak.tip.pak import (
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
from tokenpak.tip.route_contract import OptimizationRouteClass
from tokenpak.tip.telemetry_contract import SavingsSource, TelemetryPolicy
from tokenpak.tip.trace_contract import (
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
    # MultiPak (Std 32):
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
    "PolicyDecision",
    # MultiPak helpers:
    "all_subtypes",
    "default_retention_for",
    "context_level_label",
    "parse_context_level",
]
