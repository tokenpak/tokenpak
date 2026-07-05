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
- Standard 29: agent contract for the structured block error.
- Pricing single source of truth: ``tokenpak.models.get_rates``.
"""

from __future__ import annotations

import json
import logging

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

_log = logging.getLogger(__name__)

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


def _fail_closed_outcome(exc: Exception) -> "GuardOutcome":
    """Return a structured block when the guard cannot safely evaluate."""
    payload = {
        "error": {
            "type": "tokenpak_spend_guard_blocked",
            "message": (
                "Spend Guard could not verify this request because its local "
                "guard state is unavailable. The request was blocked before "
                "provider send."
            ),
            "reason": "spend_guard_internal_error",
            "threshold_hit": f"internal_error:{type(exc).__name__}",
            "projected_input_tokens": None,
            "projected_output_tokens": None,
            "projected_cost_usd": None,
            "pending_id": None,
            "approval_prompt": (
                "Repair Spend Guard state, or explicitly disable Spend Guard "
                "only as an operator-approved emergency."
            ),
            "retryable": False,
            "recovery_status": "operator_action_required",
        }
    }
    return GuardOutcome(
        kind="block",
        response_body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        http_status=402,
        audit_event="fail_closed_internal_error",
    )


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
    circular imports. Internal guard failures fail closed with a structured
    402, so store corruption or other evaluator faults cannot silently forward
    provider-bound traffic while Spend Guard is enabled.
    """
    if config is not None and getattr(config, "enabled", True) is False:
        return GuardOutcome.passthrough(body)

    # Lazy import — ``orchestrator`` pulls in pending/intent/replay/audit
    # which are heavier than estimator/policy alone.
    try:
        from .orchestrator import evaluate as _evaluate

        return _evaluate(body, model, session_id, headers or {}, config=config)
    except Exception as exc:
        _log.warning(
            "tokenpak.spend_guard: internal error (fail closed): %s: %s",
            type(exc).__name__,
            exc,
        )
        return _fail_closed_outcome(exc)
