"""Dispatch runtime — deterministic route selection (Standards Delta v0 §5.8).

This is the runtime entry that wires FrontDock output → a *selected route*. It
does **not** execute stations (that is P-EXEC-01's job): it answers the single
question "given an intake bundle, which route runs, and may it auto-dispatch?".

The decision is layered, and the order is the contract (Standards Delta v0 §5.8):

    1. explicit user route   (--route flag / explicit_route arg)
    2. project rule          (.tpk/dispatch/project_rules.yaml, if present)
    3. exact route_trigger   (a route declares the job's intent string)
    4. intent classification (deterministic keyword classification, via FrontDock)
    5. registry tie-break    (numeric ALPHA scoring; constants below)
    6. else → DispatchDecision (ask the user)

Two cross-cutting rules sit on top of the precedence walk:

* **Dynamic capability binding (§11).** A route is only *selectable* if every
  worker station can be staffed from the live worker registry by capability
  intersection (``routes.bind_route``). A route that names a role/capability no
  worker can satisfy is not a candidate — it scores the
  ``forbidden_action_required`` penalty and is skipped, never silently chosen.

* **Confidence thresholds (§5.8).** The chosen route carries a numeric
  confidence. ``>= 60`` ⇒ auto-dispatch *if autonomy permits*; ``40–59`` ⇒
  create a :class:`DispatchDecision` (ask the user); ``< 40`` ⇒ refuse. An
  explicit user route is the one path that bypasses scoring entirely (the user
  asked for it by name) but it still must bind.

**The LLM never dispatches.** :class:`RouteSuggester` (built on the FrontDock
``TipClient`` boundary, so no provider SDK is imported here) can be consulted for
a *suggestion* only — a schema-bound :class:`RouteSuggestion` (route_id +
confidence + reasons + missing_info + risk_flags). The deterministic precedence
layer is the sole authority that decides; a suggestion is advisory input to the
tie-break step and is discarded if it names an unknown or unbindable route.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Protocol, Union, runtime_checkable

from .frontdock import FrontDockResult, TipClient
from .models.decision import (
    DecisionDefaultAction,
    DecisionOption,
    DecisionRecommendation,
    DispatchDecision,
)
from .models.enums import (
    AutoApplyAfter,
    AutonomyMode,
    DecisionScope,
    DecisionStatus,
    RiskLevel,
)
from .models.job import DispatchJob
from .models.route import DispatchRoute
from .registry.routes import (
    DispatchRouteRegistry,
    RouteResolutionError,
    bind_route,
    default_route_registry,
    route_is_bindable,
)
from .registry.workers import DispatchWorkerRegistry, default_worker_registry

# ---------------------------------------------------------------------------
# Alpha scoring constants (Standards Delta v0 §5.8)
# ---------------------------------------------------------------------------
#
# status: alpha_placeholder
# recalibrate_before: v0.1-beta
#
# These weights + thresholds are GUT-FEEL ALPHA PLACEHOLDERS, transcribed
# verbatim from Standards Delta v0 §5.8. They MUST be replaced before v0.1-beta
# with data-driven values from real Run Ledger data (routes chosen / overridden
# / failed / user corrections / decision frequency / delivery acceptance — §5.8
# "Beta recalibration inputs"). Do not treat any number here as tuned.

# Machine-readable provenance marker so tooling / tests can assert the alpha
# tagging required by acceptance criterion 6.
DISPATCH_SCORING_METADATA: dict[str, str] = {
    "status": "alpha_placeholder",
    "recalibrate_before": "v0.1-beta",
}

# Score weights (§5.8 ``dispatch_scoring.weights``, verbatim).
SCORE_EXPLICIT_ROUTE_REQUESTED = 100  # status: alpha_placeholder
SCORE_EXACT_ROUTE_TRIGGER_MATCH = 40  # status: alpha_placeholder
SCORE_INTENT_MATCH = 25  # status: alpha_placeholder
SCORE_FILE_CONTEXT_HINT_MATCH = 15  # status: alpha_placeholder
SCORE_RISK_COMPATIBILITY = 10  # status: alpha_placeholder
SCORE_AUTONOMY_COMPATIBILITY = 10  # status: alpha_placeholder
SCORE_ORDERING_HINT_MATCH = 10  # status: alpha_placeholder
SCORE_RISK_MISMATCH = -25  # status: alpha_placeholder
SCORE_MISSING_REQUIRED_INFO = -40  # status: alpha_placeholder
SCORE_FORBIDDEN_ACTION_REQUIRED = -100  # status: alpha_placeholder

# Confidence thresholds (§5.8 ``dispatch_scoring.thresholds``, verbatim).
THRESHOLD_AUTO_DISPATCH = 60  # >= 60 → auto-dispatch if autonomy permits
THRESHOLD_DECISION_FLOOR = 40  # 40..59 → DispatchDecision; < 40 → refuse


# Autonomy modes under which a >=60 route may actually auto-dispatch (§5.8 +
# §14.2). ``advisory`` / ``draft`` never auto-dispatch even at high confidence —
# they only ever draft / propose.
_AUTO_DISPATCH_MODES: frozenset[AutonomyMode] = frozenset(
    {AutonomyMode.DISPATCH_WITH_APPROVAL, AutonomyMode.AUTO_DISPATCH_LIMITED}
)


# ---------------------------------------------------------------------------
# Schema-bound LLM route suggestion (advisory only; never dispatches)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteSuggestion:
    """Schema-bound LLM route suggestion (Standards Delta v0 §5.8 step 5 input).

    The LLM may *suggest* a route; it never dispatches. This is the strict,
    validated shape a suggestion must take: ``route_id`` + ``confidence`` +
    ``reasons`` + ``missing_info`` + ``risk_flags``. The deterministic layer
    treats it as advisory tie-break input and discards it if ``route_id`` is not
    a known, bindable route.
    """

    route_id: str
    confidence: int
    reasons: tuple[str, ...] = ()
    missing_info: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> "RouteSuggestion":
        """Parse + validate a raw suggestion payload (fail-loud on bad shape).

        The LLM boundary returns untyped data; this is the schema gate. A
        payload missing ``route_id``, or with a non-integer / out-of-range
        ``confidence``, is rejected with :class:`InvalidRouteSuggestion` — the
        suggester catches that and falls back to no suggestion (the LLM never
        gets to dispatch via a malformed payload).
        """

        if not isinstance(payload, Mapping):
            raise InvalidRouteSuggestion(f"suggestion must be a mapping, got {type(payload).__name__}")
        route_id = payload.get("route_id")
        if not isinstance(route_id, str) or not route_id:
            raise InvalidRouteSuggestion("suggestion missing a non-empty 'route_id'")
        raw_conf = payload.get("confidence", 0)
        if isinstance(raw_conf, bool) or not isinstance(raw_conf, (int, float)):
            raise InvalidRouteSuggestion("suggestion 'confidence' must be a number")
        confidence = int(raw_conf)
        if not 0 <= confidence <= 100:
            raise InvalidRouteSuggestion("suggestion 'confidence' must be in [0, 100]")
        return cls(
            route_id=route_id,
            confidence=confidence,
            reasons=_str_tuple(payload.get("reasons")),
            missing_info=_str_tuple(payload.get("missing_info")),
            risk_flags=_str_tuple(payload.get("risk_flags")),
        )


class InvalidRouteSuggestion(ValueError):
    """Raised when an LLM route-suggestion payload fails its schema gate."""


def _str_tuple(value: Any) -> tuple[str, ...]:
    """Coerce an optional list-of-strings field to a tuple (non-strings dropped)."""

    if value is None:
        return ()
    if isinstance(value, str):
        return (value,)
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return ()


@runtime_checkable
class RouteSuggestClient(Protocol):
    """LLM boundary for route suggestion — routes through TIP at runtime.

    Mirrors / extends the FrontDock :class:`TipClient` Protocol: the production
    binding is the TIP client (Spend Guard enforced, §8); in tests it is a
    deterministic mock. **No provider SDK is imported or called by this module.**
    A client returns a raw payload that :meth:`RouteSuggestion.from_payload`
    validates; the LLM only ever *suggests*.
    """

    def suggest_route(
        self, request: str, candidate_route_ids: list[str]
    ) -> Union[Mapping[str, Any], "RouteSuggestion"]:
        """Return a route suggestion (mapping or RouteSuggestion) for ``request``."""
        ...


class RouteSuggester:
    """Consult an injected LLM client for a *suggestion* only (never dispatch).

    Wraps a :class:`RouteSuggestClient` (or a FrontDock-style :class:`TipClient`
    exposing ``suggest_route``). ``client=None`` is legal: :meth:`suggest`
    returns ``None`` and the deterministic layer proceeds without LLM input.
    A malformed / out-of-vocabulary suggestion is discarded (returns ``None``),
    so the LLM can never push an unknown route through.
    """

    def __init__(self, client: Optional[RouteSuggestClient] = None) -> None:
        self._client = client

    def suggest(
        self, request: str, candidate_route_ids: list[str]
    ) -> Optional[RouteSuggestion]:
        """Return a validated :class:`RouteSuggestion`, or ``None`` if unavailable.

        Returns ``None`` when there is no client, the client raises, the payload
        fails the schema gate, or the suggested ``route_id`` is not among
        ``candidate_route_ids`` (out-of-vocabulary → discarded, not invented).
        """

        if self._client is None:
            return None
        suggest = getattr(self._client, "suggest_route", None)
        if suggest is None:
            return None
        try:
            raw = suggest(request, list(candidate_route_ids))
        except Exception:  # noqa: BLE001 - an LLM failure must not crash routing
            return None
        try:
            suggestion = (
                raw if isinstance(raw, RouteSuggestion) else RouteSuggestion.from_payload(raw)
            )
        except InvalidRouteSuggestion:
            return None
        if suggestion.route_id not in set(candidate_route_ids):
            # Out-of-vocabulary suggestion → discard. The LLM never invents a route.
            return None
        return suggestion


# ---------------------------------------------------------------------------
# Project rules (§5.8 step 2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectRules:
    """Project-level route overrides (Standards Delta v0 §5.8 step 2).

    A thin, in-memory representation of ``.tpk/dispatch/project_rules.yaml``.
    v0.1-alpha supports the one rule the precedence contract names: a
    per-intent forced route (``intent_routes``). Absent rules (``None`` /
    empty) make this step a no-op. Loading the YAML file itself is a later
    CLI-layer concern; the runtime takes the already-parsed mapping so it stays
    file-system-free and deterministic in tests.
    """

    intent_routes: Mapping[str, str] = field(default_factory=dict)

    def route_for_intent(self, intent: str) -> Optional[str]:
        """Return the project-forced route id for ``intent``, or ``None``."""

        return self.intent_routes.get(intent)

    @property
    def is_empty(self) -> bool:
        return not self.intent_routes


# ---------------------------------------------------------------------------
# Scoring (§5.8 step 5)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RouteScore:
    """A single candidate route's alpha score + its component breakdown."""

    route_id: str
    score: int
    components: tuple[tuple[str, int], ...]
    bindable: bool

    @property
    def confidence(self) -> int:
        """Confidence is the score clamped to [0, 100] (§5.8 threshold domain)."""

        return max(0, min(100, self.score))


