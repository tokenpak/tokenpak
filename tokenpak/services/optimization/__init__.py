"""Generic optimization pipeline.

Observe-only scaffolding for the optimization pipeline described in the
optimization-layer design (Phase 3 Component B, Phase 4
Milestone 2). Pipeline composition lives under ``services/``, the
only place the compression → security → cache → routing → telemetry
→ dispatch sequence exists; ``proxy/server.py`` invokes
``run_observe_only`` over the byte-preserved request.

Public surface:

    OptimizationContext   — per-request state passed through the pipeline
    OptimizationStage     — Protocol every stage must satisfy
    EligibilityResult     — eligible / not-eligible + skip_reason
    StageRegistry         — register and look up stages by name
    OptimizationPipeline  — runs registered stages in observe-only mode
    OptimizationTrace     — collected trace of stage decisions
    StageTrace            — per-stage trace entry
    build_contract        — services-layer contract builder (consumes the upstream contract if present)
    is_pipeline_enabled   — read TOKENPAK_OPTIMIZATION_PIPELINE flag

The default mode is observe-only. Stages declare eligibility but the pipeline
NEVER invokes ``stage.apply()`` in this module; the request body is treated
as immutable. Mutation belongs to follow-up tasks and lives
behind a separate flag.
"""

from .attribution_stage import (
    AttributionStage,
    get_attributions,
    is_attribution_v2_enabled,
)
from .cache_stage import SemanticCacheStage, get_cached_response
from .cache_trace import CacheMissReason, CacheStageTrace
from .compression_stage import (
    RouteClassCompressionStage,
)
from .compression_stage import (
    is_stage_enabled as is_route_compression_enabled,
)
from .compression_stage import (
    register_with_default_pipeline as register_route_compression_stage,
)
from .context import OptimizationContext
from .contract_builder import build_contract
from .pipeline import OptimizationPipeline, run_observe_only
from .policies import is_pipeline_enabled
from .protected_spans import (
    ProtectedSpan,
    SpanType,
    detect_protected_spans,
    rewrite_outside_spans,
)
from .registry import StageRegistry
from .route_recipe_policy import (
    DEFAULT_POLICIES,
    FidelityTier,
    RouteClass,
    RoutePolicy,
    apply_policy,
    get_route_policy,
    select_recipes,
)
from .stage import EligibilityResult, OptimizationStage
from .telemetry_sink import TelemetrySink
from .trace import OptimizationTrace, StageTrace

__all__ = [
    "OptimizationContext",
    "OptimizationPipeline",
    "OptimizationStage",
    "EligibilityResult",
    "StageRegistry",
    "OptimizationTrace",
    "StageTrace",
    "build_contract",
    "is_pipeline_enabled",
    "run_observe_only",
    # route-class compression policy
    "RouteClass",
    "FidelityTier",
    "RoutePolicy",
    "DEFAULT_POLICIES",
    "get_route_policy",
    "select_recipes",
    "apply_policy",
    "SpanType",
    "ProtectedSpan",
    "detect_protected_spans",
    "rewrite_outside_spans",
    "RouteClassCompressionStage",
    "is_route_compression_enabled",
    "register_route_compression_stage",
    # semantic cache stage
    "SemanticCacheStage",
    "get_cached_response",
    "CacheMissReason",
    "CacheStageTrace",
    # savings attribution + telemetry sink
    "AttributionStage",
    "is_attribution_v2_enabled",
    "get_attributions",
    "TelemetrySink",
]
