"""TIP-1.0 contract surface.

Canonical types for TokenPak Integration Protocol (TIP-1.0). Any
subsystem that emits a TIP-defined header, metadata field, telemetry
event, error shape, capability label, compatibility declaration, or
manifest MUST import the type from this package rather than defining
its own. Parallel definitions are an Architecture Standard §10 finding.

Contents:
    tip_version  - TIP version identifier + negotiation helpers.
    headers      - Canonical wire headers (X-TokenPak-*).
    metadata     - Canonical metadata fields carried in requests/events.
    errors       - Canonical error codes and shapes.
    capabilities - Capability label enum and registry-backed validators.
    compatibility- Version range + profile compatibility rules.
    manifests    - Manifest schemas and validators (adapter/plugin/profile).

Phase 1 scaffold (2026-04-20): modules exist as empty stubs. Phase 2
fills in the reference types as shared logic is consolidated into
``services/``. See Architecture §10 debt item D2.
"""

from __future__ import annotations

__all__ = [
    "tip_version",
    "headers",
    "metadata",
    "errors",
    "capabilities",
    "compatibility",
    "manifests",
]
