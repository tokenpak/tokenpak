"""Backwards-compat shim — see services.policy_service.budget.controller."""
from __future__ import annotations

import warnings

from tokenpak.services.policy_service.budget.controller import *  # noqa: F401,F403

warnings.warn(
    "tokenpak.budget_controller is deprecated — "
    "import from tokenpak.services.policy_service.budget.controller. "
    "Removal target: TIP-2.0.",
    DeprecationWarning, stacklevel=2,
)
