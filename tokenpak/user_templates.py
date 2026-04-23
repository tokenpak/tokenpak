"""Backwards-compat shim — see tokenpak.companion.templates.user_templates."""
from __future__ import annotations

import warnings

from tokenpak.companion.templates.user_templates import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.user_templates is deprecated — import from tokenpak.companion.templates.user_templates. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
