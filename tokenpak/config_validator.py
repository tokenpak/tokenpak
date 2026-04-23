"""Backwards-compat shim — see tokenpak.core.config.validator."""
from __future__ import annotations

import warnings

from tokenpak.core.config.validator import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.config_validator is deprecated — import from tokenpak.core.config.validator. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
