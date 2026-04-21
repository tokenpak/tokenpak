"""Backwards-compat shim — see debug.calibration.

Canonical home is ``tokenpak.debug.calibration`` (Architecture §1 —
debug owns diagnostic helpers including calibration). Moved
2026-04-20 per D1 migration. Removal target: TIP-2.0.
"""

from __future__ import annotations

import warnings

from tokenpak.debug.calibration import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.calibration is deprecated — "
    "import from tokenpak.debug.calibration. "
    "Legacy shim removal target: TIP-2.0.",
    DeprecationWarning,
    stacklevel=2,
)
