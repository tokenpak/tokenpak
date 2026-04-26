# SPDX-License-Identifier: Apache-2.0
"""Phase 2.4.1 — PolicySuggestion dataclass + pure-function builder.

Builds zero or more :class:`PolicySuggestion` objects from a
Phase 2.1 :class:`PolicyDecision` plus its :class:`IntentContract`,
config, and adapter context. Pure function; no I/O. Side-channel
for storage is :mod:`tokenpak.proxy.intent_suggestion_telemetry`.

Phase 2.4.1 scope per the Phase 2.4 spec §10:

  - Build suggestions; write them to a new ``intent_suggestions``
    table.
  - **No render path changes** (CLI / dashboard / API surfaces are
    Phase 2.4.2 work).
  - Default config (``mode=observe_only``) means suggestions are
    written only when the underlying decision is itself a
    ``suggest_*`` or ``warn_only/missing_slots`` decision; the
    operator opts in to *displaying* the suggestions in 2.4.3.

Privacy contract (asserted in tests):

  - Builder reads structured fields only (intent class, slot
    tuples, provider/model slugs, capability frozensets).
  - All emitted strings (``title``, ``message``,
    ``recommended_action``) are built from a fixed template table.
    No caller-supplied substring ever reaches a suggestion field.
  - The ``message`` template includes the explicit "DRY-RUN /
    PREVIEW ONLY" disclaimer required by Phase 2.4 spec §8.4.
  - Forbidden-phrase guardrail: emitted strings are scanned for
    the §8.2 forbidden-wording list; a hit is a hard error
    (raises :class:`SuggestionWordingError`) so a future renderer
    or template change can never ship language that implies the
    routing already happened.

Eligibility gates (Phase 2.4 spec §4) — applied in order:

  (a) confidence >= low_confidence_threshold
  (b) catch_all_reason is None
  (c) no required slot in slots_missing
      (constructive type emitted instead when this fails)
  (d) recommended_provider live_verified or
      allow_unverified_providers=true
  (e) adapter is non-None and has a capability declaration
  (f) §4.3 wire-emission gate — applied only at the render layer
      (Phase 2.4.2+); the builder writes the suggestion regardless,
      with ``user_visible`` reflecting the host's
      ``suggestion_surface`` config (default False until 2.4.3).
  (g) decision_reason is in EXPLAINABLE_REASONS
"""

from __future__ import annotations

import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from tokenpak.proxy.intent_contract import IntentContract
from tokenpak.proxy.intent_policy_engine import (
    ACTION_FLAG_BUDGET_RISK,
    ACTION_SUGGEST_CACHE_POLICY,
    ACTION_SUGGEST_COMPRESSION_PROFILE,
    ACTION_SUGGEST_DELIVERY_POLICY,
    ACTION_SUGGEST_ROUTE,
    ACTION_WARN_ONLY,
    REASON_DRY_RUN_SUGGEST,
    SAFETY_MISSING_SLOTS,
    PolicyDecision,
    PolicyEngineConfig,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


# Seven suggestion types per Phase 2.4 spec §5.
SUGGESTION_PROVIDER_MODEL = "provider_model_recommendation"
SUGGESTION_COMPRESSION = "compression_profile_recommendation"
SUGGESTION_CACHE = "cache_policy_recommendation"
SUGGESTION_DELIVERY = "delivery_strategy_recommendation"
SUGGESTION_BUDGET_WARNING = "budget_warning"
SUGGESTION_MISSING_SLOT = "missing_slot_improvement"
SUGGESTION_ADAPTER_CAPABILITY = "adapter_capability_recommendation"

SUGGESTION_TYPES: FrozenSet[str] = frozenset({
    SUGGESTION_PROVIDER_MODEL,
    SUGGESTION_COMPRESSION,
    SUGGESTION_CACHE,
    SUGGESTION_DELIVERY,
    SUGGESTION_BUDGET_WARNING,
    SUGGESTION_MISSING_SLOT,
    SUGGESTION_ADAPTER_CAPABILITY,
})


# Phase 2.4 spec §4 rule (g) — explainable decision reasons. A
# decision_reason outside this set produces no suggestion. Forward-
# compatible: when 2.4.3 adds class_rule_matched, that string
# joins the set.
EXPLAINABLE_REASONS: FrozenSet[str] = frozenset({
    REASON_DRY_RUN_SUGGEST,
})


# Phase 2.4 spec §8.2 forbidden phrases. Matched case-insensitively
# as whole words (or phrases). A regex assembled from this list
# scans every emitted string at build time; a hit raises
# SuggestionWordingError so a misconfigured template can never ship.
FORBIDDEN_PHRASES: Tuple[str, ...] = (
    "applied",
    "changed",
    "routed to",
    "switched to",
    "now using",
    "updated",
    "will route",
    "will switch",
)
_FORBIDDEN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in FORBIDDEN_PHRASES) + r")\b",
    re.IGNORECASE,
)


