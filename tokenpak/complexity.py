"""Backwards-compat shim — see routing.complexity."""
from __future__ import annotations
import warnings
from tokenpak.routing.complexity import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.complexity is deprecated — import from tokenpak.routing.complexity. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
