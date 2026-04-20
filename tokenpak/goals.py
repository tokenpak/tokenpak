"""Backwards-compat shim — see tokenpak.orchestration.goals."""
from __future__ import annotations
import warnings
from tokenpak.orchestration.goals import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.goals is deprecated — import from tokenpak.orchestration.goals. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
