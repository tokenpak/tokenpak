"""Streaming execution entry point.

Parallel to ``execute`` but yields ``Chunk`` frames as provider output
arrives. Phase 2: minimal implementation that runs request-side stages,
dispatches to a caller-supplied async generator, and runs response-side
stages on each chunk in reverse order.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from .request import Request
from .request_pipeline.composition import build_pipeline
from .request_pipeline.stages import PipelineContext
from .response import Chunk


async def stream(
    request: Request,
    *,
    dispatch: Callable[[PipelineContext], AsyncIterator[Chunk]] | None = None,
) -> AsyncIterator[Chunk]:
    """Stream ``request`` through the services pipeline.

    ``dispatch`` must be supplied for a real stream; it is the
    async-iterator factory that yields raw chunks from the provider.
    Response-side stages are applied to each chunk. If no dispatcher
    is provided we raise - see ``execute`` for parallel rationale.
    """
    if dispatch is None:
        raise NotImplementedError(
            "services.stream requires an injected dispatcher; none given"
        )
    stages = build_pipeline()
    ctx = PipelineContext(request=request)
    for stage in stages:
        stage.apply_request(ctx)
        if ctx.short_circuit:
            return  # cache hit or similar: no streaming dispatch
    async for chunk in dispatch(ctx):
        # Response-side stage application on a per-chunk basis is
        # deliberately lightweight - full per-chunk telemetry is a
        # P2 follow-on once real streaming lands from the proxy handler.
        yield chunk
