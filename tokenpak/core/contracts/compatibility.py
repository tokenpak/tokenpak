"""TIP version range + profile compatibility rules.

Defines how two TokenPak components decide whether they can talk to
each other: TIP version ranges, required profiles, required
capabilities. Compatibility rules live in the registry at
``schemas/tip/compatibility.schema.json``.

Phase 1 scaffold. Phase 2 populates the ``VersionRange``,
``ProfileRequirement``, and ``check_compatibility()`` helpers used by
``services/mcp_bridge/`` and the manifest loader.
"""

from __future__ import annotations
