"""
Intent Policy — Deterministic routing decisions for TokenPak proxy v4.

Maps (intent, slots) → (recipe_id, action_profile) using a static policy table.
All decisions are deterministic: same intent + slots always produce the same output.

Action profiles:
    lightweight   — minimal processing, fast path (status, usage)
    compress      — apply compression pipeline (summarize)
    verbose       — full trace/debug output, no compression (debug)
    retrieve      — BM25 vault retrieval injection (search)
    standard      — default pipeline (create, explain, plan, execute, query)

Usage::

    from tokenpak.agent.proxy.intent_policy import resolve_policy, FALLBACK_POLICY

    policy = resolve_policy("summarize", {"target": "vault", "period": "7d"})
    # PolicyResult(recipe_id='summarize-compress', action_profile='compress', reason='intent:summarize')
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PolicyResult:
    """Immutable result from intent policy resolution."""

    recipe_id: str
    action_profile: str
    reason: str  # e.g. "intent:summarize" or "fallback:low_confidence"
    compress: bool = False  # True → run compression pipeline
    retrieve: bool = False  # True → run vault retrieval injection
    skip_compression: bool = False  # True → bypass compression entirely
    # Context contract fields (v2)
    memory_scope: tuple = ()  # memory categories to include (empty = all)
    retrieval_sources: tuple = ()  # retrieval sources to include (empty = all)
    context_quota: int = 4000  # max tokens for this intent
    omission_rules: tuple = ()  # categories to always exclude
    reasoning_ceiling: str = "medium"  # low / medium / high
    stop_condition: str = ""  # when to stop gathering context
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "recipe_id": self.recipe_id,
            "action_profile": self.action_profile,
            "reason": self.reason,
            "compress": self.compress,
            "retrieve": self.retrieve,
            "skip_compression": self.skip_compression,
            "memory_scope": list(self.memory_scope),
            "retrieval_sources": list(self.retrieval_sources),
            "context_quota": self.context_quota,
            "omission_rules": list(self.omission_rules),
            "reasoning_ceiling": self.reasoning_ceiling,
            "stop_condition": self.stop_condition,
            **self.extra,
        }


# ---------------------------------------------------------------------------
# Policy table
# Canonical intents → base policy (may be refined by slot values)
# ---------------------------------------------------------------------------

# Base policy per intent (slot-independent)
_BASE_POLICY: Dict[str, PolicyResult] = {
    "status": PolicyResult(
        recipe_id="status-report",
        action_profile="lightweight",
        reason="intent:status",
        compress=False,
        retrieve=False,
        skip_compression=True,
        memory_scope=(),
        retrieval_sources=(),
        context_quota=500,
        omission_rules=("history", "memory"),
        reasoning_ceiling="low",
        stop_condition="first_answer",
    ),
    "usage": PolicyResult(
        recipe_id="usage-report",
        action_profile="lightweight",
        reason="intent:usage",
        compress=False,
        retrieve=False,
        skip_compression=True,
        memory_scope=(),
        retrieval_sources=(),
        context_quota=500,
        omission_rules=("history", "memory"),
        reasoning_ceiling="low",
        stop_condition="first_answer",
    ),
    "debug": PolicyResult(
        recipe_id="debug-trace",
        action_profile="verbose",
        reason="intent:debug",
        compress=False,
        retrieve=False,
        skip_compression=True,
        memory_scope=("errors", "code"),
        retrieval_sources=("logs", "diff", "tests"),
        context_quota=4000,
        omission_rules=("brand", "style"),
        reasoning_ceiling="high",
        stop_condition="",
    ),
    "summarize": PolicyResult(
        recipe_id="summarize-compress",
        action_profile="compress",
        reason="intent:summarize",
        compress=True,
        retrieve=False,
        skip_compression=False,
        memory_scope=("goals",),
        retrieval_sources=("source_docs",),
        context_quota=3000,
        omission_rules=("raw_logs", "code"),
        reasoning_ceiling="medium",
        stop_condition="",
    ),
    "plan": PolicyResult(
        recipe_id="plan-scaffold",
        action_profile="standard",
        reason="intent:plan",
        compress=True,
        retrieve=True,
        skip_compression=False,
        memory_scope=("goals", "constraints"),
        retrieval_sources=("project_state", "decisions"),
        context_quota=6000,
        omission_rules=("raw_logs",),
        reasoning_ceiling="high",
        stop_condition="",
    ),
    "execute": PolicyResult(
        recipe_id="execute-dispatch",
        action_profile="standard",
        reason="intent:execute",
        compress=False,
        retrieve=False,
        skip_compression=False,
        memory_scope=("current_task",),
        retrieval_sources=(),
        context_quota=2000,
        omission_rules=("history", "style"),
        reasoning_ceiling="low",
        stop_condition="task_complete",
    ),
    "explain": PolicyResult(
        recipe_id="explain-expand",
        action_profile="standard",
        reason="intent:explain",
        compress=True,
        retrieve=True,
        skip_compression=False,
        memory_scope=("context",),
        retrieval_sources=("docs", "code"),
        context_quota=4000,
        omission_rules=(),
        reasoning_ceiling="medium",
        stop_condition="",
    ),
    "search": PolicyResult(
        recipe_id="search-retrieve",
        action_profile="retrieve",
        reason="intent:search",
        compress=False,
        retrieve=True,
        skip_compression=True,
        memory_scope=(),
        retrieval_sources=("vault_all",),
        context_quota=2000,
        omission_rules=("history",),
        reasoning_ceiling="low",
        stop_condition="results_found",
    ),
    "create": PolicyResult(
        recipe_id="create-scaffold",
        action_profile="standard",
        reason="intent:create",
        compress=True,
        retrieve=False,
        skip_compression=False,
        memory_scope=("goals", "style"),
        retrieval_sources=("templates", "examples"),
        context_quota=3000,
        omission_rules=("raw_logs",),
        reasoning_ceiling="medium",
        stop_condition="",
    ),
    "query": PolicyResult(
        recipe_id="pipeline-v1",
        action_profile="standard",
        reason="intent:query",
        compress=True,
        retrieve=False,
        skip_compression=False,
        memory_scope=("recent",),
        retrieval_sources=("relevant",),
        context_quota=4000,
        omission_rules=(),
        reasoning_ceiling="medium",
        stop_condition="",
    ),
}

# Fallback for unknown / low-confidence intents
FALLBACK_POLICY = PolicyResult(
    recipe_id="pipeline-v1",
    action_profile="standard",
    reason="fallback:unknown_intent",
    compress=True,
    retrieve=False,
    skip_compression=False,
    memory_scope=(),
    retrieval_sources=(),
    context_quota=4000,
    omission_rules=(),
    reasoning_ceiling="medium",
    stop_condition="",
)

# Confidence threshold below which we fall back to default pipeline
CONFIDENCE_THRESHOLD = 0.4


# ---------------------------------------------------------------------------
# Slot-based refinements
# A list of (intent, slot_key, slot_value, override_fields) tuples.
# Applied in order; first matching refinement wins per field.
# ---------------------------------------------------------------------------

_SLOT_REFINEMENTS: list[Tuple[str, str, str, Dict[str, Any]]] = [
    # debug + verbose detail level → emit full trace
    ("debug", "detail_level", "verbose", {"action_profile": "verbose", "skip_compression": True}),
    # summarize + long period → heavier compression
    ("summarize", "period", "30d", {"action_profile": "compress", "compress": True}),
    # plan + detailed scope → also retrieve context
    ("plan", "scope", "detailed", {"retrieve": True}),
    # execute + dry_run mode → lightweight, no side effects flag
    (
        "execute",
        "mode",
        "dry_run",
        {"action_profile": "lightweight", "skip_compression": True, "extra": {"dry_run": True}},
    ),
    # search always retrieves (reinforce)
    ("search", "target", "", {"retrieve": True}),
]


def resolve_policy(
    intent: str,
    slots: Optional[Dict[str, Any]] = None,
    confidence: float = 1.0,
) -> PolicyResult:
    """
    Resolve the routing policy for a classified intent + filled slots.

    Args:
        intent:     Canonical intent string (e.g. "summarize", "debug").
        slots:      Slot dict from SlotFiller.fill().
        confidence: Confidence score from SlotFiller (0.0–1.0).

    Returns:
        PolicyResult (frozen dataclass) — always returns something valid.
    """
    slots = slots or {}

    # Low confidence → use fallback but preserve detected intent in reason
    if confidence < CONFIDENCE_THRESHOLD:
        return PolicyResult(
            recipe_id=FALLBACK_POLICY.recipe_id,
            action_profile=FALLBACK_POLICY.action_profile,
            reason=f"fallback:low_confidence({intent},{confidence:.2f})",
            compress=FALLBACK_POLICY.compress,
            retrieve=FALLBACK_POLICY.retrieve,
            skip_compression=FALLBACK_POLICY.skip_compression,
        )

    base = _BASE_POLICY.get(intent)
    if base is None:
        return PolicyResult(
            recipe_id=FALLBACK_POLICY.recipe_id,
            action_profile=FALLBACK_POLICY.action_profile,
            reason=f"fallback:unknown_intent({intent})",
            compress=FALLBACK_POLICY.compress,
            retrieve=FALLBACK_POLICY.retrieve,
            skip_compression=FALLBACK_POLICY.skip_compression,
        )

    # Apply slot-based refinements — collect all override fields
    overrides: Dict[str, Any] = {}
    extra_overrides: Dict[str, Any] = {}

    for ref_intent, ref_slot, ref_value, ref_fields in _SLOT_REFINEMENTS:
        if ref_intent != intent:
            continue
        slot_val = str(slots.get(ref_slot, ""))
        # Empty ref_value means "slot present at all"
        if ref_value == "" and ref_slot not in slots:
            continue
        if ref_value != "" and slot_val != ref_value:
            continue
        for k, v in ref_fields.items():
            if k == "extra":
                extra_overrides.update(v)
            else:
                overrides[k] = v

    if not overrides and not extra_overrides:
        return base

    # Build refined result
    merged_extra = {**base.extra, **extra_overrides}
    return PolicyResult(
        recipe_id=overrides.get("recipe_id", base.recipe_id),
        action_profile=overrides.get("action_profile", base.action_profile),
        reason=base.reason,
        compress=overrides.get("compress", base.compress),
        retrieve=overrides.get("retrieve", base.retrieve),
        skip_compression=overrides.get("skip_compression", base.skip_compression),
        extra=merged_extra,
    )


# ---------------------------------------------------------------------------
# Context contract enforcement
# ---------------------------------------------------------------------------


def apply_context_contract(
    policy: "PolicyResult",
    context: Dict[str, Any],
    token_counter: Optional[Any] = None,
) -> Dict[str, Any]:
    """
    Apply a PolicyResult's context contract to a context dict.

    The context dict maps category names to text values (strings).
    The function:
      1. Removes categories in omission_rules
      2. Filters memory categories by memory_scope (if non-empty)
      3. Filters retrieval categories by retrieval_sources (if non-empty)
      4. Enforces context_quota by truncating the longest values first

    Args:
        policy:        PolicyResult with context contract fields.
        context:       Dict[category_name → text_content].
        token_counter: Optional callable(text) → int for token counting.
                       Defaults to a simple whitespace-based estimator.

    Returns:
        Filtered and truncated context dict.
    """
    if token_counter is None:
        # Rough estimator: ~4 chars per token
        def token_counter(text: str) -> int:  # type: ignore[misc]
            return max(1, len(str(text)) // 4)

    result: Dict[str, Any] = {}

    # Step 1: Remove omitted categories
    for key, value in context.items():
        if key not in policy.omission_rules:
            result[key] = value

    # Step 2: Filter memory_scope (if defined, only allow listed scopes)
    if policy.memory_scope:
        result = {
            k: v
            for k, v in result.items()
            if k in policy.memory_scope or k in policy.retrieval_sources
        }

    # Step 3: Filter retrieval_sources (if defined, only allow listed sources)
    if policy.retrieval_sources:
        # Keep memory_scope items + retrieval_source items; discard others not in either
        allowed = set(policy.memory_scope) | set(policy.retrieval_sources)
        if policy.memory_scope or policy.retrieval_sources:
            result = {k: v for k, v in result.items() if k in allowed}

    # Step 4: Enforce context_quota — truncate if total tokens exceed quota
    total = sum(token_counter(str(v)) for v in result.values())
    if total > policy.context_quota:
        # Sort by token size descending; trim the largest values first
        sorted_keys = sorted(result, key=lambda k: token_counter(str(result[k])), reverse=True)
        budget = policy.context_quota
        trimmed: Dict[str, Any] = {}
        for k in sorted_keys:
            v = str(result[k])
            v_tokens = token_counter(v)
            if v_tokens <= budget:
                trimmed[k] = result[k]
                budget -= v_tokens
            else:
                # Proportional truncation: keep as many chars as budget allows
                if budget > 0:
                    keep_chars = budget * 4  # reverse of estimator
                    trimmed[k] = v[:keep_chars]
                    budget = 0
                # No budget left — drop remaining keys
        result = trimmed

    return result


def known_intents() -> list[str]:
    """Return all intents with explicit policy entries."""
    return list(_BASE_POLICY.keys())


# ---------------------------------------------------------------------------
# Decision object (used by proxy_v4 router)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DecisionAction:
    """Action directives for a routing decision."""

    compress: bool = True  # Run compression pipeline?
    retrieve: bool = False  # Inject vault retrieval?
    skip_compression: bool = False  # Bypass compression override?
    dry_run: bool = False  # Execute in dry-run mode?


@dataclass(frozen=True)
class RoutingDecision:
    """Complete routing decision from classifier-first policy."""

    intent: str
    recipe_id: str
    slots_used: dict  # Filled slots (from SlotFiller)
    action: DecisionAction
    fallback: bool = False
    fallback_reason: str = ""
    confidence: float = 1.0


def decide(
    intent: str,
    slots: Optional[Dict[str, Any]] = None,
    confidence: float = 1.0,
) -> RoutingDecision:
    """
    Resolve a complete routing decision for the proxy router.

    This is the main entry point used by proxy_v4.py.

    Args:
        intent:     Canonical intent string (e.g. "summarize").
        slots:      Slot dict from SlotFiller.fill().
        confidence: Confidence score from SlotFiller (0.0–1.0).

    Returns:
        RoutingDecision — complete decision with recipe, action profile, and flags.
    """
    slots = slots or {}

    # Low confidence → use fallback recipe but preserve detected intent
    if confidence < CONFIDENCE_THRESHOLD:
        return RoutingDecision(
            intent=intent,
            recipe_id=FALLBACK_POLICY.recipe_id,
            slots_used=slots,
            action=DecisionAction(
                compress=FALLBACK_POLICY.compress,
                retrieve=FALLBACK_POLICY.retrieve,
                skip_compression=FALLBACK_POLICY.skip_compression,
            ),
            fallback=True,
            fallback_reason=f"low_confidence({confidence:.2f})",
            confidence=confidence,
        )

    base = _BASE_POLICY.get(intent)
    if base is None:
        return RoutingDecision(
            intent=intent,
            recipe_id=FALLBACK_POLICY.recipe_id,
            slots_used=slots,
            action=DecisionAction(
                compress=FALLBACK_POLICY.compress,
                retrieve=FALLBACK_POLICY.retrieve,
                skip_compression=FALLBACK_POLICY.skip_compression,
            ),
            fallback=True,
            fallback_reason="unknown_intent",
            confidence=0.0,
        )

    # Apply slot-based refinements
    overrides: Dict[str, Any] = {}
    extra_overrides: Dict[str, Any] = {}

    for ref_intent, ref_slot, ref_value, ref_fields in _SLOT_REFINEMENTS:
        if ref_intent != intent:
            continue
        slot_val = str(slots.get(ref_slot, ""))
        if ref_value == "" and ref_slot not in slots:
            continue
        if ref_value != "" and slot_val != ref_value:
            continue
        for k, v in ref_fields.items():
            if k == "extra":
                extra_overrides.update(v)
            else:
                overrides[k] = v

    # Build action from policy
    action_compress = overrides.get("compress", base.compress)
    action_retrieve = overrides.get("retrieve", base.retrieve)
    action_skip = overrides.get("skip_compression", base.skip_compression)
    action_dry_run = extra_overrides.get("dry_run", False)

    return RoutingDecision(
        intent=intent,
        recipe_id=overrides.get("recipe_id", base.recipe_id),
        slots_used=slots,
        action=DecisionAction(
            compress=action_compress,
            retrieve=action_retrieve,
            skip_compression=action_skip,
            dry_run=action_dry_run,
        ),
        fallback=False,
        fallback_reason="",
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Canonical intent set + helper — added for classifier-first router wiring
# ---------------------------------------------------------------------------

#: All intents with explicit policy definitions.
CANONICAL_INTENTS: frozenset[str] = frozenset(_BASE_POLICY.keys())


def is_known_intent(intent: str) -> bool:
    """Return True if intent is in the canonical policy set."""
    return intent in _BASE_POLICY


def known_intents() -> list[str]:
    """Return list of all known canonical intent strings."""
    return list(_BASE_POLICY.keys())
