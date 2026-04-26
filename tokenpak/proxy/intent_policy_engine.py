# SPDX-License-Identifier: Apache-2.0
"""Phase 2.1 — dry-run intent policy engine.

Pure function: ``evaluate_policy(input, config) -> PolicyDecision``.
Reads structured inputs (the IntentContract from Phase 0 plus
request context — provider, model, adapter capabilities,
live_verified status), returns one decision. No I/O during
evaluation, no globals, no mutation. Telemetry write is a separate
concern (:mod:`tokenpak.proxy.intent_policy_telemetry`).

Phase 2.1 ships in **dry-run mode**. The engine emits decisions
that *describe* what a future opt-in suggest mode (Phase 2.4)
might recommend, but never mutates the request or causes a
re-route. The default config (:data:`_DEFAULT_CONFIG`) is
``observe_only`` + ``dry_run=true``, so an operator who has not
configured anything sees zero behavior change vs. Phase 1.1.

The seven actions in the Phase 2.1 enum:

  - ``observe_only`` — default, no fields populated beyond identity.
  - ``warn_only`` — emitted whenever any safety flag trips.
  - ``suggest_route`` — populated when ``allow_auto_routing=true``
    AND the request is safe (no flag tripped). Carries
    ``recommended_provider`` / ``recommended_model``.
  - ``suggest_compression_profile`` — populated when the intent
    has a heuristic recommendation AND the request is safe.
  - ``suggest_cache_policy`` — populated when the resolved
    adapter declares a cache capability AND the request is safe.
  - ``suggest_delivery_policy`` — populated when the intent has
    a delivery heuristic AND the request is safe.
  - ``flag_budget_risk`` — reserved; not emitted in 2.1 (real
    budget caps land in 2.6).

Multiple suggestions could apply to one request; Phase 2.1 picks
the *single* highest-priority action and populates only its
matching field. Spec §3 envisions an ``actions: list[str]`` shape
in 2.4+; the directive's "action" (singular) is honored here.
Lower-priority suggestions are deferred to Phase 2.2's explain
extension.

Safety rules (always on; cannot be disabled by config):

  1. Low confidence — ``confidence < threshold`` blocks routing-
     affecting actions; engine emits ``warn_only``.
  2. Catch-all — ``catch_all_reason is not None`` blocks routing-
     affecting actions.
  3. Missing required slots — any required slot in
     ``slots_missing`` blocks routing-affecting actions.
  4. ``live_verified=False`` provider — blocked unless
     ``allow_unverified_providers=true`` in config.

Privacy: the engine reads structured fields only. Raw prompt text
is never an input. ``warning_message`` is built from a templated
string + the safety flag id; no caller-supplied substring ever
touches the message body.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional, Tuple

# ---------------------------------------------------------------------------
# Action / decision-reason enums
# ---------------------------------------------------------------------------


# Phase 2.1 action enum (seven). Reserved set may grow in later
# sub-phases per the Phase 2 spec §3.
ACTION_OBSERVE_ONLY: str = "observe_only"
ACTION_WARN_ONLY: str = "warn_only"
ACTION_SUGGEST_ROUTE: str = "suggest_route"
ACTION_SUGGEST_COMPRESSION_PROFILE: str = "suggest_compression_profile"
ACTION_SUGGEST_CACHE_POLICY: str = "suggest_cache_policy"
ACTION_SUGGEST_DELIVERY_POLICY: str = "suggest_delivery_policy"
ACTION_FLAG_BUDGET_RISK: str = "flag_budget_risk"

ACTIONS_PHASE_2_1: FrozenSet[str] = frozenset({
    ACTION_OBSERVE_ONLY,
    ACTION_WARN_ONLY,
    ACTION_SUGGEST_ROUTE,
    ACTION_SUGGEST_COMPRESSION_PROFILE,
    ACTION_SUGGEST_CACHE_POLICY,
    ACTION_SUGGEST_DELIVERY_POLICY,
    ACTION_FLAG_BUDGET_RISK,
})

# Decision reason enum — stable identifiers, not free text. Phase 2
# spec §5 enumerates this set.
REASON_DEFAULT_OBSERVE_ONLY: str = "default_observe_only"
REASON_LOW_CONFIDENCE_BLOCKED_ROUTING: str = "low_confidence_blocked_routing"
REASON_CATCH_ALL_BLOCKED_ROUTING: str = "catch_all_blocked_routing"
REASON_MISSING_SLOTS_BLOCKED_ROUTING: str = "missing_slots_blocked_routing"
REASON_UNVERIFIED_PROVIDER_BLOCKED: str = "unverified_provider_blocked"
REASON_ROUTING_DISABLED_BY_CONFIG: str = "routing_disabled_by_config"
REASON_DRY_RUN_SUGGEST: str = "dry_run_suggest"

# Safety flag identifiers — appear in PolicyDecision.safety_flags
# tuple. Each maps to a §6 spec rule and a test in Phase 2.1.
SAFETY_LOW_CONFIDENCE: str = "low_confidence"
SAFETY_CATCH_ALL: str = "catch_all"
SAFETY_MISSING_SLOTS: str = "missing_slots"
SAFETY_UNVERIFIED_PROVIDER: str = "unverified_provider"


# ---------------------------------------------------------------------------
# Config dataclass + defaults
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyEngineConfig:
    """Engine-side projection of the ``intent_policy`` config block.

    Phase 2.1 covers exactly the five fields the directive
    enumerates. The Phase 2 spec §7 defines additional fields
    (``budget_caps``, ``class_rules``, ``class_groups``) that
    later sub-phases will plumb in.
    """

    mode: str = "observe_only"  # observe_only | suggest | confirm | enforce
    dry_run: bool = True
    allow_auto_routing: bool = False
    allow_unverified_providers: bool = False
    low_confidence_threshold: float = 0.65


_DEFAULT_CONFIG: PolicyEngineConfig = PolicyEngineConfig()


def load_default_config() -> PolicyEngineConfig:
    """Return the Phase 2.1 default config.

    Phase 2.1 ships without a config-file loader; the directive
    explicitly limits scope to the dry-run engine. The Phase 2.2
    sub-phase will add ``~/.tokenpak/policy.yaml`` parsing per the
    spec §7 schema. Until then, the default config is the only
    config a host can have.
    """
    return _DEFAULT_CONFIG


# ---------------------------------------------------------------------------
# Inputs / outputs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyInput:
    """Bundle of facts the engine reads.

    Mirrors Phase 2 spec §4 verbatim. No raw prompt text; no
    undocumented globals. Future inputs require both a spec update
    and a corresponding test under §9.
    """

    intent_class: str
    confidence: float
    slots_present: Tuple[str, ...]
    slots_missing: Tuple[str, ...]
    catch_all_reason: Optional[str]
    provider: str
    model: str
    estimated_cost_usd: Optional[float] = None
    adapter_capabilities: FrozenSet[str] = frozenset()
    delivery_target_capabilities: FrozenSet[str] = frozenset()
    live_verified_status: Optional[bool] = None
    required_slots: Tuple[str, ...] = ()


@dataclass(frozen=True)
class PolicyDecision:
    """Engine output. Field set matches the directive exactly.

    All fields except ``decision_id`` / ``mode`` /
    ``intent_class`` / ``confidence`` / ``action`` /
    ``decision_reason`` / ``safety_flags`` are nullable; the JSON
    serializer emits explicit ``null`` for unset fields so
    consumers can rely on field presence.
    """

    decision_id: str
    mode: str  # always "dry_run" in Phase 2.1
    intent_class: str
    confidence: float
    action: str
    recommended_provider: Optional[str] = None
    recommended_model: Optional[str] = None
    budget_action: Optional[str] = None
    compression_profile: Optional[str] = None
    cache_strategy: Optional[str] = None
    delivery_strategy: Optional[str] = None
    warning_message: Optional[str] = None
    requires_user_confirmation: bool = False
    decision_reason: str = REASON_DEFAULT_OBSERVE_ONLY
    safety_flags: Tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "mode": self.mode,
            "intent_class": self.intent_class,
            "confidence": self.confidence,
            "action": self.action,
            "recommended_provider": self.recommended_provider,
            "recommended_model": self.recommended_model,
            "budget_action": self.budget_action,
            "compression_profile": self.compression_profile,
            "cache_strategy": self.cache_strategy,
            "delivery_strategy": self.delivery_strategy,
            "warning_message": self.warning_message,
            "requires_user_confirmation": self.requires_user_confirmation,
            "decision_reason": self.decision_reason,
            "safety_flags": list(self.safety_flags),
        }


def make_decision_id() -> str:
    """ULID-shaped opaque id for the policy decision (29 hex chars).

    Mirrors :func:`tokenpak.proxy.intent_contract.make_contract_id`
    so consumers can sort policy + contract ids by the same prefix.
    """
    ts_ms = int(time.time() * 1000)
    rand = secrets.token_hex(8)
    return f"{ts_ms:013x}{rand}"


# ---------------------------------------------------------------------------
# Heuristic tables
# ---------------------------------------------------------------------------


# Phase 2.1 minimal per-intent heuristics. These describe what a
# 2.4+ suggest mode might recommend; in 2.1 they only populate the
# corresponding fields when the engine emits a suggest_* action.
# The table is intentionally small and conservative — bigger
# heuristics (recipes, cost-aware routing) live in 2.3+ designs.
_COMPRESSION_HEURISTIC: Dict[str, str] = {
    "summarize": "aggressive",
    "plan": "conservative",
    "explain": "conservative",
    "create": "conservative",
    "debug": "conservative",
    # status / usage / search / execute / query — no profile suggested
    # by default. The Phase 0 baseline-report deliverable will tell
    # us whether the table needs broadening.
}

_DELIVERY_HEURISTIC: Dict[str, str] = {
    "debug": "streaming",
    "summarize": "non_streaming",
    "explain": "streaming",
    "plan": "non_streaming",
    "create": "streaming",
}


# ---------------------------------------------------------------------------
# Pure engine
# ---------------------------------------------------------------------------


def evaluate_policy(
    inp: PolicyInput, config: Optional[PolicyEngineConfig] = None
) -> PolicyDecision:
    """Pure function. Phase 2.1's only entry point.

    Reads ``inp`` + ``config``, returns one :class:`PolicyDecision`.
    Never mutates either input. Never performs I/O. Safe to call
    from any thread.
    """
    cfg = config if config is not None else _DEFAULT_CONFIG

    decision_id = make_decision_id()
    safety: list[str] = []

    # ── Safety evaluation (always-on; spec §6 rules 1-4) ──────────
    if inp.confidence < cfg.low_confidence_threshold:
        safety.append(SAFETY_LOW_CONFIDENCE)
    if inp.catch_all_reason is not None:
        safety.append(SAFETY_CATCH_ALL)
    if _has_missing_required_slots(inp):
        safety.append(SAFETY_MISSING_SLOTS)
    if (
        inp.live_verified_status is False
        and not cfg.allow_unverified_providers
    ):
        safety.append(SAFETY_UNVERIFIED_PROVIDER)

    # Any safety flag → warn_only with the flags recorded.
    if safety:
        return PolicyDecision(
            decision_id=decision_id,
            mode="dry_run",
            intent_class=inp.intent_class,
            confidence=inp.confidence,
            action=ACTION_WARN_ONLY,
            warning_message=_warning_message(safety),
            decision_reason=_safety_reason(safety),
            safety_flags=tuple(safety),
        )

    # ── No safety flags. Pick a suggestion priority order. ────────
    # The Phase 2.1 directive uses singular ``action``; multi-action
    # decisions are the spec's 2.4+ shape. Priority order:
    #   suggest_route  >  suggest_compression_profile
    #                  >  suggest_cache_policy
    #                  >  suggest_delivery_policy
    #                  >  observe_only
    if cfg.allow_auto_routing:
        # In dry-run, "suggest_route" is purely observational —
        # recommended_provider / model carry the engine's hint, but
        # the dispatcher is unaware of this field in 2.1.
        return PolicyDecision(
            decision_id=decision_id,
            mode="dry_run",
            intent_class=inp.intent_class,
            confidence=inp.confidence,
            action=ACTION_SUGGEST_ROUTE,
            recommended_provider=inp.provider,
            recommended_model=inp.model,
            decision_reason=REASON_DRY_RUN_SUGGEST,
        )

    compression = _COMPRESSION_HEURISTIC.get(inp.intent_class)
    if compression is not None:
        return PolicyDecision(
            decision_id=decision_id,
            mode="dry_run",
            intent_class=inp.intent_class,
            confidence=inp.confidence,
            action=ACTION_SUGGEST_COMPRESSION_PROFILE,
            compression_profile=compression,
            decision_reason=REASON_DRY_RUN_SUGGEST,
        )

    if _has_cache_capability(inp):
        return PolicyDecision(
            decision_id=decision_id,
            mode="dry_run",
            intent_class=inp.intent_class,
            confidence=inp.confidence,
            action=ACTION_SUGGEST_CACHE_POLICY,
            cache_strategy=_cache_strategy_for(inp),
            decision_reason=REASON_DRY_RUN_SUGGEST,
        )

    delivery = _DELIVERY_HEURISTIC.get(inp.intent_class)
    if delivery is not None:
        return PolicyDecision(
            decision_id=decision_id,
            mode="dry_run",
            intent_class=inp.intent_class,
            confidence=inp.confidence,
            action=ACTION_SUGGEST_DELIVERY_POLICY,
            delivery_strategy=delivery,
            decision_reason=REASON_DRY_RUN_SUGGEST,
        )

    # Default: observe_only. No suggestion fired.
    return PolicyDecision(
        decision_id=decision_id,
        mode="dry_run",
        intent_class=inp.intent_class,
        confidence=inp.confidence,
        action=ACTION_OBSERVE_ONLY,
        decision_reason=REASON_DEFAULT_OBSERVE_ONLY,
    )


# ---------------------------------------------------------------------------
# Helpers (pure)
# ---------------------------------------------------------------------------


def _has_missing_required_slots(inp: PolicyInput) -> bool:
    if not inp.required_slots:
        return False
    missing = set(inp.slots_missing)
    return any(slot in missing for slot in inp.required_slots)


def _has_cache_capability(inp: PolicyInput) -> bool:
    return any(c.startswith("tip.cache.") for c in inp.adapter_capabilities)


def _cache_strategy_for(inp: PolicyInput) -> str:
    if "tip.cache.proxy-managed" in inp.adapter_capabilities:
        return "proxy_managed"
    if "tip.cache.provider-observer" in inp.adapter_capabilities:
        return "client_observed"
    return "bypass"


def _warning_message(flags: list[str]) -> str:
    """Build a templated warning string. NEVER includes prompt text.

    The ``flags`` argument is the engine-built safety-flag tuple;
    no caller-supplied substrings reach this string. Tests under
    Phase 2.1 §9 enforce this with the sentinel-substring pattern.
    """
    if not flags:
        return ""
    body = ", ".join(sorted(flags))
    return (
        f"Intent policy: routing-affecting actions suppressed by safety "
        f"rules ({body}). Decision is informational only (dry-run)."
    )


def _safety_reason(flags: list[str]) -> str:
    """Map a safety-flag list to a single ``decision_reason``.

    Priority when multiple flags trip: low_confidence >
    catch_all > missing_slots > unverified_provider. This matches
    the Phase 2 spec §5 reason taxonomy ordering.
    """
    if SAFETY_LOW_CONFIDENCE in flags:
        return REASON_LOW_CONFIDENCE_BLOCKED_ROUTING
    if SAFETY_CATCH_ALL in flags:
        return REASON_CATCH_ALL_BLOCKED_ROUTING
    if SAFETY_MISSING_SLOTS in flags:
        return REASON_MISSING_SLOTS_BLOCKED_ROUTING
    if SAFETY_UNVERIFIED_PROVIDER in flags:
        return REASON_UNVERIFIED_PROVIDER_BLOCKED
    return REASON_ROUTING_DISABLED_BY_CONFIG


__all__ = [
    "ACTIONS_PHASE_2_1",
    "ACTION_FLAG_BUDGET_RISK",
    "ACTION_OBSERVE_ONLY",
    "ACTION_SUGGEST_CACHE_POLICY",
    "ACTION_SUGGEST_COMPRESSION_PROFILE",
    "ACTION_SUGGEST_DELIVERY_POLICY",
    "ACTION_SUGGEST_ROUTE",
    "ACTION_WARN_ONLY",
    "PolicyDecision",
    "PolicyEngineConfig",
    "PolicyInput",
    "REASON_CATCH_ALL_BLOCKED_ROUTING",
    "REASON_DEFAULT_OBSERVE_ONLY",
    "REASON_DRY_RUN_SUGGEST",
    "REASON_LOW_CONFIDENCE_BLOCKED_ROUTING",
    "REASON_MISSING_SLOTS_BLOCKED_ROUTING",
    "REASON_ROUTING_DISABLED_BY_CONFIG",
    "REASON_UNVERIFIED_PROVIDER_BLOCKED",
    "SAFETY_CATCH_ALL",
    "SAFETY_LOW_CONFIDENCE",
    "SAFETY_MISSING_SLOTS",
    "SAFETY_UNVERIFIED_PROVIDER",
    "evaluate_policy",
    "load_default_config",
    "make_decision_id",
]
