"""Connection lifecycle management for MCP peers.

Wraps the upstream MCP library's initialization handshake and graceful
shutdown. Exposes a minimal state machine: ``disconnected`` ->
``initializing`` -> ``ready`` -> ``closing`` -> ``disconnected``.
"""

from __future__ import annotations

from enum import Enum

from .transport import Transport


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    INITIALIZING = "initializing"
    READY = "ready"
    CLOSING = "closing"


class LifecycleManager:
    """Drives an MCP connection through its lifecycle."""

    def __init__(self, transport: Transport) -> None:
        self.transport = transport
        self.state: ConnectionState = ConnectionState.DISCONNECTED

    async def initialize(self) -> None:
        """Send the MCP initialize handshake and wait for ack.

        Phase 2: marks the connection as ready without actually sending
        MCP initialize frames, pending upstream library pin. Real
        implementation lands when DECISION-P2-LIB is taken.
        """
        self.state = ConnectionState.INITIALIZING
        # TODO: real handshake via upstream MCP lib
        self.state = ConnectionState.READY

    async def shutdown(self) -> None:
        """Send shutdown + close transport."""
        self.state = ConnectionState.CLOSING
        await self.transport.close()
        self.state = ConnectionState.DISCONNECTED
