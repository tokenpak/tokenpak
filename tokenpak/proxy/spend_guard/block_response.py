# SPDX-License-Identifier: Apache-2.0
"""Builders for the structured JSON responses the guard returns to clients.

Block / hard-block / cancel / re-prompt / estimate / replay-confirmation —
all keyed by ``error.type`` so agents can switch on it deterministically
(see Standard 29). HTTP status is always **402 Payment Required** for
blocks, with the JSON body carrying the recoverable-pause contract.
"""

from __future__ import annotations

__all__ = (
    "ERR_BLOCKED",
    "ERR_CANCELLED",
    "ERR_HARD_BLOCKED",
    "ERR_PENDING_WAITING",
    "ERR_REPROMPT",
    "ERR_ROLLING_CAP_BLOCKED",
    "HTTP_BLOCK",
    "HTTP_CANCELLED",
    "HTTP_ESTIMATE",
    "HTTP_HARD_BLOCK",
    "HTTP_PENDING_WAITING",
    "HTTP_REPROMPT",
    "INFO_ESTIMATE",
    "PendingRequest",
    "PreflightDecision",
    "RiskEstimate",
    "block",
    "block_store_unavailable",
    "build_rolling_cap_block",
    "cancelled",
    "estimate_only",
    "hard_block",
    "pending_waiting",
    "reprompt",
)


import json
from dataclasses import asdict
from typing import TYPE_CHECKING

from .contracts import PendingRequest, PreflightDecision, RiskEstimate

if TYPE_CHECKING:
    from .rolling_caps import CapBreach

# Stable error.type strings — agents key on these. Single source of truth.
ERR_BLOCKED = "tokenpak_spend_guard_blocked"
ERR_HARD_BLOCKED = "tokenpak_spend_guard_hard_blocked"
ERR_PENDING_WAITING = "tokenpak_spend_guard_pending"
ERR_CANCELLED = "tokenpak_spend_guard_cancelled"
ERR_REPROMPT = "tokenpak_spend_guard_reprompt"
ERR_ROLLING_CAP_BLOCKED = "tokenpak_spend_guard_rolling_cap_blocked"
INFO_ESTIMATE = "tokenpak_spend_guard_estimate"

# HTTP status — 402 Payment Required best-fits "request requires
# authorization to proceed". 429 (Too Many Requests) was considered but
# implies retry-with-backoff; 402 implies "user action needed", which is
# the correct contract.
HTTP_BLOCK = 402
HTTP_HARD_BLOCK = 402
HTTP_PENDING_WAITING = 402
HTTP_CANCELLED = 200  # cancellation is a successful resolution
HTTP_REPROMPT = 402
HTTP_ESTIMATE = 200


def block(decision: PreflightDecision, pending: PendingRequest) -> bytes:
    """Initial block response — caller should approve via Yes/[TIP] or cancel."""
    risk = decision.risk
    payload = {
        "error": {
            "type": ERR_BLOCKED,
            "message": (
                "TIP Spend Guard blocked this request before provider send. "
                "Reply 'yes' to proceed, 'no' to cancel, or prepend "
                "'[TIP: allow=once]' to bypass."
            ),
            "reason": decision.reason,
            "threshold_hit": decision.threshold_hit,
            "projected_input_tokens": risk.projected_input_tokens if risk else None,
            "projected_output_tokens": risk.projected_output_tokens if risk else None,
            "projected_cost_usd": risk.projected_cost_usd if risk else None,
            "cache_hit_ratio": risk.cache_hit_ratio if risk else None,
            "model": risk.model if risk else None,
            "pending_id": pending.pending_id,
            "expires_at": pending.expires_at,
            "approval_prompt": "Proceed? Yes / No",
            "retryable": True,  # client may retry after approval
            "recovery_status": "user_action_required",
        }
    }
    return json.dumps(payload).encode()


