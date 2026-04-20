"""Pipeline composition — instantiate stages and run them in order.

Called by ``services.execute`` (sync) and ``services.stream`` (async-iter).
This module does not own stages; it just sequences them.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from .stages import CANONICAL_STAGES, PipelineContext, Stage

if TYPE_CHECKING:
    from ..request import Request
    from ..response import Response


# Stage name (canonical architecture term) -> implementing subpackage.
# The sequence is normative (Architecture §1.3 invariant 1); the
# subpackage names follow the concern-per-subsystem rule (§1) and
# don't have to equal the stage name. ``security`` -> ``policy_service``
# because policy_service covers budget + cost + rate-limit + DLP, which
# is broader than just security.
_STAGE_MODULES: dict[str, str] = {
    "compression": "tokenpak.services.compression_service",
    "security": "tokenpak.services.policy_service",
    "cache": "tokenpak.services.cache_service",
    "routing": "tokenpak.services.routing_service",
    "telemetry": "tokenpak.services.telemetry_service",
}


def _load_stage(name: str) -> Stage | None:
    """Import the stage implementation for ``name``.

    Returns None if the subpackage doesn't expose a ``Stage`` (for
    Phase 2 that is the steady state of most stages). Missing stages
    are pass-through in the pipeline, not errors - they'll gain
    ``apply_request`` / ``apply_response`` as D1 migration lands.
    """
    module_name = _STAGE_MODULES.get(name)
    if module_name is None:
        return None
    try:
        module = __import__(module_name, fromlist=["Stage"])
    except ImportError:
        return None
    stage_class = getattr(module, "Stage", None)
    if stage_class is None:
        return None
    return stage_class()  # type: ignore[no-any-return]


def build_pipeline() -> list[Stage]:
    """Return the ordered list of stage instances to run."""
    stages: list[Stage] = []
    for name in CANONICAL_STAGES:
        stage = _load_stage(name)
        if stage is not None:
            stages.append(stage)
    return stages


def run_pipeline(
    request: Request,
    dispatch: Callable[[PipelineContext], Response],
) -> Response:
    """Thread ``request`` through every stage, dispatch, run response stages.

    ``dispatch`` is injected so tests can substitute a fixture and the
    real path can use a provider-invoking dispatcher. Any stage may set
    ``ctx.short_circuit = True`` to skip dispatch (e.g. cache hit).
    """
    stages = build_pipeline()
    ctx = PipelineContext(request=request)
    for stage in stages:
        stage.apply_request(ctx)
        if ctx.short_circuit:
            break
    if not ctx.short_circuit:
        ctx.response = dispatch(ctx)
    # Reverse-order response transforms (outer-to-inner symmetry).
    for stage in reversed(stages):
        stage.apply_response(ctx)
    assert ctx.response is not None, (
        "pipeline ended without a response; a stage set short_circuit "
        "without populating ctx.response"
    )
    return ctx.response
