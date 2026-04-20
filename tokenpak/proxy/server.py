"""Canonical proxy-server surface — re-exports from legacy agent.proxy.server.

Architecture §1 places the proxy server at ``tokenpak.proxy.server``. The
full 10k+ LOC implementation currently lives at
``tokenpak.agent.proxy.server`` pending the broader D1 agent/ → proxy/
migration (Modular Tree Completion initiative). This module makes the
canonical path importable today so new callers target the right name;
when the full migration lands, this file becomes the real home.

Public names re-exported:

    ProxyServer        — the HTTP server class (implements the live
                         request/response lifecycle; entrypoints reach
                         it indirectly via proxy.client, not directly).
    start_proxy        — launcher function used by ``tokenpak serve``.
    GracefulShutdown   — shutdown signal coordinator.
    PipelineTrace      — per-request trace record.
    StageTrace         — per-stage trace record inside a pipeline.
    TraceStorage       — trace persistence.

Level-4 per Architecture §2 dependency layering; entrypoints at
Level 5 (cli as launcher, per §5.2-C) may import this module
directly — the actual request-execution path for ENTRYPOINT USE
is still ``proxy.client`` per §2.4.
"""

from __future__ import annotations

from tokenpak.agent.proxy.server import (  # noqa: F401
    GracefulShutdown,
    PipelineTrace,
    ProxyServer,
    StageTrace,
    TraceStorage,
    start_proxy,
)

__all__ = [
    "ProxyServer",
    "start_proxy",
    "GracefulShutdown",
    "PipelineTrace",
    "StageTrace",
    "TraceStorage",
]
