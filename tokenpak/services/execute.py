"""Synchronous execution entry point for the services pipeline.

Composes the canonical pipeline
(compression -> security -> cache -> routing -> telemetry -> dispatch)
per Architecture §1.3 design invariant 1. Stages live in sibling
subpackages; this module just sequences them via
``request_pipeline.composition``.

Called by ``proxy.client.execute`` (in-process transport) and by the
HTTP proxy server handler (which supplies a real provider-invoking
dispatcher).
"""

from __future__ import annotations

from collections.abc import Callable

from .request import Request
from .request_pipeline.composition import run_pipeline
from .request_pipeline.dispatch import raise_if_called
from .request_pipeline.stages import PipelineContext
from .response import Response


def execute(
    request: Request,
    *,
    dispatch: Callable[[PipelineContext], Response] | None = None,
) -> Response:
    """Run ``request`` through the services pipeline.

    ``dispatch`` is the terminal provider-invoking step. Callers MUST
    supply one; there is no sensible default (Phase 2). Proxy server
    supplies one wired to ``proxy.adapters``. Tests supply a fixture.
    """
    dispatcher = dispatch if dispatch is not None else raise_if_called
    return run_pipeline(request, dispatcher)
