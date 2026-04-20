"""Backwards-compat shim — see compression.reference_scanner."""
from __future__ import annotations
import warnings
from tokenpak.compression.reference_scanner import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.reference_scanner is deprecated — import from tokenpak.compression.reference_scanner. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