# Phase 2.4 spec §8.4 — every emitted string adjacent to a
# suggestion MUST include the dry-run disclaimer in plain text. The
# Phase 2.4.1 ``message`` template embeds this; tests assert
# presence.
DRY_RUN_DISCLAIMER: str = "DRY-RUN / PREVIEW ONLY"


# Source pinned for the entire 2.4.x line per spec §6.
SOURCE_INTENT_POLICY_V0: str = "intent_policy_v0"


class SuggestionWordingError(Exception):
    """Raised when a built suggestion string contains forbidden wording.

    Hard fail at build time so a misconfigured template, accidental
    f-string interpolation, or future renderer change can never
    surface "Applied" / "Changed" / "Routed to" / etc. to the user.
    """


# ---------------------------------------------------------------------------
# PolicySuggestion shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicySuggestion:
    """Operator-visible recommendation derived from a PolicyDecision.

    Field set is the Phase 2.4 spec §6 wire contract verbatim.
    JSON consumers may rely on every field's presence.
    """

    suggestion_id: str
    decision_id: str
    contract_id: str
    suggestion_type: str
    title: str
    message: str
    recommended_action: Optional[str]
    confidence: float
    safety_flags: Tuple[str, ...]
    requires_confirmation: bool
    user_visible: bool
    expires_at: Optional[str]
    source: str = SOURCE_INTENT_POLICY_V0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "suggestion_id": self.suggestion_id,
            "decision_id": self.decision_id,
            "contract_id": self.contract_id,
            "suggestion_type": self.suggestion_type,
            "title": self.title,
            "message": self.message,
            "recommended_action": self.recommended_action,
            "confidence": self.confidence,
            "safety_flags": list(self.safety_flags),
            "requires_confirmation": self.requires_confirmation,
            "user_visible": self.user_visible,
            "expires_at": self.expires_at,
            "source": self.source,
        }


def make_suggestion_id() -> str:
    """29-char hex ID (13 ms timestamp + 16 random) — sortable.

    Mirrors :func:`tokenpak.proxy.intent_policy_engine.make_decision_id`
    so consumers can sort suggestion + decision ids together.
    """
    ts_ms = int(time.time() * 1000)
    rand = secrets.token_hex(8)
    return f"{ts_ms:013x}{rand}"


# ---------------------------------------------------------------------------
# Wording templates (Phase 2.4 spec §8)
# ---------------------------------------------------------------------------


# Reason-rendering — maps decision_reason → human-readable phrase.
# Phase 2.4.1 covers only ``dry_run_suggest``; future reasons get
# entries here without changing the builder.
_REASON_RENDER: Dict[str, str] = {
    REASON_DRY_RUN_SUGGEST: "the canonical heuristic table recommends it",
}


def _render_reason(decision_reason: str) -> str:
    """Map a decision_reason to a plain-English clause.

    Returns ``""`` for unmapped reasons; eligibility rule (g)
    rejects the suggestion at the gate before this is ever called
    in production, so the empty fallback only matters in tests.
    """
    return _REASON_RENDER.get(decision_reason, "")


