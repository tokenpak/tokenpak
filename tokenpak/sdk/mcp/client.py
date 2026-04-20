"""MCP client bridge for third-party MCP-aware tools reaching TokenPak.

Wraps ``tokenpak.services.mcp_bridge`` with the connection lifecycle
an MCP client needs. IDEs, agent tools, and MCP-aware CLIs go through
here to reach the companion's control-plane surface.

Level 5 entrypoint (Architecture §2). Consumes ``services/mcp_bridge/``;
does not re-implement MCP primitives (plane rule 4).
"""

from __future__ import annotations

from dataclasses import dataclass

from tokenpak.services.mcp_bridge import (
    LifecycleManager,
    Transport,
    TransportKind,
)


@dataclass(slots=True)
class ClientOptions:
    """Options for opening an MCP client connection to TokenPak."""

    transport_kind: TransportKind = TransportKind.STDIO
    endpoint: str | None = None  # host:port for streamable_http
    required_capabilities: frozenset[str] = frozenset()


class Client:
    """MCP client connected to a TokenPak MCP server.

    Typical usage:

        opts = ClientOptions(transport_kind=TransportKind.STDIO)
        async with Client.connect(opts) as client:
            result = await client.call_tool("tip.get_proxy_status", {})

    Phase 2: the connection + dispatch surface is defined but concrete
    transport wiring pends DECISION-P2-LIB (upstream MCP library pin).
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport
        self._lifecycle = LifecycleManager(transport)

    @classmethod
    async def connect(cls, options: ClientOptions) -> "Client":
        """Open a connection and run the initialize handshake."""
        raise NotImplementedError(
            "Client.connect pending DECISION-P2-LIB (upstream MCP library pin). "
            "The connection surface is ready; transport wiring lands then."
        )

    async def close(self) -> None:
        await self._lifecycle.shutdown()
