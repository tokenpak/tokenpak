"""Tool dispatch registry for MCP.

TIP-1.0 tools (e.g. ``tip.integrate_client``, ``tip.preview_compression``,
``tip.explain_savings``, ``tip.get_proxy_status``) are registered here
by ``companion/`` (as MCP server) and invoked by MCP clients. The
registry translates MCP tool-call frames into handler invocations and
handler returns into MCP tool-result frames.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .errors import MCPBridgeError

ToolHandler = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class ToolSpec:
    """An MCP tool's registration record."""

    id: str                # e.g. "tip.preview_compression"
    description: str
    handler: ToolHandler
    input_schema: dict[str, Any] | None = None


class ToolRegistry:
    """Dispatches MCP tool calls by tool id.

    Handlers are async; they receive the tool's JSON arguments and
    return a JSON-serializable result.
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.id in self._tools:
            raise MCPBridgeError(f"tool already registered: {spec.id}")
        self._tools[spec.id] = spec

    def list(self) -> list[ToolSpec]:
        return list(self._tools.values())

    async def call(self, tool_id: str, args: dict[str, Any]) -> dict[str, Any]:
        spec = self._tools.get(tool_id)
        if spec is None:
            raise MCPBridgeError(f"unknown tool: {tool_id}")
        return await spec.handler(args)