def hard_block(decision: PreflightDecision) -> bytes:
    """Hard-block — cannot be released by the user."""
    risk = decision.risk
    payload = {
        "error": {
            "type": ERR_HARD_BLOCKED,
            "message": (
                "TIP Spend Guard hard-blocked this request. The projected "
                "cost or token count exceeds the immutable hard-block ceiling."
            ),
            "reason": decision.reason,
            "threshold_hit": decision.threshold_hit,
            "projected_input_tokens": risk.projected_input_tokens if risk else None,
            "projected_output_tokens": risk.projected_output_tokens if risk else None,
            "projected_cost_usd": risk.projected_cost_usd if risk else None,
            "model": risk.model if risk else None,
            "retryable": False,
            "recovery_status": "terminally_blocked",
        }
    }
    return json.dumps(payload).encode()


def block_store_unavailable(decision: PreflightDecision) -> bytes:
    """Block response for when the policy decided BLOCK but the pending
    store could not persist the held request (guard state DB unwritable).

    The request is still blocked — a store failure must never downgrade a
    block into a forward — but reply-to-approve is unavailable because no
    pending row exists to replay.
    """
    risk = decision.risk
    payload = {
        "error": {
            "type": ERR_BLOCKED,
            "message": (
                "TIP Spend Guard blocked this request before provider send, "
                "but could not persist it for later approval (the guard "
                "state store is unavailable). Reply-to-approve is not "
                "possible for this request. Repair the guard state store, "
                "or prepend '[TIP: allow=once]' to bypass once."
            ),
            "reason": decision.reason,
            "threshold_hit": decision.threshold_hit,
            "projected_input_tokens": risk.projected_input_tokens if risk else None,
            "projected_output_tokens": risk.projected_output_tokens if risk else None,
            "projected_cost_usd": risk.projected_cost_usd if risk else None,
            "cache_hit_ratio": risk.cache_hit_ratio if risk else None,
            "model": risk.model if risk else None,
            "pending_id": None,
            "approval_prompt": None,
            "retryable": False,
            "recovery_status": "operator_action_required",
        }
    }
    return json.dumps(payload).encode()


def pending_waiting(pending: PendingRequest) -> bytes:
    """Subsequent request from a session that already has a pending block."""
    payload = {
        "error": {
            "type": ERR_PENDING_WAITING,
            "message": (
                "A previous request from this session is held by the Spend "
                "Guard awaiting approval. Reply 'yes' to proceed, 'no' to "
                "cancel, or '[TIP: cancel]' to discard."
            ),
            "pending_id": pending.pending_id,
            "expires_at": pending.expires_at,
            "projected_cost_usd": pending.projected_cost_usd,
            "projected_tokens": pending.projected_tokens,
            "retryable": True,
            "recovery_status": "user_action_required",
        }
    }
    return json.dumps(payload).encode()


def cancelled(pending: PendingRequest) -> bytes:
    """User said no — cancellation acknowledgment."""
    payload = {
        "spend_guard": {
            "type": ERR_CANCELLED,
            "message": "Pending request cancelled. Session unblocked.",
            "pending_id": pending.pending_id,
            "projected_cost_avoided_usd": pending.projected_cost_usd,
        }
    }
    return json.dumps(payload).encode()


def reprompt(pending: PendingRequest) -> bytes:
    """Ambiguous reply — re-prompt user."""
    payload = {
        "error": {
            "type": ERR_REPROMPT,
            "message": "Could not parse approval intent. Reply yes or no.",
            "pending_id": pending.pending_id,
            "approval_prompt": "Proceed? Yes / No",
            "retryable": True,
            "recovery_status": "user_action_required",
        }
    }
    return json.dumps(payload).encode()


def estimate_only(risk: RiskEstimate) -> bytes:
    """[TIP: estimate=on] — return the risk estimate without forwarding."""
    payload = {
        "spend_guard": {
            "type": INFO_ESTIMATE,
            "message": "Risk estimate (no provider call performed).",
            "estimate": asdict(risk),
        }
    }
    return json.dumps(payload).encode()


