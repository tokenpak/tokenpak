"""Backwards-compat shim — see telemetry.aggregate.

Canonical home is ``tokenpak.telemetry.aggregate`` (Architecture §1 —
telemetry owns attribution / aggregation / timeline concerns). Moved
2026-04-20 per D1 migration. Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.telemetry.aggregate import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.aggregate is deprecated — "
    "import from tokenpak.telemetry.aggregate. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