def score_route(
    route: DispatchRoute,
    job: DispatchJob,
    worker_registry: DispatchWorkerRegistry,
    *,
    suggestion: Optional[RouteSuggestion] = None,
    has_material_missing_info: bool = False,
) -> RouteScore:
    """Score one candidate ``route`` for ``job`` (Standards Delta v0 §5.8 weights).

    ALPHA PLACEHOLDER scoring (status: alpha_placeholder; recalibrate_before:
    v0.1-beta). Applies the §5.8 weight table:

    * +exact_route_trigger_match when the route declares the job's intent;
    * +intent_match when the job's intent maps to this route family;
    * +risk_compatibility / -risk_mismatch by comparing the job's detected risk
      flags against the route ``default_risk``;
    * +autonomy_compatibility when the job's autonomy mode permits dispatch;
    * -missing_required_info when the job has *material* missing info
      (``has_material_missing_info`` — a blocking gap that FrontDock surfaced as
      a decision, NOT a soft probe gap it already downgraded to an assumption.
      Penalizing the soft probes would refuse every ordinary task, so only the
      material signal counts here);
    * -forbidden_action_required when the route cannot be staffed from the live
      worker registry (capability mismatch — the strongest negative, §11);
    * a small +file_context_hint / +ordering_hint when an LLM suggestion both
      names this route and corroborates it (advisory only).
    """

    components: list[tuple[str, int]] = []
    bindable = route_is_bindable(route, worker_registry)

    # An unstaffable route is effectively requiring a forbidden action: it cannot
    # do the work. This dominates the score so it can never be auto-chosen (§11).
    if not bindable:
        components.append(("forbidden_action_required", SCORE_FORBIDDEN_ACTION_REQUIRED))

    intent = job.detected_intent
    if intent and intent in route.triggers.intents:
        components.append(("exact_route_trigger_match", SCORE_EXACT_ROUTE_TRIGGER_MATCH))
        components.append(("intent_match", SCORE_INTENT_MATCH))

    # Risk compatibility: a job whose detected risk flags imply HIGH/CRITICAL
    # risk is mismatched against a low-risk route, and compatible with a
    # higher-risk route (alpha heuristic).
    job_risk = _job_risk_level(job)
    if _risk_compatible(job_risk, route.default_risk):
        components.append(("risk_compatibility", SCORE_RISK_COMPATIBILITY))
    else:
        components.append(("risk_mismatch", SCORE_RISK_MISMATCH))

    if job.autonomy_mode in _AUTO_DISPATCH_MODES:
        components.append(("autonomy_compatibility", SCORE_AUTONOMY_COMPATIBILITY))

    if has_material_missing_info:
        components.append(("missing_required_info", SCORE_MISSING_REQUIRED_INFO))

    # Advisory LLM corroboration: only nudges (never decides). Counts as a soft
    # file-context / ordering hint when the suggestion names THIS route.
    if suggestion is not None and suggestion.route_id == route.id:
        components.append(("file_context_hint_match", SCORE_FILE_CONTEXT_HINT_MATCH))

    total = sum(weight for _, weight in components)
    return RouteScore(
        route_id=route.id,
        score=total,
        components=tuple(components),
        bindable=bindable,
    )


