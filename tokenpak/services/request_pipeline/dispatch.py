"""Terminal dispatch step - hand the request off to a provider.

Separate from ``stages.py`` because dispatch is not a pipeline stage in
the ``Stage`` protocol sense - it is the terminal action that runs once
all request-side stages have completed (and response-side stages have
not yet started).

Phase 2: dispatch is supplied by the caller of ``run_pipeline``. The
default in-process dispatcher raises NotImplementedError. Proxy HTTP
handlers supply their own dispatcher that hits a real provider via
``proxy.adapters``. Tests supply a fixture dispatcher.
"""

from __future__ import annotations

from ..response import Response
from .stages import PipelineContext


def raise_if_called(ctx: PipelineContext) -> Response:
    """Default dispatch - raises. Callers MUST inject a real dispatcher.

    This default exists so a misconfigured services.execute call fails
    loudly rather than silently returning an empty Response.
    """
    raise NotImplementedError(
        "no dispatcher injected into services pipeline. "
        "proxy.server supplies a provider-invoking dispatcher; "
        "tests supply a fixture. "
        "services.execute never calls this default - it always threads "
        "an explicit dispatcher through."
    )
