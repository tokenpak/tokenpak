"""Backwards-compat shim — see routing.elo."""
from __future__ import annotations
import warnings
from tokenpak.routing.elo import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.elo is deprecated — import from tokenpak.routing.elo. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
