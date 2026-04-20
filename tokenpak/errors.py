"""Backwards-compat shim — see tokenpak.core.errors."""
from __future__ import annotations
import warnings
from tokenpak.core.errors import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.errors is deprecated — import from tokenpak.core.errors. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
