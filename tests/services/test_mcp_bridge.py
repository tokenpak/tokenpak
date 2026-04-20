"""Tests for services.mcp_bridge - the shared MCP plumbing.

Tests the bridge's public surface (registries, capability negotiation,
error shape) without requiring the upstream MCP library (pending
DECISION-P2-LIB). Real protocol dispatch tests land with the pin.
"""

from __future__ import annotations

import pytest

from tokenpak.services.mcp_bridge import (
    CapabilityNegotiator,
    MCPBridgeError,
    PromptRegistry,
    ResourceRegistry,
    ToolRegistry,
    TransportKind,
)


class TestCapabilityNegotiator:
    def test_intersection_of_local_and_peer(self):
        neg = CapabilityNegotiator(
            frozenset({"tip.compression.v1", "tip.preview.local"})
        )
        result = neg.negotiate(
            frozenset({"tip.preview.local", "tip.cache.provider-observer"})
        )
        assert result.agreed == frozenset({"tip.preview.local"})

    def test_requires_raises_on_missing_peer_capability(self):
        neg = CapabilityNegotiator(frozenset({"tip.compression.v1"}))
        with pytest.raises(MCPBridgeError, match="missing required"):
            neg.requires(
                frozenset({"tip.byte-preserved-passthrough"}),
                frozenset({"tip.compression.v1"}),  # peer doesn't have passthrough
            )

    def test_requires_passes_when_peer_has_all(self):
        neg = CapabilityNegotiator(frozenset({"tip.compression.v1"}))
        neg.requires(
            frozenset({"tip.preview.local"}),
            frozenset({"tip.preview.local", "tip.cache.provider-observer"}),
        )  # no raise


class TestToolRegistry:
    def test_register_and_list(self):
        reg = ToolRegistry()
        from tokenpak.services.mcp_bridge.tools import ToolSpec

        async def _handler(args):
            return {"ok": True, "echoed": args}

        reg.register(ToolSpec(id="tip.test", description="x", handler=_handler))
        assert len(reg.list()) == 1
        assert reg.list()[0].id == "tip.test"

    def test_duplicate_registration_raises(self):
        reg = ToolRegistry()
        from tokenpak.services.mcp_bridge.tools import ToolSpec

        async def _h(args):
            return {}

        reg.register(ToolSpec(id="tip.a", description="", handler=_h))
        with pytest.raises(MCPBridgeError, match="already registered"):
            reg.register(ToolSpec(id="tip.a", description="", handler=_h))

    @pytest.mark.asyncio
    async def test_call_dispatches_to_handler(self):
        reg = ToolRegistry()
        from tokenpak.services.mcp_bridge.tools import ToolSpec

        async def _handler(args):
            return {"doubled": args["n"] * 2}

        reg.register(
            ToolSpec(id="tip.double", description="", handler=_handler)
        )
        result = await reg.call("tip.double", {"n": 7})
        assert result == {"doubled": 14}

    @pytest.mark.asyncio
    async def test_call_unknown_tool_raises(self):
        reg = ToolRegistry()
        with pytest.raises(MCPBridgeError, match="unknown tool"):
            await reg.call("tip.does-not-exist", {})


class TestResourceRegistry:
    @pytest.mark.asyncio
    async def test_read_returns_reader_result(self):
        reg = ResourceRegistry()
        from tokenpak.services.mcp_bridge.resources import ResourceSpec

        async def _reader():
            return {"ok": True}

        reg.register(
            ResourceSpec(
                uri="tip://status/summary", description="x", reader=_reader
            )
        )
        assert await reg.read("tip://status/summary") == {"ok": True}


class TestPromptRegistry:
    @pytest.mark.asyncio
    async def test_render_returns_renderer_result(self):
        reg = PromptRegistry()
        from tokenpak.services.mcp_bridge.prompts import PromptSpec

        async def _renderer(args):
            return {"rendered": f"prompt-for-{args['kind']}"}

        reg.register(
            PromptSpec(
                name="tip.optimize_prompt", description="", renderer=_renderer
            )
        )
        result = await reg.render("tip.optimize_prompt", {"kind": "code"})
        assert result == {"rendered": "prompt-for-code"}


class TestTransportKind:
    def test_stdio_and_streamable_http_exist(self):
        assert TransportKind.STDIO.value == "stdio"
        assert TransportKind.STREAMABLE_HTTP.value == "streamable_http"
