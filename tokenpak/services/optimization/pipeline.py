"""Observe-only optimization pipeline.

The pipeline runs registered ``OptimizationStage`` instances against an
``OptimizationContext`` and emits a ``StageTrace`` for each. Observe-only
mode means the pipeline calls ONLY ``stage.eligible(ctx)`` — never
``stage.apply(ctx)``. Body bytes flow through unchanged by construction.

Integration point: ``run_observe_only(...)`` is the single function call
sites in ``proxy/server.py`` use. It accepts the inputs available at the
proxy hot path and returns an ``OptimizationTrace``. Callers MUST treat
the original ``body`` as the only request body — this function never
returns a different one.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional, Tuple

from .context import OptimizationContext
from .contract_builder import build_contract
from .policies import bypass_reasons, is_pipeline_enabled
from .registry import StageRegistry
from .stage import EligibilityResult, OptimizationStage
from .trace import OptimizationTrace, StageTrace

_log = logging.getLogger(__name__)


class OptimizationPipeline:
    """Runs registered stages in observe-only mode.

    Stateless; create once per process or once per request, both work.
    Stage registration order determines run order.
    """

    def __init__(self, registry: Optional[StageRegistry] = None) -> None:
        self.registry = registry or StageRegistry()

    def register(self, stage: OptimizationStage) -> None:
        self.registry.register(stage)

    def run_observe_only(self, ctx: OptimizationContext) -> OptimizationTrace:
        """Iterate every registered stage and record an eligibility trace.

        The pipeline NEVER calls ``stage.apply`` here. Body byte counts
        on the trace are the input length, both in and out — the contract
        is that ``ctx.raw_body`` is the request body the proxy will send.
        """
        trace = ctx.trace
        trace.body_bytes_in = ctx.body_size

        for stage in self.registry:
            started = time.perf_counter()
            try:
                result = stage.eligible(ctx)
            except Exception as exc:
                # Stage eligibility failures must NEVER break a request.
                # Record a synthetic skip and continue.
                _log.debug(
                    "optimization.pipeline: %s.eligible raised %s: %s; treating as ineligible",
                    getattr(stage, "name", "<unknown>"),
                    type(exc).__name__,
                    exc,
                )
                result = EligibilityResult(
                    eligible=False,
                    skip_reason="eligible-exception",
                    detail=f"{type(exc).__name__}: {exc}",
                )
            elapsed_ms = (time.perf_counter() - started) * 1000.0

            trace.add_stage(
                StageTrace(
                    name=getattr(stage, "name", "<unknown>"),
                    eligible=result.eligible,
                    skip_reason=result.skip_reason,
                    applied=False,
                    duration_ms=elapsed_ms,
                    detail=result.detail,
                )
            )

        # Observe-only contract: body_bytes_out == body_bytes_in.
        trace.body_bytes_out = ctx.body_size
        return trace


# ---------------------------------------------------------------------------
# Module-level convenience for the proxy hot path
# ---------------------------------------------------------------------------


_default_pipeline: Optional[OptimizationPipeline] = None


def _get_default_pipeline() -> OptimizationPipeline:
    global _default_pipeline
    if _default_pipeline is None:
        _default_pipeline = OptimizationPipeline()
    return _default_pipeline


def reset_default_pipeline() -> None:
    """Test helper — drop the cached default pipeline."""
    global _default_pipeline
    _default_pipeline = None


def run_observe_only(
    *,
    request_id: str,
    body: bytes,
    method: str = "POST",
    path: str = "",
    headers: Optional[Dict[str, str]] = None,
    target_url: str = "",
    adapter: Any = None,
    platform: Optional[str] = None,
    route: Optional[str] = None,
    policy: Optional[Dict[str, Any]] = None,
    pipeline: Optional[OptimizationPipeline] = None,
    env: Optional[Dict[str, str]] = None,
) -> Tuple[bytes, OptimizationTrace]:
    """Run the observe-only pipeline if enabled; return (body, trace).

    Always returns the *same* ``body`` bytes that were passed in — the
    pipeline never mutates them. When the feature flag is off the function
    is a near no-op (returns a trace with mode='off' and zero stages).

    Call sites SHOULD always use the returned ``body`` (never substitute
    the original): this lets a future, gated, mutate-mode share the same
    integration site. Today, identity is the documented contract.
    """
    headers = dict(headers or {})

    if not is_pipeline_enabled(env):
        trace = OptimizationTrace(request_id=request_id, mode="off")
        trace.body_bytes_in = len(body) if body else 0
        trace.body_bytes_out = trace.body_bytes_in
        return body, trace

    bypass, reason = bypass_reasons(method=method, path=path, body=body or b"")
    if bypass:
        trace = OptimizationTrace(request_id=request_id, mode="observe")
        trace.mark_bypass(reason)
        trace.body_bytes_in = len(body) if body else 0
        trace.body_bytes_out = trace.body_bytes_in
        return body, trace

    contract = build_contract(
        adapter=adapter,
        platform=platform,
        route=route,
        policy=policy,
    )

    trace = OptimizationTrace(request_id=request_id, mode="observe")
    ctx = OptimizationContext(
        request_id=request_id,
        raw_body=body,
        trace=trace,
        adapter=adapter,
        platform=platform,
        route=route,
        policy=dict(policy or {}),
        contract=contract,
        headers=headers,
        target_url=target_url,
    )

    runner = pipeline or _get_default_pipeline()
    runner.run_observe_only(ctx)
    return body, trace


__all__ = [
    "OptimizationPipeline",
    "run_observe_only",
    "reset_default_pipeline",
]
