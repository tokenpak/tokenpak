"""Backwards-compat shim — see tokenpak.routing.profiles."""
from __future__ import annotations

import warnings

from tokenpak.routing.profiles import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.profiles is deprecated — import from tokenpak.routing.profiles. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
