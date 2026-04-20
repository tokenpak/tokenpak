"""TIP capability labels <-> MCP capability-negotiation frame translation.

TIP-1.0 capability labels (``core.contracts.capabilities``) are the
authoritative set of things a TokenPak component can promise. MCP's
capability-negotiation frames carry those labels between peers. This
module is the one-way translator - never duplicates the label set or
invents labels not in the registry.

Phase 2 scaffold. Real translator lands in task P2-12, driven by the
registry's ``schemas/tip/capabilities.schema.json``.
"""

from __future__ import annotations
