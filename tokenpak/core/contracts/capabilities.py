"""TIP capability labels + TokenPak self-declaration.

Capability labels are TIP-defined identifiers (e.g. ``tip.compression.v1``,
``tip.byte-preserved-passthrough``, ``tip.cache.provider-observer``) that a
component publishes via MCP capability negotiation or in an
``X-TokenPak-Capability`` header. Authoritative label set lives in the
registry repo at ``schemas/tip/capabilities.schema.json``.

This module exports TokenPak-the-package's *self-declared* capability set
— what TokenPak publishes in its own responses and MCP frames. The
reference-implementation conformance check
(``scripts/tip_conformance_check.py``) verifies every label here is in
the registry catalog AND satisfies the profile requirements for the
``SELF_PROFILES`` TokenPak claims.
"""

from __future__ import annotations

# TokenPak's self-declared TIP-1.0 capability set for the tip-proxy
# profile. The proxy path claims these; adding one here without
# implementing it in services/ is an audit finding.
SELF_CAPABILITIES_PROXY: frozenset[str] = frozenset({
    "tip.compression.v1",
    "tip.cache.provider-observer",
    "tip.telemetry.wire-side",
    "tip.byte-preserved-passthrough",
    "tip.routing.fallback-chain",
    "tip.security.dlp-redaction",
})

# Self-declared set for the tip-companion profile. Companion publishes
# these for its MCP server surface (pre-send optimizer concerns).
SELF_CAPABILITIES_COMPANION: frozenset[str] = frozenset({
    "tip.preview.local",
    "tip.companion.prompt-packaging",
    "tip.companion.memory-capsule",
    "tip.companion.session-journal",
})

# Aggregated set — what TokenPak publishes across all its profiles.
SELF_CAPABILITIES: frozenset[str] = (
    SELF_CAPABILITIES_PROXY | SELF_CAPABILITIES_COMPANION
)

# Profiles TokenPak claims conformance for. When the conformance check
# script passes both, Constitution §13.3 reference-implementation
# status is satisfied (pending pipeline-stage logic completion).
SELF_PROFILES: tuple[str, ...] = ("tip-proxy", "tip-companion")

__all__ = [
    "SELF_CAPABILITIES",
    "SELF_CAPABILITIES_PROXY",
    "SELF_CAPABILITIES_COMPANION",
    "SELF_PROFILES",
]
