"""Backwards-compat shim — see routing.semantic.

Canonical home is ``tokenpak.routing.semantic`` (Architecture §1 —
semantic intent/entity resolution is a routing concern: it normalizes
user wording variants to canonical keys before routing decisions are
made). Moved 2026-04-20 per D1 migration. Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.routing.semantic import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.semantic is deprecated — "
    "import from tokenpak.routing.semantic (canonical home since 2026-04-20, D1 migration). "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
