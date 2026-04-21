"""Transport abstraction for MCP.

Two transports are in scope for TIP-1.0 (docs/protocol/transport-bindings.md):

- ``stdio``           — for TUI/CLI agent tools that spawn the companion
                        as a child process.
- ``streamable_http`` — for IDE extensions and long-lived editor tooling
                        that maintain a persistent MCP connection.

The abstract ``Transport`` protocol defines the 4 methods every
transport implementation MUST provide. Concrete transports plug in
via the upstream MCP library once pinned.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Protocol, runtime_checkable


class TransportKind(Enum):
    """The two MCP transports TIP-1.0 recognizes."""

    STDIO = "stdio"
    STREAMABLE_HTTP = "streamable_http"


@runtime_checkable
class Transport(Protocol):
    """A running MCP transport session.

    Concrete implementations wrap an upstream MCP library session type.
    """

    kind: TransportKind

    async def send(self, frame: dict[str, Any]) -> None:
        """Send one JSON-RPC frame to the peer."""

    async def recv(self) -> dict[str, Any]:
        """Receive one JSON-RPC frame from the peer. Blocks until one arrives."""

    async def close(self) -> None:
        """Close the transport cleanly."""

    def is_open(self) -> bool:
        """Return True while the transport can still send/recv."""
