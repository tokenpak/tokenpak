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
import sqlite3
import time
from typing import Any

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


# Transient state-store lock contention (e.g. host IO stalls) is retried
# in-request before failing: everything ahead of the pending-store reads is
# idempotent, so re-running the full evaluation is safe. Sleeps are short —
# the client is holding an open request while we wait.
_LOCK_RETRY_BACKOFF_SEC = (0.5, 1.5)


def _is_transient_lock_error(exc: BaseException) -> bool:
    """True for SQLite lock/busy contention, false for corruption or bugs."""
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    msg = str(exc).lower()
    return "locked" in msg or "busy" in msg


def _state_busy_outcome(exc: Exception, retries: int) -> "GuardOutcome":
    """Structured block for transient lock contention — retryable, not a
    budget decision."""
    payload = {
        "error": {
            "type": "tokenpak_spend_guard_unavailable",
            "message": (
                "Spend Guard state store is temporarily busy (lock contention "
                f"persisted through {retries + 1} evaluation attempts). This "
                "is a local infrastructure fault, NOT a spend or budget "
                "block — no spend threshold was evaluated or exceeded. The "
                "request was blocked before provider send and is safe to "
                "retry as-is in a few seconds."
            ),
            "reason": "guard_state_busy",
            "failure_kind": "spend_guard_state_busy",
            "threshold_hit": f"state_busy:{type(exc).__name__}",
            "projected_input_tokens": None,
            "projected_output_tokens": None,
            "projected_cost_usd": None,
            "pending_id": None,
            "approval_prompt": None,
            "approval_prompt_available": False,
            "auto_proceed_available": False,
            "continuum_auto_proceed_available": False,
            "continuum_status": "not_active",
            "retryable": True,
            "retry_after_seconds": 5,
            "recovery_status": "retry_safe",
            "recovery_actions": [
                "retry the same request after retry_after_seconds",
                "if this recurs, check host IO pressure and run tokenpak doctor",
            ],
            "operator_note": (
                "Recurring guard_state_busy blocks indicate sustained IO "
                "contention on the guard state store's disk, not a Spend "
                "Guard policy problem."
            ),
        }
    }
    return GuardOutcome(
        kind="block",
        response_body=json.dumps(payload, separators=(",", ":")).encode("utf-8"),
        http_status=402,
        audit_event="fail_closed_state_busy",
    )


def _fail_closed_outcome(exc: Exception) -> "GuardOutcome":
    """Return a structured block when the guard cannot safely evaluate."""
    payload = {
        "error": {
            "type": "tokenpak_spend_guard_blocked",
            "message": (
                "Spend Guard hit an internal error and could not verify this "
                "request, so it was blocked before provider send (fail "
                "closed). This is a local guard-state fault, NOT a spend or "
                "budget block — no spend threshold was evaluated or "
                "exceeded. No allow/stop prompt is available because no "
                "pending request could be recorded."
            ),
            "reason": "spend_guard_state_unavailable",
            "failure_kind": "spend_guard_internal_error",
            "threshold_hit": f"internal_error:{type(exc).__name__}",
            "projected_input_tokens": None,
            "projected_output_tokens": None,
            "projected_cost_usd": None,
            "pending_id": None,
            "approval_prompt": None,
            "approval_prompt_available": False,
            "auto_proceed_available": False,
            "continuum_auto_proceed_available": False,
            "continuum_status": "not_active",
            "retryable": False,
            "recovery_status": "operator_action_required",
            "recovery_actions": [
                "run tokenpak doctor",
                "repair or restore the local Spend Guard state store",
                "restart the TokenPak proxy after repair",
            ],
            "operator_note": (
                "Disable Spend Guard only as an explicit operator-approved "
                "emergency; auto-proceed is unsafe while spend cannot be "
                "verified."
            ),
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
    headers: dict[str, Any] | None = None,
    *,
    config: Any = None,
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
    #
    # Transient lock contention on the guard state store gets bounded
    # in-request retries (the evaluation pipeline is idempotent up to the
    # point the lock can fire); anything else fails closed immediately.
    last_lock_exc: Exception | None = None
    for attempt, backoff in enumerate((*_LOCK_RETRY_BACKOFF_SEC, None)):
        try:
            from .orchestrator import evaluate as _evaluate

            return _evaluate(body, model, session_id, headers or {}, config=config)
        except Exception as exc:
            if not _is_transient_lock_error(exc):
                _log.warning(
                    "tokenpak.spend_guard: internal error (fail closed): %s: %s",
                    type(exc).__name__,
                    exc,
                )
                return _fail_closed_outcome(exc)
            last_lock_exc = exc
            if backoff is not None:
                _log.warning(
                    "tokenpak.spend_guard: state store busy (attempt %d, retrying in %.1fs): %s",
                    attempt + 1,
                    backoff,
                    exc,
                )
                time.sleep(backoff)

    assert last_lock_exc is not None
    _log.warning(
        "tokenpak.spend_guard: state store still busy after %d attempts (blocking retryable): %s",
        len(_LOCK_RETRY_BACKOFF_SEC) + 1,
        last_lock_exc,
    )
    return _state_busy_outcome(last_lock_exc, retries=len(_LOCK_RETRY_BACKOFF_SEC))
