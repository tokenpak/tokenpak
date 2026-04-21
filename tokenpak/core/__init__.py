"""Core subsystem (Architecture §1 Level-0).

The system backbone: config + defaults + env overrides, shared runtime
state, shared data structures, shared error types, startup/shutdown
lifecycle, license activation/validation, version checks,
cooldown/rate-recovery primitives.

Hosts ``core/contracts/`` — the canonical TIP-1.0 contract surface.
All wire- and control-plane contracts live there and are imported by
subsystems that implement them (Architecture §1.4 plane rule 3).

Subpackages (as of Phase 2):
    contracts/  — TIP-1.0 types (tip_version, headers, metadata,
                  errors, capabilities, compatibility, manifests)
    auth/       — credential discovery + refresh (D1 migration
                  target from tokenpak/creds/)
    registry/   — component registry runtime helpers
    runtime/    — startup/shutdown lifecycle + shared runtime state
    schemas/    — internal validation schemas
    state_schemas/ — schema versioning for local stores
    validation/ — shared validation helpers

``_index_builder`` (internal) hosts the legacy vault-index builder
used by the rebuild-vault-index.sh script; D1 migration target is
``tokenpak.vault.storage``.
"""

from __future__ import annotations

from tokenpak.core._index_builder import index_directory  # noqa: F401

__all__ = ["index_directory"]