_RISK_ORDER: dict[RiskLevel, int] = {
    RiskLevel.LOW: 0,
    RiskLevel.MEDIUM: 1,
    RiskLevel.HIGH: 2,
    RiskLevel.CRITICAL: 3,
}


def _job_risk_level(job: DispatchJob) -> RiskLevel:
    """Derive the job's effective risk level from its detected risk flags.

    No flags → LOW. Otherwise the most severe registered flag level. Imported
    lazily from FrontDock's registry so the single source of truth for flag
    severities is not duplicated here.
    """

    from .frontdock import RISK_FLAG_REGISTRY

    level = RiskLevel.LOW
    for flag in job.risk_flags:
        flag_level = RISK_FLAG_REGISTRY.get(flag, RiskLevel.LOW)
        if _RISK_ORDER[flag_level] > _RISK_ORDER[level]:
            level = flag_level
    return level


def _risk_compatible(job_risk: RiskLevel, route_risk: RiskLevel) -> bool:
    """A route is risk-compatible if its default risk is >= the job's risk.

    A high-risk job routed through a low-risk route is a mismatch (the route
    under-provisions for the risk); a low-risk job through any route is fine.
    """

    return _RISK_ORDER[route_risk] >= _RISK_ORDER[job_risk]


# ---------------------------------------------------------------------------
# Selection outcome
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SelectionOutcome:
    """The result of route selection (Standards Delta v0 §5.8).

    Exactly one of ``route`` (a selected, bound route) or ``decision`` (a
    DispatchDecision asking the user) is set, per ``status``:

    * ``auto_dispatch`` — route selected, confidence >= 60, autonomy permits;
    * ``needs_approval`` — route selected, confidence >= 60, but autonomy is
      advisory/draft (the route is chosen but not auto-dispatched);
    * ``decision`` — confidence 40..59 (or no decisive route): a
      :class:`DispatchDecision` is attached for the user;
    * ``refused`` — confidence < 40: no route is dispatchable.
    """

    status: str  # "auto_dispatch" | "needs_approval" | "decision" | "refused"
    route: Optional[DispatchRoute]
    confidence: int
    precedence_layer: str  # which §5.8 step decided
    decision: Optional[DispatchDecision] = None
    bindings: Mapping[str, list] = field(default_factory=dict)
    reasons: tuple[str, ...] = ()
    scores: tuple[RouteScore, ...] = ()

    @property
    def is_auto_dispatch(self) -> bool:
        return self.status == "auto_dispatch"

    @property
    def is_refused(self) -> bool:
        return self.status == "refused"

    @property
    def needs_decision(self) -> bool:
        return self.status == "decision"


