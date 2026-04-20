"""Backwards-compat shim — see compression.precompute."""
from __future__ import annotations
import warnings
from tokenpak.compression.precompute import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.precompute is deprecated — import from tokenpak.compression.precompute. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
