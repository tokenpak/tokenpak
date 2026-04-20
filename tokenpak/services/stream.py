"""Streaming execution entry point for the services pipeline.

Mirror of ``execute`` for streaming responses. Yields ``Chunk`` frames as
they become available from the provider, after each frame has passed
through the streaming-aware parts of the pipeline (cache-write, telemetry,
response-side transforms).

Phase 2 scaffold. Real implementation lands in P2-01 alongside
``execute``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from .request import Request
from .response import Chunk


async def stream(request: Request) -> AsyncIterator[Chunk]:
    """Stream ``request`` through the services pipeline.

    Phase 2 scaffold. Real composition lands in P2-01.
    """
    raise NotImplementedError(
        "services.stream is a Phase 2 scaffold. Streaming pipeline "
        "composition ships in task packet P2-01."
    )
    yield  # pragma: no cover  (makes the function an async generator)
