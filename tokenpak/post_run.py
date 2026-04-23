"""Backwards-compat shim — see tokenpak.services.post_run."""
from __future__ import annotations

import warnings

from tokenpak.services.post_run import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.post_run is deprecated — import from tokenpak.services.post_run. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