# ---------------------------------------------------------------------------
# The runtime
# ---------------------------------------------------------------------------


class DispatchRuntime:
    """Wires FrontDock output → a selected route (Standards Delta v0 §5.8).

    Does NOT execute stations (P-EXEC-01). Construct with a route registry +
    worker registry (defaults load the packaged profiles) and an optional
    :class:`RouteSuggester` (the LLM boundary; ``None`` ⇒ deterministic-only).
    The single entry point is :meth:`select_route`.
    """

    def __init__(
        self,
        route_registry: Optional[DispatchRouteRegistry] = None,
        worker_registry: Optional[DispatchWorkerRegistry] = None,
        suggester: Optional[RouteSuggester] = None,
    ) -> None:
        self._routes = route_registry if route_registry is not None else default_route_registry()
        self._workers = (
            worker_registry if worker_registry is not None else default_worker_registry()
        )
        self._suggester = suggester if suggester is not None else RouteSuggester(None)

    # -- public API ----------------------------------------------------------

    @property
    def routes(self) -> DispatchRouteRegistry:
        return self._routes

    @property
    def workers(self) -> DispatchWorkerRegistry:
        return self._workers

    def select_route(
        self,
        intake: FrontDockResult,
        *,
        explicit_route: Optional[str] = None,
        project_rules: Optional[ProjectRules] = None,
        now: Optional[datetime] = None,
    ) -> SelectionOutcome:
        """Select a route for an intake bundle using the §5.8 precedence order.

        Walks the precedence layers in order and returns the first decisive
        outcome:

        1. explicit user route → bind + select (bypasses scoring; still binds);
        2. project rule → forced route for the job's intent;
        3. exact route_trigger match → a unique route declaring the intent;
        4. intent classification → FrontDock already classified the intent;
        5. registry tie-break → alpha scoring across all candidates;
        6. else → DispatchDecision.

        Confidence thresholds (§5.8) gate the deterministic-scoring outcomes:
        ``>=60`` auto-dispatch (autonomy permitting) / ``40-59`` decision /
        ``<40`` refuse.
        """

        job = intake.job
        created_at = now or datetime.now(timezone.utc)
        # FrontDock surfaces *material* missing info as a blocking decision; the
        # soft probe gaps it already downgraded to assumptions do NOT count as
        # material (penalizing those would refuse every ordinary task — §5.8).
        material_missing = intake.is_blocked

        # Layer 1 — explicit user route (highest precedence; user named it).
        if explicit_route:
            return self._select_explicit(job, explicit_route, created_at)

        # Layer 2 — project rule (forced route for the job's intent).
        if project_rules is not None and not project_rules.is_empty:
            forced = project_rules.route_for_intent(job.detected_intent)
            if forced:
                return self._select_forced(job, forced, created_at, layer="project_rule")

        # Layers 3-5 use the live candidate set + an optional LLM suggestion.
        candidate_ids = self._routes.ids()
        suggestion = self._suggester.suggest(job.raw_request, candidate_ids)

        # Layer 3 — exact route_trigger match (a route declares the intent).
        trigger_routes = self._routes.for_intent(job.detected_intent)
        if len(trigger_routes) == 1:
            route = trigger_routes[0]
            score = score_route(
                route, job, self._workers,
                suggestion=suggestion, has_material_missing_info=material_missing,
            )
            return self._finalize_scored(
                job, route, score, created_at, layer="exact_route_trigger_match"
            )

        # Layer 4 — intent classification. FrontDock has already classified the
        # intent; if exactly one route family matches it deterministically (and
        # layer 3 did not fire because of >1 trigger route), prefer the route
        # whose id encodes the intent. This is the deterministic keyword path.
        if len(trigger_routes) > 1:
            intent_matched = [r for r in trigger_routes if job.detected_intent in r.id]
            if len(intent_matched) == 1:
                route = intent_matched[0]
                score = score_route(
                    route, job, self._workers,
                    suggestion=suggestion, has_material_missing_info=material_missing,
                )
                return self._finalize_scored(
                    job, route, score, created_at, layer="intent_classification"
                )

        # Layer 5 — registry tie-break (numeric alpha scoring across candidates).
        scores = self._score_all(
            job, suggestion=suggestion, has_material_missing_info=material_missing
        )
        if scores:
            best = scores[0]
            # A clear single winner (strictly higher than the runner-up) is the
            # tie-break result; an actual tie falls through to a decision.
            if len(scores) == 1 or scores[0].score > scores[1].score:
                route = self._routes.get(best.route_id)
                return self._finalize_scored(
                    job, route, best, created_at, layer="registry_tie_break", scores=scores
                )

        # Layer 6 — no decisive route: ask the user.
        return self._build_decision_outcome(
            job,
            created_at,
            layer="dispatch_decision",
            confidence=scores[0].confidence if scores else 0,
            reason=(
                "No route could be selected deterministically (ambiguous tie or "
                "no candidate)."
            ),
            scores=tuple(scores),
        )

    # -- precedence-layer helpers -------------------------------------------

    def _select_explicit(
        self, job: DispatchJob, route_id: str, created_at: datetime
    ) -> SelectionOutcome:
        """Layer 1: explicit user route. Must exist + bind, but bypasses scoring."""

        normalized = self._normalize_route_id(route_id)
        if not self._routes.has(normalized):
            return self._build_decision_outcome(
                job,
                created_at,
                layer="explicit_route",
                confidence=0,
                reason=(
                    f"Explicit route {route_id!r} is not a known route "
                    f"(known: {self._routes.ids()})."
                ),
            )
        route = self._routes.get(normalized)
        try:
            bindings = bind_route(route, self._workers)
        except RouteResolutionError as exc:
            return self._refuse(
                job,
                route,
                confidence=0,
                layer="explicit_route",
                reasons=(f"Explicit route cannot be staffed: {exc}",),
            )
        # Explicit user route is treated as full confidence; autonomy still gates
        # whether it auto-dispatches.
        return self._dispatch_or_approve(
            job,
            route,
            confidence=100,
            layer="explicit_route",
            bindings=bindings,
            reasons=("User explicitly selected this route.",),
        )

    def _select_forced(
        self, job: DispatchJob, route_id: str, created_at: datetime, *, layer: str
    ) -> SelectionOutcome:
        """Layer 2: project-rule forced route. Must exist + bind."""

        normalized = self._normalize_route_id(route_id)
        if not self._routes.has(normalized):
            return self._build_decision_outcome(
                job,
                created_at,
                layer=layer,
                confidence=0,
                reason=(
                    f"Project rule names route {route_id!r}, which is not registered "
                    f"(known: {self._routes.ids()})."
                ),
            )
        route = self._routes.get(normalized)
        try:
            bindings = bind_route(route, self._workers)
        except RouteResolutionError as exc:
            return self._refuse(
                job,
                route,
                confidence=0,
                layer=layer,
                reasons=(f"Project-rule route cannot be staffed: {exc}",),
            )
        return self._dispatch_or_approve(
            job,
            route,
            confidence=100,
            layer=layer,
            bindings=bindings,
            reasons=("Project rule forced this route for the detected intent.",),
        )

    def _finalize_scored(
        self,
        job: DispatchJob,
        route: DispatchRoute,
        score: RouteScore,
        created_at: datetime,
        *,
        layer: str,
        scores: tuple[RouteScore, ...] = (),
    ) -> SelectionOutcome:
        """Apply the §5.8 confidence thresholds to a scored route."""

        confidence = score.confidence
        reasons = tuple(f"{name}: {weight:+d}" for name, weight in score.components)

        if confidence >= THRESHOLD_AUTO_DISPATCH:
            try:
                bindings = bind_route(route, self._workers)
            except RouteResolutionError as exc:
                # Defensive: scoring penalized an unbindable route, so it should
                # not reach >=60, but never auto-dispatch an unstaffable route.
                return self._refuse(
                    job, route, confidence=confidence, layer=layer,
                    reasons=(f"Route cannot be staffed: {exc}",), scores=scores,
                )
            return self._dispatch_or_approve(
                job, route, confidence=confidence, layer=layer,
                bindings=bindings, reasons=reasons, scores=scores,
            )

        if confidence >= THRESHOLD_DECISION_FLOOR:
            return self._build_decision_outcome(
                job, created_at, layer=layer, confidence=confidence,
                reason=(
                    f"Route {route.id!r} scored {confidence} (40-59 band): "
                    "confirm before dispatching."
                ),
                recommended_route_id=route.id, scores=scores,
            )

        # confidence < 40 → refuse.
        return self._refuse(
            job, route, confidence=confidence, layer=layer,
            reasons=(
                f"Best route {route.id!r} scored {confidence} (< {THRESHOLD_DECISION_FLOOR}): "
                "refusing to dispatch.",
            ),
            scores=scores,
        )

    # -- outcome builders ----------------------------------------------------

    def _dispatch_or_approve(
        self,
        job: DispatchJob,
        route: DispatchRoute,
        *,
        confidence: int,
        layer: str,
        bindings: Mapping[str, list],
        reasons: tuple[str, ...],
        scores: tuple[RouteScore, ...] = (),
    ) -> SelectionOutcome:
        """A selected, bound route: auto-dispatch iff autonomy permits (§5.8)."""

        if job.autonomy_mode in _AUTO_DISPATCH_MODES:
            status = "auto_dispatch"
        else:
            # advisory / draft: route is chosen but not auto-dispatched.
            status = "needs_approval"
        return SelectionOutcome(
            status=status,
            route=route,
            confidence=confidence,
            precedence_layer=layer,
            bindings=bindings,
            reasons=reasons,
            scores=scores,
        )

    def _refuse(
        self,
        job: DispatchJob,
        route: Optional[DispatchRoute],
        *,
        confidence: int,
        layer: str,
        reasons: tuple[str, ...],
        scores: tuple[RouteScore, ...] = (),
    ) -> SelectionOutcome:
        """A refusal: no dispatchable route (< 40 confidence or unstaffable)."""

        return SelectionOutcome(
            status="refused",
            route=route,
            confidence=confidence,
            precedence_layer=layer,
            reasons=reasons,
            scores=scores,
        )

    def _build_decision_outcome(
        self,
        job: DispatchJob,
        created_at: datetime,
        *,
        layer: str,
        confidence: int,
        reason: str,
        recommended_route_id: Optional[str] = None,
        scores: tuple[RouteScore, ...] = (),
    ) -> SelectionOutcome:
        """Build a DispatchDecision asking the user which route to run (§5.8 step 6)."""

        options = self._route_options()
        recommended = recommended_route_id or (options[0].id if options else "no_route")
        decision = DispatchDecision(
            id=f"decision_{job.id}_route_selection",
            job_id=job.id,
            created_at=created_at,
            scope=DecisionScope.JOB,
            title="Choose a route for this request",
            question=(
                "The dispatch runtime could not select a single route with enough "
                f"confidence. {reason} Choose a route to run, or cancel."
            ),
            reason=(
                "Standards Delta v0 §5.8 precedence did not yield a confident "
                f"auto-dispatch (deciding layer: {layer})."
            ),
            risk_level=RiskLevel.MEDIUM,
            options=options + [_cancel_option()],
            recommendation=DecisionRecommendation(
                option_id=recommended,
                rationale="Highest-scoring / closest-matching route under alpha scoring.",
            ),
            default_action=DecisionDefaultAction(
                option_id=recommended,
                auto_apply_after=AutoApplyAfter.NEVER,
            ),
            status=DecisionStatus.PENDING,
        )
        return SelectionOutcome(
            status="decision",
            route=None,
            confidence=confidence,
            precedence_layer=layer,
            decision=decision,
            reasons=(reason,),
            scores=scores,
        )

    # -- internals -----------------------------------------------------------

    def _score_all(
        self,
        job: DispatchJob,
        *,
        suggestion: Optional[RouteSuggestion],
        has_material_missing_info: bool = False,
    ) -> list[RouteScore]:
        """Score every registered route, sorted by score desc then id asc."""

        scored = [
            score_route(
                route, job, self._workers,
                suggestion=suggestion,
                has_material_missing_info=has_material_missing_info,
            )
            for route in self._routes.all()
        ]
        scored.sort(key=lambda s: (-s.score, s.route_id))
        return scored

    def _route_options(self) -> list[DecisionOption]:
        """One decision option per registered route (for the route-choice decision)."""

        options: list[DecisionOption] = []
        for route in self._routes.all():
            options.append(
                DecisionOption(
                    id=route.id,
                    label=route.name,
                    description=route.description,
                    tradeoffs=[],
                )
            )
        return options

    @staticmethod
    def _normalize_route_id(route_id: str) -> str:
        """Accept a bare route name (``code_task``) or a full id (``route.code_task.v1``).

        A bare name with no ``route.`` prefix is resolved to the highest packaged
        ``route.<name>.v1`` form so ``--route=code_task`` works as the CLI signature
        promises (Standards Delta v0 §14.1). A value already shaped like a full id
        is returned unchanged.
        """

        if route_id.startswith("route."):
            return route_id
        return f"route.{route_id}.v1"


