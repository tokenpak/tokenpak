"""Shared MCP protocol plumbing (Architecture §1.4 plane rule 4).

This subpackage is the ONLY site that hosts MCP protocol machinery.
``companion/`` (which exposes the TokenPak MCP server surface) and
``sdk/mcp/`` (which exposes the MCP client bridge) both consume it;
neither forks MCP primitives.

Public surface:

    TransportKind           — stdio | streamable_http (enum)
    Transport               — abstract transport protocol
    LifecycleManager        — connect / negotiate / heartbeat / shutdown
    CapabilityNegotiator    — intersects TIP capability sets against peer
    ToolRegistry            — dispatch TIP-defined tools by id
    ResourceRegistry        — dispatch TIP-defined resources by URI
    PromptRegistry          — dispatch TIP-defined prompts by name
    MCPBridgeError          — canonical error for this subsystem

The concrete transport + JSON-RPC framing plugs in via an upstream MCP
library. The library choice is tracked as DECISION-P2-LIB (follow-on);
until then the bridge exposes the full surface with no-op protocol
dispatch, which is enough for companion + sdk.mcp to develop against.
"""

from __future__ import annotations

from .capabilities import CapabilityNegotiator
from .errors import MCPBridgeError
from .lifecycle import LifecycleManager
from .prompts import PromptRegistry
from .resources import ResourceRegistry
from .tools import ToolRegistry
from .transport import Transport, TransportKind

__all__ = [
    "CapabilityNegotiator",
    "LifecycleManager",
    "MCPBridgeError",
    "PromptRegistry",
    "ResourceRegistry",
    "ToolRegistry",
    "Transport",
    "TransportKind",
]
