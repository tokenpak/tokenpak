# SPDX-License-Identifier: Apache-2.0
"""Receipt v1 — canonical request-level proof object.

A :class:`RequestReceiptV1` explains a single TokenPak request: its route /
provider / model, projected-or-actual cost, the context that was reused vs.
dropped, the spend-guard decision, the optimization (savings) it produced, and a
redaction-safe pointer to its debug capture. It is the score-5 *proof* surface
for the request-level trust claims:

    C03  Unclear AI costs          -> cost block (tokens + estimated_cost_usd)
    C06  Context overload          -> context block (cache reuse + include/drop)
    C10  No clear request trail     -> trail block (session / agent / cycle / job)
    C11  No proof of optimization   -> optimization block (would_have_saved_tokens)
    C18  Hard debugging            -> debug_pointer block (trace id + capture mode)
    C21  Hidden repetition          -> context.cache_read_tokens + trail.agent_id

Honesty contract (the reason this object exists)
------------------------------------------------
Every datum is a :class:`ProofField`: it is either *proven* (``available=True``
with a value the runtime actually observed) or *explicitly unavailable*
(``available=False`` with a machine ``reason`` token). The receipt NEVER silently
omits a field and NEVER invents a number the system cannot prove — an
unobservable field is rendered as ``{"available": false, "reason": "..."}``, not
as a missing key and not as a guessed ``0``.

Not a parallel store
--------------------
Receipt v1 is a pure *projection* over proof TokenPak already records — the
per-request ``requests`` row surfaced by
:mod:`tokenpak.cli.request_explorer` (model, tokens, ``estimated_cost``,
``would_have_saved``, cache reuse, session / agent / dispatch ids) — composed
with the optional spend-guard :class:`~tokenpak.proxy.spend_guard.contracts.PreflightDecision`
and an optional debug-capture pointer. It introduces no new persistence and
makes no LLM/network call; it is fully deterministic given the same inputs.

The module deliberately declares ``__all__ = []``: Receipt v1 is a v1 surface
that may evolve, so it is intentionally not yet part of the frozen public-API
snapshot. Consumers import the names directly.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, is_dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping, Optional
from uuid import uuid4

SCHEMA_VERSION = "receipt.v1"

# Sentinel distinguishing "key genuinely absent from the record" (-> unavailable)
# from "key present with a falsy value such as 0 / 0.0" (-> proven). This is what
# keeps the cost block honest: a record that never carried a cost reads as
# explicitly unavailable, never as a fabricated ``0.0`` (AC-3).
_MISSING = object()


@dataclass
class ProofField:
    """A single proof datum: proven (a known value) or explicitly unavailable.

    Honesty contract (AC-3): a field the runtime cannot observe is represented as
    ``available=False`` with a machine ``reason`` token — never a guessed value,
    never a missing key.
    """

    available: bool
    value: Any = None
    reason: Optional[str] = None  # machine token; set iff available is False

    @classmethod
    def known(cls, value: Any) -> "ProofField":
        """A proven datum the runtime actually observed."""
        return cls(available=True, value=value)

    @classmethod
    def unavailable(cls, reason: str) -> "ProofField":
        """An explicitly-unavailable datum with a machine ``reason`` token."""
        return cls(available=False, value=None, reason=reason)

    def to_dict(self) -> dict:
        if self.available:
            return {"available": True, "value": self.value}
        return {"available": False, "reason": self.reason or "unavailable"}


# ---------------------------------------------------------------------------
# Receipt sub-blocks
# ---------------------------------------------------------------------------


@dataclass
class ReceiptRoute:
    """Where the request went — route / provider / model (C10)."""

    provider: ProofField
    model: ProofField
    endpoint: ProofField
    request_type: ProofField


@dataclass
class ReceiptCost:
    """What it cost — projected or actual (C03)."""

    input_tokens: ProofField
    output_tokens: ProofField
    estimated_cost_usd: ProofField


@dataclass
class ReceiptContext:
    """What context was reused vs. dropped (C06 / C21).

    ``cache_read_tokens`` / ``cache_creation_tokens`` are the *observed* reuse
    signal. ``included`` / ``dropped`` carry the per-reference include/drop proof
    when the context-selection layer captured it; until that proof is threaded
    through they are explicitly unavailable (``context_selection_not_captured``).
    """

    cache_read_tokens: ProofField
    cache_creation_tokens: ProofField
    included: ProofField
    dropped: ProofField


@dataclass
class ReceiptSpendGuard:
    """The spend-guard verdict for this request (from a PreflightDecision)."""

    decision: ProofField
    reason: ProofField
    requires_approval: ProofField
    threshold_hit: ProofField


@dataclass
class ReceiptOptimization:
    """Proof of optimization — what TokenPak saved (C11)."""

    would_have_saved_tokens: ProofField
    methods: ProofField


@dataclass
class ReceiptDebugPointer:
    """Redaction-safe pointer to the request's debug capture (C18).

    By construction this never embeds request/response plaintext — only the
    ``trace_id`` and the capture ``mode`` (``off`` / ``encrypted`` / ``hash_only``).
    ``path`` (the on-disk capture file, which reveals the OS user's home dir) is
    dropped when the receipt is rendered with ``redact=True``.
    """

    present: bool
    trace_id: Optional[str] = None
    capture_mode: Optional[str] = None
    path: Optional[str] = None

    def to_dict(self, *, redact: bool = True) -> dict:
        out: dict[str, Any] = {
            "present": self.present,
            "trace_id": self.trace_id,
            "capture_mode": self.capture_mode,
        }
        if not redact and self.path:
            out["path"] = self.path
        return out


@dataclass
class ReceiptTrail:
    """Request trail — who/what produced it (C10 / C21)."""

    session_id: ProofField
    agent_id: ProofField
    cycle_id: ProofField
    dispatch_job_id: ProofField


@dataclass
class RequestReceiptV1:
    """Canonical request-level proof object (schema ``receipt.v1``)."""

    receipt_id: str
    schema_version: str
    created_at: str
    request_id: ProofField
    status: ProofField
    route: ReceiptRoute
    cost: ReceiptCost
    context: ReceiptContext
    spend_guard: ReceiptSpendGuard
    optimization: ReceiptOptimization
    debug_pointer: ReceiptDebugPointer
    trail: ReceiptTrail

    def to_dict(self, *, redact: bool = True) -> dict:
        """Serialize to a plain dict. ``redact`` drops the debug capture path."""
        return _serialize(self, redact=redact)


def _serialize(obj: Any, *, redact: bool) -> Any:
    """Recursively serialize a receipt, honoring ProofField + redaction."""
    if isinstance(obj, ProofField):
        return obj.to_dict()
    if isinstance(obj, ReceiptDebugPointer):
        return obj.to_dict(redact=redact)
    if is_dataclass(obj) and not isinstance(obj, type):
        return {
            f: _serialize(getattr(obj, f), redact=redact)
            for f in obj.__dataclass_fields__
        }
    return obj


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _provider_from_model(model: str) -> Optional[str]:
    """Best-effort provider derivation from a model id. None when unknowable."""
    m = (model or "").lower()
    if not m:
        return None
    if m.startswith("claude") or "anthropic" in m:
        return "anthropic"
    if m.startswith(("gpt", "o1", "o3", "o4", "text-", "chatgpt")) or "openai" in m:
        return "openai"
    if m.startswith("gemini") or "google" in m:
        return "google"
    return None


def _field_from_record(
    record: Mapping[str, Any], *keys: str, missing_reason: str
) -> ProofField:
    """Pull the first present (non-None) key from ``record`` as a proven field.

    A key present with a falsy value (``0``, ``0.0``, ``""``) is still *proven*;
    only a genuinely-absent or ``None`` value yields an explicit unavailable.
    """
    for key in keys:
        value = record.get(key, _MISSING)
        if value is not _MISSING and value is not None:
            return ProofField.known(value)
    return ProofField.unavailable(missing_reason)


def _conservative_savings_field(record: Mapping[str, Any]) -> ProofField:
    """Surface the per-request optimization saving under conservative,
    proxy-only attribution — consistent with the canonical savings aggregate.

    The recorded ``would_have_saved`` is the per-request input-token reduction the
    proxy achieved by compressing the request body
    (``max(0, input_tokens - sent_input_tokens)``). The byte-preserved
    (client-cached) path skips compression, so by construction a *positive*
    ``would_have_saved`` implies the proxy compressed the body and the row is
    attributed ``cache_origin == 'proxy'``. The canonical savings aggregate
    (``status`` / ``cache_stats``) credits a saving only when
    ``cache_origin == 'proxy'`` — everything else (``'client'`` / ``'unknown'`` /
    absent) is not credited. This builder gates the *surfaced* per-request saving
    the same way so the receipt never publishes a saving the aggregate would not:

      * an absent saving stays explicit-unavailable (``savings_not_recorded``);
      * a recorded saving of 0 (or a non-positive value) is proven — an honest
        "no saving", not an over-claim;
      * a *positive* saving is proven only when proxy-attributable — ``cache_origin``
        absent (the conservative-by-construction invariant holds on real rows) or
        explicitly ``'proxy'``;
      * a *positive* saving on a row explicitly attributed to a non-proxy origin
        (e.g. ``'client'`` / ``'unknown'``) is marked **unavailable**
        (``savings_not_proxy_attributed``) — never the raw value, never a
        fabricated 0. This locks out any future regression that would record a
        non-proxy positive ``would_have_saved`` from leaking into a surfaced
        saving.

    Note: the production ``requests``-row projection
    (:mod:`tokenpak.cli.request_explorer`) does not yet surface ``cache_origin``,
    so on today's real rows this gate is a forward regression-lock rather than an
    active filter — the invariant already keeps the receipt conservative. Gating
    arbitrary records on genuine proxy attribution would require threading
    ``cache_origin`` through that projection (a separate change).
    """
    field = _field_from_record(
        record,
        "would_have_saved",
        "saved_cost",
        missing_reason="savings_not_recorded",
    )
    if not field.available:
        return field
    try:
        positive = float(field.value) > 0
    except (TypeError, ValueError):
        return field  # non-numeric recorded value: surface as-is, never reinterpret
    if not positive:
        return field  # 0 / negative carries no saving claim — proven, not over-claim
    origin = record.get("cache_origin")
    if origin is not None and origin != "proxy":
        return ProofField.unavailable("savings_not_proxy_attributed")
    return field


def build_request_receipt(
    record: Optional[Mapping[str, Any]] = None,
    *,
    receipt_id: Optional[str] = None,
    decision: Optional[Any] = None,
    context_included: Optional[list] = None,
    context_dropped: Optional[list] = None,
    optimization_methods: Optional[list] = None,
    debug_pointer: Optional[ReceiptDebugPointer] = None,
    clock: Optional[Callable[[], datetime]] = None,
) -> RequestReceiptV1:
    """Project a per-request monitor ``record`` into a Receipt v1 proof object.

    ``record`` is the ``requests``-row dict shape returned by
    :func:`tokenpak.cli.request_explorer.load_requests` /
    :func:`~tokenpak.cli.request_explorer.get_request_by_id`. It may be ``None``
    (e.g. a pre-send receipt built from a spend-guard ``decision`` alone, before
    any monitor row exists) — every unobservable field then reports an explicit
    unavailable reason rather than a fabricated value.

    ``decision`` is an optional spend-guard
    :class:`~tokenpak.proxy.spend_guard.contracts.PreflightDecision` (duck-typed):
    when supplied it populates the spend-guard block — and, when the record has
    no cost of its own, its attached :class:`RiskEstimate` backfills the model and
    projected cost. ``context_included`` / ``context_dropped`` /
    ``optimization_methods`` populate proof the monitor row does not carry; absent
    them those fields are explicitly unavailable. ``debug_pointer`` attaches the
    redaction-safe capture pointer (default: ``present=False``).
    """
    rec: Mapping[str, Any] = record or {}
    now = (clock or (lambda: datetime.now(timezone.utc)))()
    risk = getattr(decision, "risk", None)

    # --- identity -------------------------------------------------------
    request_id_field = _field_from_record(
        rec, "id", "request_id", missing_reason="request_id_not_recorded"
    )
    rid = request_id_field.value if request_id_field.available else None
    if receipt_id:
        rcpt_id = receipt_id
    elif rid:
        rcpt_id = f"rcpt_{rid}"
    else:
        rcpt_id = f"rcpt_{uuid4().hex}"

    # --- route ----------------------------------------------------------
    model_field = _field_from_record(rec, "model", missing_reason="model_not_recorded")
    if not model_field.available and risk is not None and getattr(risk, "model", None):
        model_field = ProofField.known(risk.model)
    model_str = model_field.value if model_field.available else ""
    provider = _provider_from_model(model_str)
    provider_field = (
        ProofField.known(provider)
        if provider
        else ProofField.unavailable("provider_not_derivable")
    )
    route = ReceiptRoute(
        provider=provider_field,
        model=model_field,
        endpoint=_field_from_record(
            rec, "endpoint", missing_reason="endpoint_not_recorded"
        ),
        request_type=_field_from_record(
            rec, "request_type", missing_reason="request_type_not_recorded"
        ),
    )

    # --- cost -----------------------------------------------------------
    cost_field = _field_from_record(
        rec, "estimated_cost", "cost", missing_reason="cost_not_recorded"
    )
    if not cost_field.available and risk is not None:
        projected = getattr(risk, "projected_cost_usd", None)
        if projected is not None:
            cost_field = ProofField.known(projected)
    cost = ReceiptCost(
        input_tokens=_field_from_record(
            rec, "input_tokens", missing_reason="input_tokens_not_recorded"
        ),
        output_tokens=_field_from_record(
            rec, "output_tokens", missing_reason="output_tokens_not_recorded"
        ),
        estimated_cost_usd=cost_field,
    )

    # --- context --------------------------------------------------------
    context = ReceiptContext(
        cache_read_tokens=_field_from_record(
            rec, "cache_read_tokens", "cache_read", missing_reason="cache_not_recorded"
        ),
        cache_creation_tokens=_field_from_record(
            rec, "cache_creation_tokens", missing_reason="cache_not_recorded"
        ),
        included=(
            ProofField.known(context_included)
            if context_included is not None
            else ProofField.unavailable("context_selection_not_captured")
        ),
        dropped=(
            ProofField.known(context_dropped)
            if context_dropped is not None
            else ProofField.unavailable("context_selection_not_captured")
        ),
    )

    # --- spend guard ----------------------------------------------------
    if decision is not None:
        spend_guard = ReceiptSpendGuard(
            decision=ProofField.known(getattr(decision, "decision", None)),
            reason=ProofField.known(getattr(decision, "reason", None)),
            requires_approval=ProofField.known(
                bool(getattr(decision, "requires_approval", False))
            ),
            threshold_hit=(
                ProofField.known(getattr(decision, "threshold_hit", None))
                if getattr(decision, "threshold_hit", None) is not None
                else ProofField.unavailable("no_threshold_hit")
            ),
        )
    else:
        spend_guard = ReceiptSpendGuard(
            decision=ProofField.unavailable("guard_decision_not_recorded"),
            reason=ProofField.unavailable("guard_decision_not_recorded"),
            requires_approval=ProofField.unavailable("guard_decision_not_recorded"),
            threshold_hit=ProofField.unavailable("guard_decision_not_recorded"),
        )

    # --- optimization ---------------------------------------------------
    # Conservative/proxy-only attribution: never surface a per-request saving the
    # canonical aggregate would not credit (see _conservative_savings_field).
    optimization = ReceiptOptimization(
        would_have_saved_tokens=_conservative_savings_field(rec),
        methods=(
            ProofField.known(optimization_methods)
            if optimization_methods is not None
            else ProofField.unavailable("optimization_methods_not_recorded")
        ),
    )

    # --- trail ----------------------------------------------------------
    trail = ReceiptTrail(
        session_id=_field_from_record(
            rec, "session_id", missing_reason="session_not_recorded"
        ),
        agent_id=_field_from_record(
            rec, "agent_id", "agent", missing_reason="agent_not_recorded"
        ),
        cycle_id=_field_from_record(
            rec, "cycle_id", missing_reason="cycle_not_recorded"
        ),
        dispatch_job_id=_field_from_record(
            rec, "dispatch_job_id", missing_reason="dispatch_job_not_recorded"
        ),
    )

    return RequestReceiptV1(
        receipt_id=rcpt_id,
        schema_version=SCHEMA_VERSION,
        created_at=now.isoformat(),
        request_id=request_id_field,
        status=_field_from_record(rec, "status", missing_reason="status_not_recorded"),
        route=route,
        cost=cost,
        context=context,
        spend_guard=spend_guard,
        optimization=optimization,
        debug_pointer=debug_pointer or ReceiptDebugPointer(present=False),
        trail=trail,
    )


def render_receipt(
    receipt: RequestReceiptV1, *, redact: bool = True, indent: int = 2
) -> str:
    """Render a receipt to a redaction-safe JSON string (``redact=True`` default)."""
    return json.dumps(receipt.to_dict(redact=redact), indent=indent, default=str)


__all__: list[str] = []
