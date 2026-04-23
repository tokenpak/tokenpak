"""Backwards-compat shim — see tokenpak.telemetry.forecast."""
from __future__ import annotations

import warnings

from tokenpak.telemetry.forecast import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.forecast is deprecated — import from tokenpak.telemetry.forecast. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
