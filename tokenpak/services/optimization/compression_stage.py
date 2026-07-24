"""Route-class compression OptimizationStage.

This is the proxy-pipeline-facing wrapper around ``route_recipe_policy``.
The stage:

1. Reads ``ctx.route`` and ``ctx.contract`` to decide eligibility.
2. Skips early when its feature flag is off, the route is unknown, the
   adapter doesn't declare ``tip.compression.v1``, or the contract's
   fidelity tier disallows compression.
3. When invoked via ``apply()`` (NOT in observe-only mode — the
   observe-only pipeline never calls it there), executes ``apply_policy()``
   over the raw body and writes savings into the trace's StageTrace.detail.

The default observe-only pipeline only calls ``eligible(ctx)``; the body
is therefore byte-preserved by construction. ``apply()`` is exposed for
tests and for the future enable-mutation milestone, gated behind
``TOKENPAK_ROUTE_COMPRESSION_STAGE`` plus the upstream pipeline-mode flag.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional

from .context import OptimizationContext
from .route_recipe_policy import (
    CompressionResult,
    FidelityTier,
    RouteClass,
    RoutePolicy,
    apply_policy,
    get_route_policy,
)
from .stage import EligibilityResult
from .trace import StageTrace

log = logging.getLogger(__name__)


ENV_FLAG = "TOKENPAK_ROUTE_COMPRESSION_STAGE"
TIP_COMPRESSION_V1 = "tip.compression.v1"

_TRUTHY = {"1", "on", "true", "yes", "observe", "apply"}


def is_stage_enabled(env: Optional[Dict[str, str]] = None) -> bool:
    """True when the route compression stage flag is set to a truthy value."""
    source = env if env is not None else os.environ
    raw = (source.get(ENV_FLAG, "") or "").strip().lower()
    return raw in _TRUTHY


@dataclass
class RouteClassCompressionStage:
    """OptimizationStage that maps route class to safe compression recipes.

    name:                 stable identifier used in trace
    required_capabilities: stage requests adapters declare TIP_COMPRESSION_V1
    env:                  optional env dict (test injection); defaults to
                          ``os.environ`` at call time

    Eligibility rules (in order, first match wins):

    1. flag-off:               TOKENPAK_ROUTE_COMPRESSION_STAGE not truthy
    2. route-unknown:          ctx.route is None / "" / "unknown"
    3. capability-missing:     contract declares capabilities but not
                               ``tip.compression.v1`` (graceful unknown:
                               an *empty* capability set is allowed,
                               matching the proposal's "graceful unknowns")
    4. fidelity-no-optimize:   policy.fidelity == "no_optimize"
    5. no-recipes-for-route:   policy.recipe_names is empty
    6. eligible=True:          would-apply detail records the recipe names
    """

    name: str = "route-class-compression"
    required_capabilities: FrozenSet[str] = field(
        default_factory=lambda: frozenset({TIP_COMPRESSION_V1})
    )
    env: Optional[Dict[str, str]] = None

    def _read_env(self) -> Dict[str, str]:
        return self.env if self.env is not None else dict(os.environ)

    # ---- eligibility -----------------------------------------------------

    def eligible(self, ctx: OptimizationContext) -> EligibilityResult:
        env = self._read_env()
        if not is_stage_enabled(env):
            return EligibilityResult(
                eligible=False,
                skip_reason="flag-off",
                detail=f"{ENV_FLAG} not set",
            )

        route = ctx.route or RouteClass.UNKNOWN
        if route in ("", RouteClass.UNKNOWN, None):
            return EligibilityResult(eligible=False, skip_reason="route-unknown")

        contract = ctx.contract
        if contract is not None and hasattr(contract, "capabilities"):
            caps = getattr(contract, "capabilities", None) or frozenset()
            if caps and TIP_COMPRESSION_V1 not in caps:
                return EligibilityResult(
                    eligible=False,
                    skip_reason="capability-missing",
                    detail=f"missing {TIP_COMPRESSION_V1}",
                )

        policy = self._policy_for(ctx, route)
        if policy.fidelity == FidelityTier.NO_OPTIMIZE:
            return EligibilityResult(
                eligible=False,
                skip_reason="fidelity-no-optimize",
                detail=f"fidelity={policy.fidelity}",
            )
        if not policy.recipe_names:
            return EligibilityResult(
                eligible=False,
                skip_reason="no-recipes-for-route",
                detail=f"route={route}",
            )

        return EligibilityResult(
            eligible=True,
            detail=f"would-apply={','.join(policy.recipe_names)};fidelity={policy.fidelity}",
        )

    # ---- mutation (only called outside observe-only mode) ----------------

    def apply(self, ctx: OptimizationContext) -> OptimizationContext:
        """Compress ``ctx.raw_body`` in place per the route policy.

        The observe-only pipeline NEVER invokes this. Tests call it
        directly to verify protected-span preservation; future mutation-mode
        sites must guard with their own flag in addition to the stage's
        ``TOKENPAK_ROUTE_COMPRESSION_STAGE`` gate.

        On any failure the original body is restored — protected-span
        invariants are stronger than savings.
        """
        env = self._read_env()
        if not is_stage_enabled(env):
            self._record_skip(ctx, "flag-off")
            return ctx

        route = ctx.route or RouteClass.UNKNOWN
        if route in ("", RouteClass.UNKNOWN):
            self._record_skip(ctx, "route-unknown")
            return ctx

        policy = self._policy_for(ctx, route)
        if policy.fidelity == FidelityTier.NO_OPTIMIZE:
            self._record_skip(ctx, "fidelity-no-optimize")
            return ctx

        original_body = ctx.raw_body
        try:
            text = original_body.decode("utf-8")
        except UnicodeDecodeError:
            self._record_skip(ctx, "non-utf8-body")
            return ctx

        try:
            result = apply_policy(text, policy=policy)
        except Exception as exc:  # pragma: no cover - defensive
            log.warning("route-class compression failed: %s", exc)
            self._record_skip(ctx, f"apply-error:{type(exc).__name__}")
            return ctx

        if not result.applied:
            self._record_skip(ctx, result.skipped_reason or "no-op")
            return ctx

        new_body = result.text.encode("utf-8")
        if len(new_body) >= len(original_body):
            # No real savings — keep the original. Do not mutate when we
            # would only inflate or break determinism.
            self._record_skip(ctx, "no-savings")
            return ctx

        ctx.raw_body = new_body
        self._record_applied(ctx, result, policy)
        return ctx

    # ---- helpers ---------------------------------------------------------

    def _policy_for(self, ctx: OptimizationContext, route: str) -> RoutePolicy:
        # Allow the contract to override the default policy through extras.
        contract = ctx.contract
        if contract is not None and hasattr(contract, "extras"):
            extras = getattr(contract, "extras", {}) or {}
            override = extras.get("route_policy")
            if isinstance(override, RoutePolicy):
                return override
        return get_route_policy(route)

    def _record_skip(self, ctx: OptimizationContext, reason: str) -> None:
        ctx.trace.add_stage(
            StageTrace(
                name=self.name,
                eligible=False,
                skip_reason=reason,
                applied=False,
                duration_ms=0.0,
                detail="",
            )
        )

    def _record_applied(
        self,
        ctx: OptimizationContext,
        result: CompressionResult,
        policy: RoutePolicy,
    ) -> None:
        detail_payload: Dict[str, Any] = {
            "bytes_in": result.bytes_in,
            "bytes_out": result.bytes_out,
            "bytes_saved": result.bytes_saved,
            "ratio": round(result.ratio, 4),
            "recipes": list(result.recipes_applied),
            "spans_preserved": result.spans_preserved,
            "fidelity": policy.fidelity,
            "route": policy.route_class,
        }
        ctx.trace.add_stage(
            StageTrace(
                name=self.name,
                eligible=True,
                skip_reason="",
                applied=True,
                duration_ms=0.0,
                detail=json.dumps(detail_payload, sort_keys=True),
            )
        )


# ---------------------------------------------------------------------------
# Pipeline registration helper (opt-in)
# ---------------------------------------------------------------------------


def register_with_default_pipeline(
    *,
    pipeline: Any = None,
    env: Optional[Dict[str, str]] = None,
    force: bool = False,
) -> Optional[RouteClassCompressionStage]:
    """Register the stage with a pipeline if the feature flag is on.

    Returns the registered stage instance, or ``None`` when the flag is
    off (and ``force`` is False). Callers SHOULD use the proxy-side
    integration that invokes this once at process start. Tests pass an
    explicit ``pipeline``; in production, ``None`` registers with the
    module-level default pipeline.
    """
    src_env = env if env is not None else dict(os.environ)
    if not (force or is_stage_enabled(src_env)):
        return None
    stage = RouteClassCompressionStage(env=env)
    if pipeline is None:
        from .pipeline import _get_default_pipeline

        pipeline = _get_default_pipeline()
    pipeline.register(stage)
    return stage


__all__ = [
    "RouteClassCompressionStage",
    "register_with_default_pipeline",
    "is_stage_enabled",
    "ENV_FLAG",
    "TIP_COMPRESSION_V1",
]
