"""Backwards-compat shim — see telemetry.request_ledger."""
from __future__ import annotations
import warnings
from tokenpak.telemetry.request_ledger import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.request_ledger is deprecated — import from tokenpak.telemetry.request_ledger. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
