"""TIP version identifier + negotiation.

TIP versions are ``TIP-<major>.<minor>`` strings. MAJOR bumps break
wire compatibility; MINOR bumps are additive. No patch segment.

Phase 1 scaffold. Phase 2 fills in ``TIPVersion``, ``negotiate()``,
and the ``X-TokenPak-TIP-Version`` header round-tripping.
"""

from __future__ import annotations

CURRENT: str = "TIP-1.0"
