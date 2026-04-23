"""Pipeline stage protocol + canonical stage ordering.

Per `01-architecture-standard.md §1.3` design invariant 1, ``services/`` is
the only place the compression -> security -> cache -> routing ->
telemetry -> dispatch sequence exists. This module defines:

- The ``Stage`` protocol every stage conforms to.
- ``CANONICAL_STAGES`` — the ordered list of stage names.
- ``PipelineContext`` — per-request context threaded between stages.

Stage implementations live in sibling subpackages
(``compression_service``, ``cache_service``, ``routing_service``,
``telemetry_service``, ``policy_service``) and are imported+instantiated
by ``services.execute``.

Decision DECISION-P2-01 (approved 2026-04-20): extraction order is
compression -> cache -> routing -> telemetry, because cache hits
short-circuit routing cleanly. Compression -> security runs first so
policy gates see the compressed request. Dispatch is the terminal stage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from tokenpak.core.routing.policy import Policy
    from tokenpak.core.routing.route_class import RouteClass

from ..request import Request
from ..response import Response


@dataclass(slots=True)
class PipelineContext:
    """Per-request state threaded through every pipeline stage.

    Stages may add keys to ``extras`` (e.g. cache_key, routing_decision)
    to communicate with later stages. ``short_circuit`` lets a stage
    (like cache) return a response without running subsequent stages.

    Classification fields (``route_class``, ``policy``) are populated by
    the first pipeline stage (``classify_stage``). Every downstream
    stage branches on ``policy`` fields — not on ``route_class``
    directly — so capability tuning happens in YAML presets rather
    than Python code.
    """

    request: Request
    response: Response | None = None
    short_circuit: bool = False
    stage_telemetry: dict[str, Any] = field(default_factory=dict)
    extras: dict[str, Any] = field(default_factory=dict)
    # Classification — filled in by classify_stage at the top of the
    # pipeline. Before that stage runs these are sentinels; any stage
    # reading them before classify_stage is a boundary violation.
    route_class: "RouteClass | None" = None
    policy: "Policy | None" = None


@runtime_checkable
class Stage(Protocol):
    """Every pipeline stage implements this protocol.

    ``name``              - stable lowercase identifier (matches
                            subpackage prefix, e.g. ``"compression"``).
    ``apply_request``     - mutate ``ctx.request`` in place (or replace
                            via assignment); may set ``ctx.short_circuit``.
    ``apply_response``    - mutate ``ctx.response`` in place on the way
                            back out of the pipeline. Runs in reverse
                            stage order so the first stage runs last on
                            response (outer-to-inner symmetry).
    """

    name: str

    def apply_request(self, ctx: PipelineContext) -> None: ...

    def apply_response(self, ctx: PipelineContext) -> None: ...


# Canonical stage ordering. ``services.execute`` instantiates each
# stage in this order, runs ``apply_request`` forward, dispatches,
# then runs ``apply_response`` in reverse (LIFO).
#
# A stage name present here but with no corresponding implementation
# module is a services scaffold that pass-throughs cleanly. This is
# the Phase 2 reality — stages become live as their primitive modules
# are brought online per Architecture §10 D1 migration.
CANONICAL_STAGES: tuple[str, ...] = (
    "compression",
    "security",
    "cache",
    "routing",
    "telemetry",
)
# ``dispatch`` is not a stage — it is the terminal action after
# stages run. It lives in ``services.request_pipeline.dispatch``.
