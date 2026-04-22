"""TIP capability labels + TokenPak self-declaration.

Capability labels are TIP-defined identifiers (e.g. ``tip.compression.v1``,
``tip.byte-preserved-passthrough``, ``tip.cache.provider-observer``) that a
component publishes via MCP capability negotiation or in an
``X-TokenPak-Capability`` header. Authoritative label set lives in the
registry repo at ``capability-catalog.json`` (validates against
``schemas/tip/capabilities.schema.json``).

This module exports TokenPak-the-package's *self-declared* capability set
— what TokenPak publishes in its own responses and MCP frames. Two
consumers rely on this being the single source of truth:

1. ``scripts/tip_conformance_check.py`` (legacy paper-spec gate) —
   verifies every label is in the registry catalog AND satisfies the
   profile requirements for ``SELF_PROFILES``.
2. ``tests/conformance/`` (TIP-SC phase, 2026-04-22) — uses these sets
   to (a) drive ``notify_capability_published`` at boot, (b) assert
   ``tokenpak/manifests/*.json`` capability arrays match, and (c) run
   ``validate_profile`` against the canonical set.

Adding a label here without implementing it is an audit finding.
Removing a label because it isn't implemented yet is fine — the
catalog simply says "TokenPak doesn't claim this."
"""
from __future__ import annotations

# Self-declared TIP-1.0 capability set for the tip-proxy profile.
# Source of truth for what the proxy publishes at startup (see
# proxy/server.py ConformanceObserver notify_capability_published).
#
# Every label MUST exist in registry/capability-catalog.json. The CI
# self-conformance workflow enforces this via validate_profile().
SELF_CAPABILITIES_PROXY: frozenset[str] = frozenset({
    # required (per catalog classes)
    "tip.cache.provider-observer",
    "tip.routing.classifier.v1",
    "tip.security.header-allowlist",
    "tip.telemetry.wire-side",
    # profile-specific / optional that the proxy implements today
    "tip.compression.v1",
    "tip.byte-preserved-passthrough",
    "tip.cache.proxy-managed",
    "tip.cache.ttl-ordering",
    "tip.routing.fallback-chain",
    "tip.security.dlp-redaction",
})

# Self-declared set for the tip-companion profile. Companion publishes
# these via its MCP server surface (pre-send optimizer concerns).
SELF_CAPABILITIES_COMPANION: frozenset[str] = frozenset({
    # required
    "tip.cache.provider-observer",
    "tip.mcp.bridge.v1",
    # profile-specific
    "tip.telemetry.prompt-side",
    "tip.companion.prompt-packaging",
    "tip.companion.memory-capsule",
    "tip.companion.session-journal",
    # local-only
    "tip.preview.local",
})

# Aggregated set — what TokenPak publishes across all its profiles.
SELF_CAPABILITIES: frozenset[str] = (
    SELF_CAPABILITIES_PROXY | SELF_CAPABILITIES_COMPANION
)

# Profiles TokenPak claims conformance for. When the conformance gate
# passes both, Constitution §13.3 reference-implementation status is
# satisfied mechanically (Phase TIP-SC, 2026-04-22).
SELF_PROFILES: tuple[str, ...] = ("tip-proxy", "tip-companion")

__all__ = [
    "SELF_CAPABILITIES",
    "SELF_CAPABILITIES_PROXY",
    "SELF_CAPABILITIES_COMPANION",
    "SELF_PROFILES",
]
