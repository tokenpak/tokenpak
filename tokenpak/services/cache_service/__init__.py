"""Cache lookup + write orchestration (Phase 2 stage wrapper).

Thin-wraps ``tokenpak.cache`` (which has a real public API, per its
``__init__.py``). On request: look up cached response; on hit, populate
``ctx.response`` and set ``ctx.short_circuit = True``. On response:
write the response to cache if eligible.

Critical (Constitution §5.3): ``cache_origin`` is set here and never
over-claimed. ``proxy`` when we serve from the TokenPak cache,
``client`` when the provider's own cache fires (inferred from response
metadata), ``unknown`` when we cannot attribute.

P2-03 acceptance: single site of cache_origin classification.
"""

from __future__ import annotations

from ..request_pipeline.stages import PipelineContext


class Stage:
    """Cache lookup/write pipeline stage."""

    name = "cache"

    def apply_request(self, ctx: PipelineContext) -> None:
        """Consult cache; short-circuit on hit.

        Phase 2 pass-through. Full implementation: compute cache key
        over ``ctx.request.body`` + headers; call
        ``tokenpak.cache.get_registry().get(key)``; on hit set
        ``ctx.response`` + ``ctx.short_circuit = True`` + record
        ``cache_origin='proxy'`` in ctx.extras.
        """
        return None

    def apply_response(self, ctx: PipelineContext) -> None:
        """Write response to cache if eligible; set cache_origin.

        Phase 2 pass-through. Full implementation: inspect
        ``ctx.response.headers`` for provider-cache hit markers
        (``cache_control`` echo on Anthropic, etc.) and set
        ``cache_origin='client'`` when inferable, ``'unknown'``
        otherwise. Never overwrite ``'proxy'`` if we already set it
        in apply_request.
        """
        return None
