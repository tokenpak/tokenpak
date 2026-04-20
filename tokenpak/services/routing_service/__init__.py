"""Routing orchestration (Phase 2 stage wrapper).

Thin-wraps ``tokenpak.routing`` (``RouteEngine``, ``RouteRule``,
``RouteStore`` are real public types). Selects provider/model;
applies fallback chains on provider failure; honors circuit-breaker
state.

P2-04 acceptance: ``ctx.extras['routing_decision']`` carries the
selected provider + model; the dispatcher the proxy server hands to
``services.execute`` honors this decision.
"""

from __future__ import annotations

from ..request_pipeline.stages import PipelineContext


class Stage:
    """Routing decision pipeline stage."""

    name = "routing"

    def apply_request(self, ctx: PipelineContext) -> None:
        """Select provider + model for ``ctx.request``.

        Phase 2 pass-through. Full implementation: construct a
        ``RouteEngine`` from the user's config once (module-level
        cache), call ``.route(ctx.request.metadata)``, store the
        decision in ``ctx.extras['routing_decision']``.
        """
        return None

    def apply_response(self, ctx: PipelineContext) -> None:
        """Record provider failure into circuit-breaker state.

        Phase 2 pass-through. Full implementation: if
        ``ctx.response.status >= 500``, notify the circuit breaker
        in ``routing/`` so subsequent requests pick a fallback.
        """
        return None
