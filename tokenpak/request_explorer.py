"""Backwards-compat shim — see telemetry.request_explorer."""
from __future__ import annotations

import warnings

from tokenpak.telemetry.request_explorer import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.request_explorer is deprecated — import from tokenpak.telemetry.request_explorer. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
