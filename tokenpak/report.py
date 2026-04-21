"""Backwards-compat shim — see telemetry.report."""
from __future__ import annotations
import warnings
from tokenpak.telemetry.report import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.report is deprecated — import from tokenpak.telemetry.report. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
