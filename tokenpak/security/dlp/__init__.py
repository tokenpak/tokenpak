"""Data Loss Prevention — outbound secret scanning.

Single implementation, consumed via two paths:

1. :mod:`tokenpak.services.policy_service.dlp_stage` — the pipeline
   Stage that applies DLP to every request the proxy forwards.
   Gated by :attr:`tokenpak.core.routing.policy.Policy.dlp_mode`.

2. :mod:`tokenpak.companion.hooks.pre_send` (§5.2-C local helper) —
   may surface a pre-send warning to the user when a prompt looks like
   it contains secrets, independent of proxy-side handling.

No other subsystem reimplements secret detection — the rule set lives
in :mod:`.rules`, the scanner lives in :mod:`.scanner`, the mode
executors live in :mod:`.modes`.
"""

from __future__ import annotations

from tokenpak.security.dlp.modes import (
    DLPOutcome,
    apply_mode,
)
from tokenpak.security.dlp.rules import DEFAULT_RULES, Rule
from tokenpak.security.dlp.scanner import DLPScanner, Finding

__all__ = [
    "DLPScanner",
    "Finding",
    "Rule",
    "DEFAULT_RULES",
    "DLPOutcome",
    "apply_mode",
]
