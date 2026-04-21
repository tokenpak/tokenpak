"""Backwards-compat shim — see tokenpak.telemetry.tokens."""
from __future__ import annotations
import warnings
from tokenpak.telemetry.tokens import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.tokens is deprecated — import from tokenpak.telemetry.tokens. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
