# SPDX-License-Identifier: Apache-2.0
"""TIP Spend Guard — proxy-side pre-send circuit breaker.

Blocks risky requests before they reach the upstream provider, holds them in
a TTL-bounded pending store, and replays only on explicit Yes/No approval or
an explicit ``[TIP: ...]`` directive.

Public surface:

    evaluate(body, model, session_id, headers, *, config=None) -> GuardOutcome
        Single entry point used by the proxy hook in ``proxy/server.py``.

    GuardOutcome
        Tagged-union result. Either ``forward_body`` (bytes to send upstream
        unchanged), ``block_response`` (bytes to return to the client now), or
        ``estimate_response`` (bytes carrying a RiskEstimate JSON for
        ``[TIP: estimate=on]`` requests).

The whole subsystem is config-driven via ``spend_guard.*`` keys
(see ``proxy/config.py``). ``spend_guard.enabled = false`` makes ``evaluate``
a no-op (returns ``GuardOutcome.passthrough()``).

Authority:
- Initiative: ``~/vault/01_PROJECTS/tokenpak/initiatives/2026-05-07-tip-spend-guard-oss/``
- Standard 29: agent contract for the structured block error.
- Pricing single source of truth: ``tokenpak.models.get_rates``.
"""

from __future__ import annotations

from ._context_window import get_model_max_context
from .contracts import (
    GuardOutcome,
    PendingRequest,
    PreflightDecision,
    RiskEstimate,
    TIPDirective,
)
from .estimator import estimate as estimate_request
from .policy import (
    DEFAULT_BLOCK_RATIO,
    derive_block_threshold,
)
from .policy import (
    decide as decide_policy,
)

__all__ = [
    "GuardOutcome",
    "PendingRequest",
    "PreflightDecision",
    "RiskEstimate",
    "TIPDirective",
    "estimate_request",
    "decide_policy",
    "derive_block_threshold",
    "DEFAULT_BLOCK_RATIO",
    "get_model_max_context",
    "evaluate",
]


def evaluate(
    body: bytes,
    model: str,
    session_id: str,
    headers: dict | None = None,
    *,
    config=None,
) -> "GuardOutcome":
    """Top-level guard entry point.

    Imported lazily by ``proxy/server.py`` to keep startup cheap and avoid
    circular imports. Fails open on any internal error: the proxy continues
    forwarding the original bytes.
    """
    # Lazy import — ``orchestrator`` pulls in pending/intent/replay/audit
    # which are heavier than estimator/policy alone.
    from .orchestrator import evaluate as _evaluate

    return _evaluate(body, model, session_id, headers or {}, config=config)
