"""Backwards-compat shim — see compression.span_extractor."""
from __future__ import annotations
import warnings
from tokenpak.compression.span_extractor import *  # noqa: F401,F403
warnings.warn(
    "tokenpak.span_extractor is deprecated — import from tokenpak.compression.span_extractor. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
