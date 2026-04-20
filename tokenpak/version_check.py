"""Backwards-compat shim — see debug.version_check."""
from __future__ import annotations
import warnings
from tokenpak.debug.version_check import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.version_check is deprecated — import from tokenpak.debug.version_check. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