def build_rolling_cap_block(breach: CapBreach) -> bytes:
    """Build the JSON response body for a rolling-cap block.

    `breach` is a :class:`rolling_caps.CapBreach` dataclass instance.
    Returns the structured 402 body bytes; the caller wraps the HTTP
    status and headers.

    Attribution clarity: for **per_fleet** breaches, ``agent_id`` is the
    *triggering caller* (the request that tripped the cap), and ``used`` is the
    **fleet-wide aggregate** across all tagged callers in the window — NOT the
    triggering caller's own spend. The legacy ``(agent=X, used=$)`` wording was
    routinely misread as "caller X spent $" and cost diagnostic time. The
    message + body below are dimension-aware so an operator reads it correctly
    once: ``triggered_by`` always names the caller; ``fleet_used``/``fleet_cap``
    carry the aggregate for fleet-wide breaches. Legacy fields (``agent_id``,
    ``used``, ``cap``, ``projected_add``) are retained unchanged for backward
    compatibility.
    """
    is_fleet = str(breach.cap_dimension).startswith("per_fleet")
    is_unmeasurable = str(breach.cap_dimension) == "rolling_cap_unmeasurable"
    scope = "fleet" if is_fleet else "agent"
    if is_unmeasurable:
        attribution = (
            "rolling usage could not be measured: the usage database exists "
            "but is unreadable (locked, corrupt, or permission-denied). "
            "Blocking before provider send because caps cannot be verified."
        )
    elif is_fleet:
        attribution = (
            f"triggered_by={breach.agent_id} (this caller tripped the cap; it is "
            f"NOT necessarily the biggest spender). fleet_used={breach.used:.4g}, "
            f"fleet_cap={breach.cap:.4g}, would_add={breach.projected_add:.4g}, "
            f"window={breach.window_seconds}s. fleet_used is the SUM of all tagged "
            f"agents in the window, not {breach.agent_id} alone."
        )
    else:
        attribution = (
            f"agent={breach.agent_id} used={breach.used:.4g} of its own cap="
            f"{breach.cap:.4g} (this IS {breach.agent_id}'s rolling usage), "
            f"would_add={breach.projected_add:.4g}, window={breach.window_seconds}s."
        )
    if is_unmeasurable:
        message = (
            "TIP Spend Guard blocked this request: rolling_cap_unmeasurable. "
            f"{attribution} "
            "Operator action required: repair (or remove) the usage database "
            "so rolling usage can be measured again. Prepend "
            "'[TIP: allow=once]' only as an explicit operator-approved bypass."
        )
    else:
        message = (
            f"TIP Spend Guard rolling cap exceeded: {breach.cap_dimension} [{scope}]. "
            f"{attribution} "
            "Reply 'yes' or prepend '[TIP: allow=once]' to bypass; "
            "wait ~30 min for usage to age out, or operator may raise "
            "the cap in spend_guard.rolling_caps."
        )
    payload = {
        "error": {
            "type": ERR_ROLLING_CAP_BLOCKED,
            "message": message,
            # --- attribution-clear fields ---
            "scope": scope,  # "fleet" | "agent"
            "triggered_by": breach.agent_id,  # the caller that tripped the cap
            "fleet_used": breach.used if is_fleet else None,
            "fleet_cap": breach.cap if is_fleet else None,
            "window_seconds": breach.window_seconds,
            # --- backward-compatible legacy fields (DO NOT remove) ---
            "cap_dimension": breach.cap_dimension,
            "agent_id": breach.agent_id,
            "used": breach.used,
            "cap": breach.cap,
            "projected_add": breach.projected_add,
            "retry_after_seconds": breach.retry_after_seconds,
            "bypass_directive": "[TIP: allow=once]",
        }
    }
    # Optional per-agent breakdown — included only when the breach carries it
    # (top-N by spend in the window). Full population is a separate slice.
    contributing = getattr(breach, "contributing_agents", None)
    if contributing:
        payload["error"]["contributing_agents"] = contributing
    return json.dumps(payload, separators=(",", ":")).encode("utf-8")
