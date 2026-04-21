"""Resource dispatch registry for MCP.

TIP-1.0 resources (e.g. ``tip://status/summary``, ``tip://telemetry/today``,
``tip://cache/stats``, ``tip://protocol/version``) are registered here.
Unlike tools they are read-only and carry a URI scheme.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .errors import MCPBridgeError

ResourceReader = Callable[[], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class ResourceSpec:
    """An MCP resource registration record."""

    uri: str           # e.g. "tip://status/summary"
    description: str
    reader: ResourceReader
    mime_type: str = "application/json"


class ResourceRegistry:
    """Dispatches MCP resource reads by URI."""

    def __init__(self) -> None:
        self._resources: dict[str, ResourceSpec] = {}

    def register(self, spec: ResourceSpec) -> None:
        if spec.uri in self._resources:
            raise MCPBridgeError(f"resource already registered: {spec.uri}")
        self._resources[spec.uri] = spec

    def list(self) -> list[ResourceSpec]:
        return list(self._resources.values())

    async def read(self, uri: str) -> dict[str, Any]:
        spec = self._resources.get(uri)
        if spec is None:
            raise MCPBridgeError(f"unknown resource: {uri}")
        return await spec.reader()
