"""Backwards-compat shim — renamed to compression.capsules.

Canonical home is ``tokenpak.compression.capsules`` (Architecture §1 —
proxy-pipeline capsule compression belongs under compression; distinct
from ``companion/capsules/`` which is companion-side memory capsules).
Renamed 2026-04-20 per Kevin's capsule disambiguation decision.
Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.compression.capsules import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.capsule is deprecated — renamed to tokenpak.compression.capsules. "
    "(This is the proxy-pipeline capsule builder; tokenpak.companion.capsules "
    "is the distinct companion-side memory-capsule subsystem.) "
    "Removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