def _check_wording(*texts: str) -> None:
    """Scan emitted strings for §8.2 forbidden phrases.

    Hard-fail so a future template change or rendering bug never
    surfaces user-facing language that implies routing already
    happened. Skip ``None`` values (used for nullable fields like
    ``recommended_action``).
    """
    for text in texts:
        if text is None:
            continue
        m = _FORBIDDEN_RE.search(text)
        if m is not None:
            raise SuggestionWordingError(
                f"forbidden wording {m.group(0)!r} in suggestion text: {text!r}"
            )


def _build_text(
    suggestion_type: str,
    *,
    intent_class: str,
    decision: PolicyDecision,
    extra: Optional[Dict[str, str]] = None,
) -> Tuple[str, str, Optional[str]]:
    """Construct (title, message, recommended_action) from a fixed template.

    All inputs are TIP-controlled identifiers (canonical-intent
    keywords, profile names, provider slugs). No caller-supplied
    free-form strings reach the template.
    """
    extra = extra or {}
    reason = _render_reason(decision.decision_reason) or "the engine's heuristic table"

    if suggestion_type == SUGGESTION_COMPRESSION:
        profile = decision.compression_profile or "aggressive"
        title = f"Recommended: {profile} compression for this {intent_class} request"
        message = (
            f"Could apply {profile} compression for this {intent_class} request "
            f"because {reason}. {DRY_RUN_DISCLAIMER} — no compression has been "
            f"recommended into the dispatch path."
        )
        action = f"Consider applying {profile} compression"

    elif suggestion_type == SUGGESTION_CACHE:
        strategy = decision.cache_strategy or "proxy_managed"
        title = f"Recommended: {strategy} cache strategy"
        message = (
            f"Could enable {strategy} cache for this {intent_class} request "
            f"because {reason}. {DRY_RUN_DISCLAIMER} — no cache strategy has "
            f"been recommended into the dispatch path."
        )
        action = f"Consider enabling {strategy} cache"

    elif suggestion_type == SUGGESTION_DELIVERY:
        strategy = decision.delivery_strategy or "non_streaming"
        title = f"Recommended: {strategy} delivery for this {intent_class} request"
        message = (
            f"Could request {strategy} delivery for this {intent_class} request "
            f"because {reason}. {DRY_RUN_DISCLAIMER} — no delivery strategy "
            f"has been recommended into the dispatch path."
        )
        action = f"Consider {strategy} delivery"

    elif suggestion_type == SUGGESTION_PROVIDER_MODEL:
        provider = decision.recommended_provider or "(unknown)"
        model = decision.recommended_model or "(unknown)"
        title = f"Recommended: route this {intent_class} request to {provider}"
        message = (
            f"Could route this {intent_class} request to {provider} / {model} "
            f"because {reason}. {DRY_RUN_DISCLAIMER} — the request still "
            f"flows to the caller's declared provider."
        )
        action = f"Consider routing to {provider} / {model}"

    elif suggestion_type == SUGGESTION_BUDGET_WARNING:
        title = "Budget risk: this request approaches a soft cap"
        message = (
            f"This {intent_class} request was flagged for budget risk because "
            f"{reason}. {DRY_RUN_DISCLAIMER} — no budget cap has been "
            f"enforced; this is an advisory warning only."
        )
        action = None  # observation only — no action recommended

    elif suggestion_type == SUGGESTION_MISSING_SLOT:
        slot_list = ", ".join(extra.get("missing_slots", "").split(","))
        title = f"Could improve: missing slot(s) for this {intent_class} request"
        message = (
            f"Adding the missing slot(s) ({slot_list}) to this {intent_class} "
            f"request would unlock the safety gate and surface routing "
            f"recommendations. {DRY_RUN_DISCLAIMER} — no routing decision "
            f"is being made."
        )
        action = f"Consider adding slot(s): {slot_list}"

    elif suggestion_type == SUGGESTION_ADAPTER_CAPABILITY:
        cap = extra.get("missing_capability", "tip.compression.v1")
        title = f"Adapter could declare {cap}"
        message = (
            f"The resolved adapter for this {intent_class} request does not "
            f"declare {cap}. Declaring it would unlock the matching engine "
            f"recommendation. {DRY_RUN_DISCLAIMER} — no adapter modification "
            f"has occurred."
        )
        action = f"Consider declaring {cap} on the adapter"

    else:  # pragma: no cover — unknown type guarded earlier
        raise SuggestionWordingError(f"unknown suggestion_type: {suggestion_type!r}")

    _check_wording(title, message, action)
    return title, message, action


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SuggestionBuilderContext:
    """Bundle of facts the builder reads beyond decision/contract.

    Mirrors Phase 2.4 spec §4 eligibility-gate inputs.
    """

    config: PolicyEngineConfig
    adapter_capabilities: FrozenSet[str] = field(default_factory=frozenset)
    provider_verified: Optional[bool] = None  # None = unknown
    required_slots: Tuple[str, ...] = field(default_factory=tuple)


