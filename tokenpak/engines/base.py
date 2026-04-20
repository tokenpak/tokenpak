"""Backwards-compat shim — see compression.engines.base."""
from __future__ import annotations

from tokenpak.compression.engines.base import (  # noqa: F401
    CompactionEngine,
    CompactionHints,
)
