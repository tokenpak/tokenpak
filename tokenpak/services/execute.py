"""Synchronous execution entry point for the services pipeline.

This is the single function every entrypoint ultimately reaches (via
``proxy.client.execute``) to run a TokenPak request. It composes the
canonical pipeline: compression -> security -> cache -> routing ->
telemetry -> dispatch.

Phase 2 scaffold - pipeline composition lands in task packet P2-01
(request_pipeline). Until then, ``execute`` raises NotImplementedError
rather than silently returning; this enforces that no caller ships
against a ghost implementation.
"""

from __future__ import annotations

from .request import Request
from .response import Response


def execute(request: Request) -> Response:
    """Run ``request`` through the services pipeline and return the Response.

    Phase 2 scaffold. Real composition lands in P2-01.
    """
    raise NotImplementedError(
        "services.execute is a Phase 2 scaffold. "
        "Pipeline composition ships in task packet P2-01 "
        "(extract request_lifecycle from proxy -> services). "
        "Until then, the proxy handles requests via its legacy path."
    )
