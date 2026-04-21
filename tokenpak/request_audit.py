"""Backwards-compat shim — see telemetry.request_audit."""
from __future__ import annotations
import warnings
from tokenpak.telemetry.request_audit import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.request_audit is deprecated — import from tokenpak.telemetry.request_audit. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
