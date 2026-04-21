"""Backwards-compat shim — see routing.shadow_hook."""
from __future__ import annotations
import warnings
from tokenpak.routing.shadow_hook import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.shadow_hook is deprecated — import from tokenpak.routing.shadow_hook. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
