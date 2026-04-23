"""Backwards-compat shim — see tokenpak.orchestration.fleet."""
from __future__ import annotations

import warnings

from tokenpak.orchestration.fleet import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.fleet is deprecated — import from tokenpak.orchestration.fleet. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
