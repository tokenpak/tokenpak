"""TIP capability labels + registry-backed validators.

Capability labels are TIP-defined identifiers (e.g.
``tip.compression.v1``, ``tip.byte-preserved-passthrough``,
``tip.cache.provider-observer``) that a component publishes via MCP
capability negotiation. Authoritative label set lives in the registry
repo at ``schemas/tip/capabilities.schema.json``.

Phase 1 scaffold. Phase 2 populates the label enum (built dynamically
from the registry per Constitution §5.4 — no hardcoded enumerations)
and the ``validate()`` / ``intersect()`` helpers.
"""

from __future__ import annotations
