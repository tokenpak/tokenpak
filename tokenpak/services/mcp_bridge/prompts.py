"""Prompt dispatch registry for MCP.

TIP-1.0 prompts (e.g. ``tip.optimize_prompt``, ``tip.analyze_context_waste``,
``tip.summarize_capsule_candidates``) are registered here. Prompts
differ from tools in that they return structured prompt content the
caller then sends to an LLM - they do NOT themselves invoke a provider.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from .errors import MCPBridgeError

PromptRenderer = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(slots=True)
class PromptSpec:
    """An MCP prompt registration record."""

    name: str
    description: str
    renderer: PromptRenderer
    argument_schema: dict[str, Any] | None = None


class PromptRegistry:
    """Dispatches MCP prompt renders by name."""

    def __init__(self) -> None:
        self._prompts: dict[str, PromptSpec] = {}

    def register(self, spec: PromptSpec) -> None:
        if spec.name in self._prompts:
            raise MCPBridgeError(f"prompt already registered: {spec.name}")
        self._prompts[spec.name] = spec

    def list(self) -> list[PromptSpec]:
        return list(self._prompts.values())

    async def render(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        spec = self._prompts.get(name)
        if spec is None:
            raise MCPBridgeError(f"unknown prompt: {name}")
        return await spec.renderer(args)