def build_suggestions(
    *,
    decision: PolicyDecision,
    contract: IntentContract,
    ctx: SuggestionBuilderContext,
) -> List[PolicySuggestion]:
    """Build zero or more :class:`PolicySuggestion` objects.

    Pure function — no I/O. Returns ``[]`` when the decision is
    not eligible per the §4 gates. The caller persists the
    returned list via :mod:`intent_suggestion_telemetry`.
    """
    cfg = ctx.config

    # Rule (e) — adapter is non-None and has a capability set.
    # An empty frozenset is OK in 2.4.1 since it's the default for
    # PassthroughAdapter (functional, no opt-in features). The gate
    # blocks ``None`` only — caller passes ``frozenset()`` for
    # passthrough.
    # Implemented at the call site (server.py); the builder is
    # given the resolved capabilities, so a None adapter would
    # never reach here.

    # Rule (b) — catch-all blocks routing-affecting suggestions.
    if contract.catch_all_reason is not None:
        return []

    # Rule (a) — low confidence blocks routing-affecting suggestions.
    if contract.confidence < cfg.low_confidence_threshold:
        return []

    # Rule (c) — missing required slots: emit the constructive
    # type ``missing_slot_improvement`` when the decision is
    # warn_only with the missing_slots safety flag, but suppress
    # any other suggestion type.
    if _has_missing_required_slots(contract, ctx.required_slots):
        return _build_missing_slot_suggestions(decision, contract, ctx)

    # Rule (d) — unverified provider blocks provider/model recs.
    if (
        decision.action == ACTION_SUGGEST_ROUTE
        and ctx.provider_verified is False
        and not cfg.allow_unverified_providers
    ):
        return []

    # Rule (g) — explainable reason gate.
    if decision.decision_reason not in EXPLAINABLE_REASONS:
        return []

    # warn_only without a constructive mapping (low_confidence,
    # catch_all, unverified) produces no suggestion. Multi-flag
    # warns also produce nothing — operator panel covers those.
    if decision.action == ACTION_WARN_ONLY:
        return []

    # Determine the suggestion type from the decision action.
    # Capability-aware fallback: when the engine emits a suggest_*
    # for compression/cache/delivery but the adapter doesn't
    # declare the corresponding TIP capability, emit
    # ``adapter_capability_recommendation`` instead (Phase 2.4
    # spec §5 constructive type).
    suggestion_type, extra = _select_type(decision, ctx.adapter_capabilities)
    if suggestion_type is None:
        return []

    title, message, action = _build_text(
        suggestion_type,
        intent_class=contract.intent_class,
        decision=decision,
        extra=extra,
    )
    return [
        PolicySuggestion(
            suggestion_id=make_suggestion_id(),
            decision_id=decision.decision_id,
            contract_id=contract.contract_id,
            suggestion_type=suggestion_type,
            title=title,
            message=message,
            recommended_action=action,
            confidence=contract.confidence,
            safety_flags=tuple(decision.safety_flags),
            requires_confirmation=False,  # always False in 2.4.1
            user_visible=False,  # 2.4.3 wires the surface flags
            expires_at=None,
        )
    ]