def _cancel_option() -> DecisionOption:
    return DecisionOption(
        id="cancel",
        label="Cancel the request",
        description="Do not dispatch this request to any route.",
        tradeoffs=["No work is performed."],
    )


# Re-export the FrontDock TipClient so callers that wire one boundary for both
# intent classification and route suggestion have a single import surface.
__all__ = [
    # scoring constants + metadata
    "DISPATCH_SCORING_METADATA",
    "SCORE_EXPLICIT_ROUTE_REQUESTED",
    "SCORE_EXACT_ROUTE_TRIGGER_MATCH",
    "SCORE_INTENT_MATCH",
    "SCORE_FILE_CONTEXT_HINT_MATCH",
    "SCORE_RISK_COMPATIBILITY",
    "SCORE_AUTONOMY_COMPATIBILITY",
    "SCORE_ORDERING_HINT_MATCH",
    "SCORE_RISK_MISMATCH",
    "SCORE_MISSING_REQUIRED_INFO",
    "SCORE_FORBIDDEN_ACTION_REQUIRED",
    "THRESHOLD_AUTO_DISPATCH",
    "THRESHOLD_DECISION_FLOOR",
    # LLM suggestion (advisory only)
    "RouteSuggestion",
    "InvalidRouteSuggestion",
    "RouteSuggestClient",
    "RouteSuggester",
    "TipClient",
    # project rules
    "ProjectRules",
    # scoring
    "RouteScore",
    "score_route",
    # selection
    "SelectionOutcome",
    "DispatchRuntime",
]
