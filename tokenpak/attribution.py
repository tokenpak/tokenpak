"""Backwards-compat shim — see telemetry.attribution.

Canonical home is ``tokenpak.telemetry.attribution`` (Architecture §1 —
telemetry owns attribution / aggregation / timeline concerns). Moved
2026-04-20 per D1 migration. Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.telemetry.attribution import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.attribution is deprecated — "
    "import from tokenpak.telemetry.attribution. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
