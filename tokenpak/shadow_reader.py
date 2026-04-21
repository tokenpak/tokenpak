"""Backwards-compat shim — see routing.shadow_reader."""
from __future__ import annotations
import warnings
from tokenpak.routing.shadow_reader import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.shadow_reader is deprecated — import from tokenpak.routing.shadow_reader. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
