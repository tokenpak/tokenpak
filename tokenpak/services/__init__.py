"""Shared execution backbone — the canonical place TokenPak runs a request.

``services/`` owns the wire-format-agnostic functional pipeline (compression
→ security → cache → routing → telemetry → dispatch), request-lifecycle
orchestration, budget and cost orchestration, streaming lifecycle, provider-
call invariants, and the MCP bridge that companion/sdk consume.

Every request that passes through TokenPak — whether it arrived via HTTP at
``proxy/``, via a framework adapter in ``sdk/``, via ``cli/``, or via an
exception-path companion call — executes through ``services.execute(...)``.
This is where shared functional logic lives; duplicating it elsewhere is an
Architecture Standard §10 finding.

Public surface (import via ``from tokenpak.services import ...``):

    Request, Response, Chunk       - wire-format-agnostic types (§2.4 contract)
    execute(request) -> Response    - synchronous execution entry point
    stream(request) -> AsyncIterator[Chunk]
                                    - streaming execution entry point
    mcp_bridge                      - MCP protocol plumbing module

Subpackages:

    request_pipeline/     - pipeline composition (compression -> ... -> dispatch)
    compression_service/  - compression-stage orchestration
    cache_service/        - cache lookup + write orchestration
    routing_service/      - routing decisions + fallback execution
    telemetry_service/    - per-request telemetry emission
    policy_service/       - budget / cost / rate-limit / content-policy gates
    mcp_bridge/           - MCP protocol adapter shared by companion + sdk.mcp
    client/               - internal client helpers (see also proxy.client)

Phase 2 scaffold (2026-04-20) - directories and package skeletons only.
Pipeline logic migrates from ``proxy/`` in subsequent PRs (task packets P2-01
through P2-05). See Architecture §10 debt item D1.
"""

from __future__ import annotations

from .execute import execute
from .request import Request
from .response import Chunk, Response
from .stream import stream

__all__ = [
    "Request",
    "Response",
    "Chunk",
    "execute",
    "stream",
    "request_pipeline",
    "compression_service",
    "cache_service",
    "routing_service",
    "telemetry_service",
    "policy_service",
    "mcp_bridge",
]