def _has_missing_required_slots(
    contract: IntentContract, required_slots: Tuple[str, ...]
) -> bool:
    if not required_slots:
        return False
    missing = set(contract.slots_missing)
    return any(s in missing for s in required_slots)


def _build_missing_slot_suggestions(
    decision: PolicyDecision,
    contract: IntentContract,
    ctx: SuggestionBuilderContext,
) -> List[PolicySuggestion]:
    """Emit the constructive missing_slot_improvement suggestion.

    Fires only when the decision is ``warn_only`` with safety_flags
    exactly ``("missing_slots",)`` — i.e. a clean signal that the
    only thing wrong is the slot. Multi-flag warns produce no
    suggestion (the operator panel covers those).
    """
    if decision.action != ACTION_WARN_ONLY:
        return []
    if tuple(decision.safety_flags) != (SAFETY_MISSING_SLOTS,):
        return []

    missing = sorted(set(ctx.required_slots) & set(contract.slots_missing))
    title, message, action = _build_text(
        SUGGESTION_MISSING_SLOT,
        intent_class=contract.intent_class,
        decision=decision,
        extra={"missing_slots": ",".join(missing)},
    )
    return [
        PolicySuggestion(
            suggestion_id=make_suggestion_id(),
            decision_id=decision.decision_id,
            contract_id=contract.contract_id,
            suggestion_type=SUGGESTION_MISSING_SLOT,
            title=title,
            message=message,
            recommended_action=action,
            confidence=contract.confidence,
            safety_flags=(SAFETY_MISSING_SLOTS,),
            requires_confirmation=False,
            user_visible=False,
            expires_at=None,
        )
    ]


def _select_type(
    decision: PolicyDecision,
    adapter_capabilities: FrozenSet[str],
) -> Tuple[Optional[str], Dict[str, str]]:
    """Pick the suggestion type for a non-warn decision.

    When the decision is ``suggest_compression_profile`` /
    ``suggest_cache_policy`` / ``suggest_delivery_policy`` and the
    adapter doesn't declare the corresponding capability, fall
    back to ``adapter_capability_recommendation`` (constructive
    guidance to the adapter author) per Phase 2.4 spec §5.
    """
    a = decision.action
    if a == ACTION_SUGGEST_ROUTE:
        return SUGGESTION_PROVIDER_MODEL, {}
    if a == ACTION_SUGGEST_COMPRESSION_PROFILE:
        if "tip.compression.v1" not in adapter_capabilities:
            return SUGGESTION_ADAPTER_CAPABILITY, {
                "missing_capability": "tip.compression.v1",
            }
        return SUGGESTION_COMPRESSION, {}
    if a == ACTION_SUGGEST_CACHE_POLICY:
        if not any(c.startswith("tip.cache.") for c in adapter_capabilities):
            return SUGGESTION_ADAPTER_CAPABILITY, {
                "missing_capability": "tip.cache.proxy-managed",
            }
        return SUGGESTION_CACHE, {}
    if a == ACTION_SUGGEST_DELIVERY_POLICY:
        # Delivery doesn't have a single canonical TIP capability;
        # emit the suggestion directly.
        return SUGGESTION_DELIVERY, {}
    if a == ACTION_FLAG_BUDGET_RISK:
        return SUGGESTION_BUDGET_WARNING, {}
    return None, {}


__all__ = [
    "DRY_RUN_DISCLAIMER",
    "EXPLAINABLE_REASONS",
    "FORBIDDEN_PHRASES",
    "PolicySuggestion",
    "SOURCE_INTENT_POLICY_V0",
    "SUGGESTION_ADAPTER_CAPABILITY",
    "SUGGESTION_BUDGET_WARNING",
    "SUGGESTION_CACHE",
    "SUGGESTION_COMPRESSION",
    "SUGGESTION_DELIVERY",
    "SUGGESTION_MISSING_SLOT",
    "SUGGESTION_PROVIDER_MODEL",
    "SUGGESTION_TYPES",
    "SuggestionBuilderContext",
    "SuggestionWordingError",
    "build_suggestions",
    "make_suggestion_id",
]
