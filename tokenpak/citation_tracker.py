"""Backwards-compat shim — see compression.citation_tracker."""
from __future__ import annotations
import warnings
from tokenpak.compression.citation_tracker import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.citation_tracker is deprecated — import from tokenpak.compression.citation_tracker. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
