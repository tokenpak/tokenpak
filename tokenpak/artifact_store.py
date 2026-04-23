"""Backwards-compat shim — see tokenpak.vault.artifact_store."""
from __future__ import annotations

import warnings

from tokenpak.vault.artifact_store import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.artifact_store is deprecated — import from tokenpak.vault.artifact_store. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
