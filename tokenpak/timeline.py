"""Backwards-compat shim — see telemetry.timeline.

Canonical home is ``tokenpak.telemetry.timeline`` (Architecture §1 —
telemetry owns attribution / aggregation / timeline concerns). Moved
2026-04-20 per D1 migration. Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.telemetry.timeline import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.timeline is deprecated — "
    "import from tokenpak.telemetry.timeline. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
