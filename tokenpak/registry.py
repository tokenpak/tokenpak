"""Backwards-compat shim — see tokenpak.core.registry."""
from __future__ import annotations
import warnings
from tokenpak.core.registry import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.registry is deprecated — import from tokenpak.core.registry. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
