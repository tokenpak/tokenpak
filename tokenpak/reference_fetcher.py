"""Backwards-compat shim — see compression.reference_fetcher."""
from __future__ import annotations

import warnings

from tokenpak.compression.reference_fetcher import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.reference_fetcher is deprecated — import from tokenpak.compression.reference_fetcher. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
