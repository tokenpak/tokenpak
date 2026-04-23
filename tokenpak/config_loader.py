"""Backwards-compat shim — see tokenpak.core.config.loader."""
from __future__ import annotations

import warnings

from tokenpak.core.config.loader import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.config_loader is deprecated — import from tokenpak.core.config.loader. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
