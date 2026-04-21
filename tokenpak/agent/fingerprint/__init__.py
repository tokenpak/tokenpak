"""Backwards-compatible re-export shim — see compression.fingerprinting.

The canonical home for fingerprint code is
``tokenpak.compression.fingerprinting`` (Architecture §1 canonical
layout). Moved 2026-04-20 per D1 migration. This shim lets existing
``from tokenpak.agent.fingerprint...`` imports keep working for one
MINOR. Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.compression.fingerprinting import (  # noqa: F401
    Fingerprint,
    FingerprintGenerator,
    FingerprintSync,
    PrivacyLevel,
    Segment,
    SyncResult,
    apply_privacy,
)

warnings.warn(
    "tokenpak.agent.fingerprint is deprecated — "
    "import from tokenpak.compression.fingerprinting (canonical home since 2026-04-20, D1 migration). "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "FingerprintGenerator",
    "Fingerprint",
    "Segment",
    "PrivacyLevel",
    "apply_privacy",
    "FingerprintSync",
    "SyncResult",
]
