"""Backwards-compat shim — see debug.calibrator."""
from __future__ import annotations

import warnings

from tokenpak.debug.calibrator import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.calibrator is deprecated — import from tokenpak.debug.calibrator. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
