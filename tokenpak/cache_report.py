"""Backwards-compat shim — see telemetry.cache_report."""
from __future__ import annotations

import warnings

from tokenpak.telemetry.cache_report import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.cache_report is deprecated — import from tokenpak.telemetry.cache_report. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
