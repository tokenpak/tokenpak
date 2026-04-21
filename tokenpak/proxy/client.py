"""The canonical entry point contract for reaching the TokenPak backbone.

Per Architecture Standard §2.4, every entrypoint (cli, sdk, companion,
dashboard, alerts) reaches the execution backbone through this module.
Direct imports of ``services/``, ``proxy/`` internals, or pipeline
primitives for request execution are forbidden for entrypoints except
under Architecture §5.2 exception criteria.

Transport modes (auto-selected from runtime context via core/config):

1. **In-process** — entrypoint shares a process with a running proxy.
   Calls dispatch directly to ``services.execute``. Zero HTTP hop.
2. **Loopback** — entrypoint runs in a sidecar process; calls go over
   http://127.0.0.1:<port> using the same wire format external clients
   use.
3. **Remote** — entrypoint targets a user-configured remote proxy;
   calls go over the configured base URL with auth headers.

Callers never choose the mode. ``_resolve_transport()`` picks one from
runtime context.

Phase 2 scaffold:
- The public interface (``execute``, ``stream``, ``health``) is defined.
- **In-process mode** is the real target. Because ``services.execute``
  is itself a Phase 2 scaffold, this function delegates correctly but
  raises NotImplementedError until P2-01 lands. This is intentional -
  no silent success.
- **Loopback** and **Remote** modes are stubs that raise a clear
  NotImplementedError with a pointer to the implementing task packet.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from enum import Enum

from tokenpak.services import execute as _services_execute
from tokenpak.services import stream as _services_stream
from tokenpak.services.request import Request
from tokenpak.services.response import Chunk, Response

__all__ = ["execute", "stream", "health", "ProxyHealth", "Transport"]


class Transport(Enum):
    """Transport mode the proxy client resolved for the current call."""

    IN_PROCESS = "in_process"
    LOOPBACK = "loopback"
    REMOTE = "remote"


@dataclass(slots=True)
class ProxyHealth:
    """Snapshot of proxy reachability returned by ``health()``."""

    reachable: bool
    transport: Transport
    endpoint: str | None = None
    latency_ms: float | None = None
    detail: str | None = None


def _resolve_transport() -> Transport:
    """Resolve which transport mode the current caller should use.

    Phase 2 scaffold. In-process is the only currently-supported mode.
    Loopback and remote resolution ship in P2-11 / P2-12 follow-ons.
    """
    return Transport.IN_PROCESS


def execute(request: Request) -> Response:
    """Run ``request`` through TokenPak and return the Response.

    Auto-selects transport; always dispatches into ``services.execute``
    (directly for in-process, over HTTP for loopback and remote).
    """
    mode = _resolve_transport()
    if mode is Transport.IN_PROCESS:
        return _services_execute(request)
    if mode is Transport.LOOPBACK:
        raise NotImplementedError(
            "proxy.client loopback transport is a Phase 2 scaffold. "
            "Implementation ships with task packet P2-entrypoint-migration "
            "once in-process has proven stable."
        )
    if mode is Transport.REMOTE:
        raise NotImplementedError(
            "proxy.client remote transport is a Phase 2 scaffold. "
            "Implementation ships after loopback; remote is opt-in for "
            "fleet and shared-team-proxy deployments."
        )
    raise RuntimeError(f"unreachable: unknown transport {mode!r}")


def stream(request: Request) -> AsyncIterator[Chunk]:
    """Stream ``request`` through TokenPak, yielding Chunk frames.

    Auto-selects transport; always drives ``services.stream``.
    """
    mode = _resolve_transport()
    if mode is Transport.IN_PROCESS:
        return _services_stream(request)
    if mode is Transport.LOOPBACK:
        raise NotImplementedError(
            "proxy.client loopback streaming is a Phase 2 scaffold."
        )
    if mode is Transport.REMOTE:
        raise NotImplementedError(
            "proxy.client remote streaming is a Phase 2 scaffold."
        )
    raise RuntimeError(f"unreachable: unknown transport {mode!r}")


def health() -> ProxyHealth:
    """Report whether the resolved transport can reach the backbone."""
    mode = _resolve_transport()
    if mode is Transport.IN_PROCESS:
        return ProxyHealth(
            reachable=True,
            transport=mode,
            endpoint=None,
            latency_ms=0.0,
            detail="in-process: services module importable",
        )
    return ProxyHealth(
        reachable=False,
        transport=mode,
        detail=f"transport {mode.value} is a Phase 2 scaffold",
    )
