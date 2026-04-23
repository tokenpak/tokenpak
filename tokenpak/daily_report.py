"""Backwards-compat shim — see telemetry.daily_report."""
from __future__ import annotations

import warnings

from tokenpak.telemetry.daily_report import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.daily_report is deprecated — import from tokenpak.telemetry.daily_report. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
