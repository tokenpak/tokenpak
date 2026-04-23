"""Backwards-compat shim — see compression.miss_detector."""
from __future__ import annotations

import warnings

from tokenpak.compression.miss_detector import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.miss_detector is deprecated — import from tokenpak.compression.miss_detector. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
