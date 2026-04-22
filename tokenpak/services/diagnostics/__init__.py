"""Shared diagnostic checks — single implementation consumed by:

- ``tokenpak doctor`` + ``tokenpak doctor --claude-code`` (cli entrypoint)
- CI health probes
- ``tokenpak integrate claude-code`` post-install verifier

Every check returns a :class:`CheckResult` so the caller can group
results by severity and render them uniformly. No check writes to
stdout/stderr directly — the caller decides the presentation layer.

Canonical location per the 1.3.0 architecture map. Replaces any ad-hoc
diagnostic code duplicated across CLI + installer + CI.
"""

from __future__ import annotations

from tokenpak.services.diagnostics.checks import (
    CheckResult,
    CheckStatus,
    run_claude_code_checks,
    run_core_checks,
)
from tokenpak.services.diagnostics.drift import (
    DriftReport,
    detect_install_drift,
)

__all__ = [
    "CheckResult",
    "CheckStatus",
    "DriftReport",
    "detect_install_drift",
    "run_claude_code_checks",
    "run_core_checks",
]
