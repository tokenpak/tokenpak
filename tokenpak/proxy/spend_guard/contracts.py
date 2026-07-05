# SPDX-License-Identifier: Apache-2.0
"""Spend Guard contracts (dataclasses) — the typed surface of the subsystem.

Kept minimal and free of internal dependencies so estimator/policy can run in
contexts that don't import the full proxy stack (e.g. unit tests, the
``[TIP: estimate=on]`` dry-run path, or future MCP/Pro consumers).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

# ---------------------------------------------------------------------------
# Estimator output
# ---------------------------------------------------------------------------

@dataclass
class RiskEstimate:
    """Projection of a single inbound request's cost.

    All token figures are *projected* totals for the upcoming provider call.
    ``current_context_tokens`` represents the running context the model will
    have after this request lands (cached + new).
    """

    model: str
    current_context_tokens: int
    request_tokens: int                 # uncached new content in this request
    projected_input_tokens: int         # current_context_tokens + request_tokens
    projected_output_tokens: int        # heuristic: max_tokens hint or default
    projected_cost_usd: float           # full input+output cost at model rates
    cache_hit_ratio: float              # 0.0..1.0 — fraction we expect cached
    rates: dict = field(default_factory=dict)  # {input, output, cached} per MTok


# ---------------------------------------------------------------------------
# Policy output
# ---------------------------------------------------------------------------

DecisionKind = Literal["allow", "warn", "block", "hard_block", "estimate_only", "cancel"]


@dataclass
class PreflightDecision:
    """Policy verdict on a RiskEstimate."""

    decision: DecisionKind
    reason: str                         # short machine token, e.g. ``projected_tokens_exceeded``
    requires_approval: bool             # True when caller can unblock with yes/[TIP]
    threshold_hit: Optional[str] = None  # the named threshold, for logging
    risk: Optional[RiskEstimate] = None


# ---------------------------------------------------------------------------
# Pending store
# ---------------------------------------------------------------------------

@dataclass
class PendingRequest:
    """A request held back from the provider, awaiting approval."""

    pending_id: str
    session_id: str
    created_at: float
    expires_at: float
    request_hash: str
    provider: str
    model: str
    projected_tokens: int
    projected_cost_usd: float
    raw_request_blob: bytes             # gzipped original body — replay verbatim
    raw_request_headers: dict           # forwarded as-is on replay (auth etc.)
    target_url: str                     # provider URL to replay to
    status: Literal["pending", "consumed", "discarded", "expired"] = "pending"


# ---------------------------------------------------------------------------
# TIP directive
# ---------------------------------------------------------------------------

@dataclass
class TIPDirective:
    """Parsed ``[TIP: ...]`` control directive.

    All fields default-empty; presence indicates the directive set them. The
    raw text is preserved for audit.
    """

    raw: str = ""
    allow_scope: Optional[Literal["once", "15m", "session"]] = None
    bypass: bool = False
    max_cost_usd: Optional[float] = None
    max_tokens: Optional[int] = None
    estimate_only: bool = False
    cancel: bool = False
    reason: Optional[str] = None
    unknown_keys: list = field(default_factory=list)  # for warning audit


# ---------------------------------------------------------------------------
# Guard outcome — what the orchestrator returns to proxy/server.py
# ---------------------------------------------------------------------------

OutcomeKind = Literal[
    "forward",          # forward_body to provider unchanged
    "forward_modified", # forward_body with TIP-stripped bytes
    "block",            # return block_response_body to client; no provider call
    "hard_block",       # like block but explicitly cannot be bypassed
    "replay",           # forward_body is the consumed pending blob
    "estimate",         # return estimate_response_body to client
    "cancel",           # return cancel_response_body to client; pending discarded
    "reprompt",         # return reprompt_response_body to client; pending kept
]


@dataclass
class GuardOutcome:
    """Tagged result returned by ``evaluate``.

    The proxy hook reads ``kind`` and acts accordingly:
    - ``forward`` / ``forward_modified`` / ``replay`` → write ``body`` upstream
    - ``block`` / ``hard_block`` / ``estimate`` / ``cancel`` / ``reprompt`` →
      write ``response_body`` to the client with ``http_status``
    """

    kind: OutcomeKind
    body: Optional[bytes] = None              # bytes to forward upstream (forward/replay)
    headers: Optional[dict] = None            # headers to forward (replay only — original)
    target_url: Optional[str] = None          # provider URL for replay
    response_body: Optional[bytes] = None     # JSON to return to client now
    http_status: int = 200
    decision: Optional[PreflightDecision] = None
    pending_id: Optional[str] = None
    audit_event: Optional[str] = None         # event_type for audit row
    # In-flight admission ticket (rolling-cap accounting). Present only on
    # forward outcomes that were admitted past the rolling caps; the proxy
    # settles it once the request's actual cost is recorded (or it fails).
    admission_ticket: Optional[str] = None

    @classmethod
    def passthrough(cls, body: bytes) -> "GuardOutcome":
        """Default no-op outcome — guard disabled or estimator allowed."""
        return cls(kind="forward", body=body)
