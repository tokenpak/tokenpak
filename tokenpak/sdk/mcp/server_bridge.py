"""Glue between sdk.mcp client usage and companion's MCP server.

Used when a framework-adapter in ``sdk/frameworks/*`` wants to expose
TokenPak's control-plane tools to its own host framework (e.g. a
LangChain Runnable that surfaces ``tip.preview_compression`` to the
LangChain user). Routes through ``Client`` in ``sdk.mcp.client``.
"""

from __future__ import annotations

from typing import Any

from .client import Client, ClientOptions


class ServerBridge:
    """Invoke companion MCP tools from framework-adapter code.

    Phase 2 surface scaffold. Real implementation lands after
    ``Client.connect`` (pending DECISION-P2-LIB).
    """

    def __init__(self, options: ClientOptions | None = None) -> None:
        self._options = options or ClientOptions()
        self._client: Client | None = None

    async def __aenter__(self) -> "ServerBridge":
        self._client = await Client.connect(self._options)
        return self

    async def __aexit__(self, *_: object) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    async def call(self, tool_id: str, args: dict[str, Any]) -> dict[str, Any]:
        if self._client is None:
            raise RuntimeError("ServerBridge not entered")
        # Delegates to Client.call_tool (defined in the same pending
        # DECISION-P2-LIB follow-on that wires Client.connect).
        raise NotImplementedError(
            "ServerBridge.call pending sdk.mcp.client.Client.connect"
        )
