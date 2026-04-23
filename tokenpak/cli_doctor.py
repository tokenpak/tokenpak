"""Backwards-compat shim — see debug.doctor (renamed)."""
from __future__ import annotations

import warnings

from tokenpak.debug.doctor import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.cli_doctor is deprecated — import from tokenpak.debug.doctor "
    "(renamed for canonical §1 layout). Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
