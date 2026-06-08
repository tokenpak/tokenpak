"""FrontDock intake module — turns a raw request into scoped Dispatch records.

The Front Dock is the first thing a request hits (Standards Delta v0 §13 item 3).
It is **not a worker**: it does not execute the work, call a builder/reviewer
station, or mutate the workspace. It is a single deterministic-first intake module
that reads a raw request and produces:

* a :class:`~tokenpak.orchestration.dispatch.models.job.DispatchJob` — the intake
  record (detected intent, route hint, drafted assumptions, missing info, risk
  flags, interpreted autonomy mode);
* a *draft* :class:`~tokenpak.orchestration.dispatch.models.manifest.DispatchManifest`
  — a scoped work contract sketch (status ``draft`` / ``needs_decision``);
* an *optional* blocking
  :class:`~tokenpak.orchestration.dispatch.models.decision.DispatchDecision` —
  created when, and only when, ``missing_info`` contains a **high-risk** item
  (§4.6). The Front Dock never silently assumes for those.

Design contracts (Standards Delta v0 §5.8, §13):

* **Deterministic path works with NO LLM.** Intent detection runs a fixed
  keyword/heuristic battery first; when that resolves a confident intent, no LLM
  call is made. The LLM is strictly a *fallback* for ambiguous intent / judgment.
* **LLM calls go through TIP. No direct provider calls.** The dispatch runtime /
  TIP wiring is unbuilt, so this module defines a thin injectable boundary — the
  :class:`TipClient` ``Protocol`` — and takes it as a constructor dependency. In
  production that is the real TIP client (wired by P-RUNTIME-01 / P-EXEC-01); in
  tests it is a deterministic mock. **This module imports / calls no provider
  SDK.** Passing ``None`` for the client is legal: the deterministic path is fully
  functional without an LLM, and an ambiguous request with no client falls back
  to the ``unknown`` intent (never a provider call).
* **Front Dock Rule (§13 item 3).** Ask only for information that *materially
  changes the outcome*. Low-value gaps are recorded as assumptions, not surfaced
  as blocking questions. Only high-risk missing information triggers a blocking
  :class:`DispatchDecision`.

User-facing terminology is plain ``Worker`` / ``Route`` / ``Station`` (§11): this
module emits route hints like ``route.code_task.v1`` and never the string
"Fleet Worker".
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Protocol, Union, runtime_checkable

from .models.common import (
    AcceptanceCriterion,
    ManifestPermissions,
    QualityRequirements,
)
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
    DispatchJobStatus,
    ManifestStatus,
    RiskLevel,
)
from .models.job import DispatchJob
from .models.manifest import DispatchManifest

# ---------------------------------------------------------------------------
# Intent + route vocabulary (v0.1-alpha)
# ---------------------------------------------------------------------------

# v0.1-alpha intents (Standards Delta v0 §13 routes: quick_answer / code_task /
# doc_task, plus the open-world ``unknown``). These are the only strings the
# Front Dock will ever place in ``DispatchJob.detected_intent``.
INTENT_CODE_TASK = "code_task"
INTENT_DOC_TASK = "doc_task"
INTENT_QUICK_ANSWER = "quick_answer"
INTENT_UNKNOWN = "unknown"

KNOWN_INTENTS: frozenset[str] = frozenset(
    {INTENT_CODE_TASK, INTENT_DOC_TASK, INTENT_QUICK_ANSWER, INTENT_UNKNOWN}
)

# Intent → route hint (Standards Delta v0 §4.3 id form "route.<name>.v<n>"). The
# ``unknown`` intent has no route hint (None) — routing is deferred to a decision
# or an explicit ``--route`` override downstream.
INTENT_TO_ROUTE_HINT: dict[str, str | None] = {
    INTENT_CODE_TASK: "route.code_task.v1",
    INTENT_DOC_TASK: "route.doc_task.v1",
    INTENT_QUICK_ANSWER: "route.quick_answer.v1",
    INTENT_UNKNOWN: None,
}

# Deterministic keyword sets per intent (Standards Delta v0 §5.8 step 4: "intent
# classification match (deterministic keyword set)"). Matched as whole words,
# case-insensitively. Order of evaluation is fixed (see _INTENT_PRECEDENCE) so the
# rule path is fully deterministic.
_INTENT_KEYWORDS: dict[str, frozenset[str]] = {
    INTENT_CODE_TASK: frozenset(
        {
            "code",
            "implement",
            "fix",
            "bug",
            "patch",
            "refactor",
            "function",
            "class",
            "module",
            "test",
            "tests",
            "endpoint",
            "api",
            "feature",
        }
    ),
    INTENT_DOC_TASK: frozenset(
        {
            "doc",
            "docs",
            "document",
            "documentation",
            "readme",
            "changelog",
            "guide",
            "tutorial",
            "write-up",
            "writeup",
        }
    ),
    INTENT_QUICK_ANSWER: frozenset(
        {
            "what",
            "why",
            "how",
            "when",
            "explain",
            "define",
            "question",
            "summarize",
            "summarise",
        }
    ),
}

# Tie-break precedence when more than one deterministic intent matches: a request
# that says "fix the bug and document it" is a code_task first. doc_task outranks
# quick_answer (a doc request is more specific than a bare question word).
_INTENT_PRECEDENCE: tuple[str, ...] = (
    INTENT_CODE_TASK,
    INTENT_DOC_TASK,
    INTENT_QUICK_ANSWER,
)

_WORD_RE = re.compile(r"[a-z0-9_]+")


# ---------------------------------------------------------------------------
# Risk-flag registry (PAKPlan-style, simple registered set for alpha)
# ---------------------------------------------------------------------------

# Standards Delta v0 §4.1: ``risk_flags`` is "registry-bound (PAKPlan risk_flag
# registry)". The full PAKPlan registry is a later concern; for v0.1-alpha a
# simple registered set is sufficient (this module's docstring + the packet say
# so). Each flag maps to a severity; HIGH/CRITICAL flags are the ones that make a
# corresponding missing-info gap *material* enough to block (§13 Front Dock Rule).
RISK_FLAG_REGISTRY: dict[str, RiskLevel] = {
    # High-stakes surfaces — a gap here materially changes the outcome.
    "touches_secrets": RiskLevel.CRITICAL,
    "touches_credentials": RiskLevel.CRITICAL,
    "deletes_data": RiskLevel.HIGH,
    "schema_migration": RiskLevel.HIGH,
    "public_release_surface": RiskLevel.HIGH,
    "external_side_effect": RiskLevel.HIGH,
    "auth_or_permissions": RiskLevel.HIGH,
    # Lower-stakes surfaces — recorded, but not on their own blocking.
    "touches_cli": RiskLevel.MEDIUM,
    "touches_tests": RiskLevel.LOW,
    "touches_docs": RiskLevel.LOW,
}

# Deterministic keyword → risk-flag mapping. Whole-word, case-insensitive. Only
# flags that exist in RISK_FLAG_REGISTRY may be produced.
_RISK_KEYWORDS: dict[str, str] = {
    "secret": "touches_secrets",
    "secrets": "touches_secrets",
    "credential": "touches_credentials",
    "credentials": "touches_credentials",
    "password": "touches_credentials",
    "token": "touches_credentials",
    "delete": "deletes_data",
    "drop": "deletes_data",
    "migration": "schema_migration",
    "migrate": "schema_migration",
    "schema": "schema_migration",
    "release": "public_release_surface",
    "publish": "public_release_surface",
    "deploy": "external_side_effect",
    "auth": "auth_or_permissions",
    "permission": "auth_or_permissions",
    "permissions": "auth_or_permissions",
    "cli": "touches_cli",
    "docs": "touches_docs",
    "documentation": "touches_docs",
}

# Risk levels that make an associated missing-info item "material" (Front Dock
# Rule): only HIGH and CRITICAL gaps block. MEDIUM/LOW gaps become assumptions.
_BLOCKING_RISK_LEVELS: frozenset[RiskLevel] = frozenset(
    {RiskLevel.HIGH, RiskLevel.CRITICAL}
)


def is_registered_risk_flag(flag: str) -> bool:
    """Return ``True`` iff ``flag`` is in :data:`RISK_FLAG_REGISTRY`."""

    return flag in RISK_FLAG_REGISTRY


def risk_flag_level(flag: str) -> RiskLevel:
    """Return the registered :class:`RiskLevel` for ``flag`` (fail-loud on unknown).

    Raises :class:`UnknownRiskFlagError` for an unregistered flag — the registry
    is the single source of truth and the Front Dock never invents severities.
    """

    try:
        return RISK_FLAG_REGISTRY[flag]
    except KeyError as exc:
        raise UnknownRiskFlagError(flag) from exc


class UnknownRiskFlagError(ValueError):
    """Raised when a risk flag is not in :data:`RISK_FLAG_REGISTRY`."""

    def __init__(self, flag: str) -> None:
        self.flag = flag
        known = ", ".join(sorted(RISK_FLAG_REGISTRY))
        super().__init__(
            f"unknown Dispatch risk flag {flag!r}. Registered flags: {known}."
        )


# ---------------------------------------------------------------------------
# Injectable LLM boundary (routes through TIP at runtime; NO provider SDK here)
# ---------------------------------------------------------------------------


@runtime_checkable
class TipClient(Protocol):
    """Injected LLM boundary for FrontDock — routes through TIP at runtime.

    The Front Dock is deterministic-first; this client is consulted **only** when
    the rule battery cannot confidently resolve an intent. In production the
    concrete binding is the TIP client (wired by P-RUNTIME-01 / P-EXEC-01); in
    tests it is a deterministic mock. **No real provider SDK is imported or called
    by this module** — all LLM access goes through this contract, which itself
    goes through TIP (and therefore Spend Guard, §8) at runtime.

    A conforming client implements at least :meth:`classify_intent`; the optional
    :meth:`complete` method is reserved for future judgment calls (assumption
    refinement, etc.) and is not required for v0.1-alpha intent resolution.
    """

    def classify_intent(self, request: str, candidates: list[str]) -> str:
        """Return one of ``candidates`` for ``request`` (the resolved intent)."""
        ...

    def complete(self, prompt: str) -> Union[str, dict[str, Any]]:  # pragma: no cover - reserved
        """Optional free-form completion (reserved; not used by v0.1-alpha intake)."""
        ...


# ---------------------------------------------------------------------------
# Intent detection
# ---------------------------------------------------------------------------


class IntentResolution:
    """Result of intent detection: the intent plus how it was resolved.

    ``source`` is ``"deterministic"`` when the rule battery resolved it (no LLM
    call), ``"llm"`` when the injected TIP client was consulted, or
    ``"unknown"`` when neither could resolve a confident intent.
    """

    __slots__ = ("intent", "source", "matched_keywords")

    def __init__(
        self, intent: str, source: str, matched_keywords: frozenset[str] | None = None
    ) -> None:
        self.intent = intent
        self.source = source
        self.matched_keywords = matched_keywords or frozenset()

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"IntentResolution(intent={self.intent!r}, source={self.source!r}, "
            f"matched_keywords={sorted(self.matched_keywords)!r})"
        )


def _tokenize(text: str) -> set[str]:
    """Lower-case whole-word tokens (``[a-z0-9_]+``) of ``text``."""

    return set(_WORD_RE.findall(text.lower()))


def detect_intent_deterministic(request: str) -> IntentResolution | None:
    """Resolve intent by rules alone, or return ``None`` when ambiguous.

    Returns an :class:`IntentResolution` (``source="deterministic"``) when exactly
    one intent's keyword set matches, or when several match but precedence picks a
    single winner unambiguously. Returns ``None`` when the request matches no
    keywords (ambiguous → LLM fallback) — that is the ONLY case that escalates to
    the LLM. Multiple matches are NOT ambiguous: precedence resolves them
    deterministically without an LLM call.
    """

    tokens = _tokenize(request)
    matches: dict[str, frozenset[str]] = {}
    for intent, keywords in _INTENT_KEYWORDS.items():
        hit = keywords & tokens
        if hit:
            matches[intent] = frozenset(hit)

    if not matches:
        # No deterministic signal at all → genuinely ambiguous → LLM fallback.
        return None

    # One or more matched: precedence picks a single deterministic winner.
    for intent in _INTENT_PRECEDENCE:
        if intent in matches:
            return IntentResolution(
                intent=intent,
                source="deterministic",
                matched_keywords=matches[intent],
            )
    # Unreachable: every key in _INTENT_KEYWORDS is in _INTENT_PRECEDENCE.
    return None  # pragma: no cover


def detect_risk_flags(request: str) -> list[str]:
    """Deterministically detect registered risk flags in ``request``.

    Whole-word, case-insensitive keyword match against :data:`_RISK_KEYWORDS`.
    Returns a de-duplicated, sorted list of registered flags (every returned flag
    is guaranteed to be in :data:`RISK_FLAG_REGISTRY`).
    """

    tokens = _tokenize(request)
    flags: set[str] = set()
    for token in tokens:
        flag = _RISK_KEYWORDS.get(token)
        if flag is not None:
            flags.add(flag)
    # Invariant: only registered flags are produced.
    return sorted(f for f in flags if f in RISK_FLAG_REGISTRY)


# ---------------------------------------------------------------------------
# Front Dock
# ---------------------------------------------------------------------------

# Per-intent missing-information probes. Each entry is (label, risk_flag|None):
# the risk flag (if any) determines whether a gap is *material* (high/critical →
# blocking) under the Front Dock Rule. ``None`` means "always an assumption, never
# blocking". These are alpha heuristics, not an exhaustive elicitation model.
_INTENT_MISSING_INFO_PROBES: dict[str, tuple[tuple[str, str | None], ...]] = {
    INTENT_CODE_TASK: (
        ("target files or module to change", None),
        ("acceptance criteria / definition of done", None),
    ),
    INTENT_DOC_TASK: (
        ("target document to create or update", None),
        ("intended audience", None),
    ),
    INTENT_QUICK_ANSWER: (),
}

# Default assumptions drafted per intent when the corresponding info is missing
# but NOT material (Front Dock Rule: assume rather than ask).
_INTENT_DEFAULT_ASSUMPTIONS: dict[str, tuple[str, ...]] = {
    INTENT_CODE_TASK: (
        "Scope is limited to the current repository working tree.",
        "No external side effects (deploy / dependency install / secret change).",
    ),
    INTENT_DOC_TASK: (
        "Output is a Markdown document in the repository.",
    ),
    INTENT_QUICK_ANSWER: (
        "A concise prose answer is sufficient; no files are produced.",
    ),
    INTENT_UNKNOWN: (),
}


class FrontDockResult:
    """The Front Dock's output bundle (§13 item 3).

    Carries the intake :class:`DispatchJob`, the draft
    :class:`DispatchManifest`, and an optional blocking
    :class:`DispatchDecision` (present iff a high-risk gap was detected). All
    three are schema-valid Pydantic models.
    """

    __slots__ = ("job", "manifest", "decision", "intent_resolution")

    def __init__(
        self,
        job: DispatchJob,
        manifest: DispatchManifest,
        decision: DispatchDecision | None,
        intent_resolution: IntentResolution,
    ) -> None:
        self.job = job
        self.manifest = manifest
        self.decision = decision
        self.intent_resolution = intent_resolution

    @property
    def is_blocked(self) -> bool:
        """True iff the Front Dock produced a blocking decision (high-risk gap)."""

        return self.decision is not None


class FrontDock:
    """Request intake: raw request → DispatchJob + draft manifest (+ optional decision).

    Deterministic-first: :meth:`intake` resolves intent by rules and only consults
    the injected :class:`TipClient` when the rules cannot. It is **not a worker**:
    it produces records, never executes them. Construct with an optional TIP
    client (``None`` is legal — the deterministic path needs no LLM; an ambiguous
    request with no client resolves to ``unknown`` rather than calling a provider).
    """

    def __init__(self, tip_client: TipClient | None = None) -> None:
        self._tip = tip_client

    # ---- intent resolution -------------------------------------------------

    def resolve_intent(self, request: str) -> IntentResolution:
        """Resolve intent: rules first, LLM fallback only when ambiguous.

        Deterministic rules run first. If they resolve a confident intent, the
        injected client is **never** called (the LLM is strictly a fallback). If
        the rules are ambiguous (no keyword signal) and a TIP client is present,
        exactly one ``classify_intent`` call is made; its answer is validated
        against :data:`KNOWN_INTENTS` (an out-of-vocabulary answer falls back to
        ``unknown``). With no client, ambiguity resolves to ``unknown`` — never a
        provider call.
        """

        deterministic = detect_intent_deterministic(request)
        if deterministic is not None:
            return deterministic

        if self._tip is None:
            return IntentResolution(intent=INTENT_UNKNOWN, source="unknown")

        # Ambiguous + client present → exactly one LLM fallback call (via TIP).
        candidates = [
            INTENT_CODE_TASK,
            INTENT_DOC_TASK,
            INTENT_QUICK_ANSWER,
            INTENT_UNKNOWN,
        ]
        answer = self._tip.classify_intent(request, candidates)
        if not isinstance(answer, str) or answer not in KNOWN_INTENTS:
            # Out-of-vocabulary LLM answer → fail safe to unknown, do not invent.
            return IntentResolution(intent=INTENT_UNKNOWN, source="llm")
        return IntentResolution(intent=answer, source="llm")

    # ---- the intake itself -------------------------------------------------

    def intake(
        self,
        raw_request: str,
        *,
        autonomy_mode: AutonomyMode | str = AutonomyMode.DISPATCH_WITH_APPROVAL,
        source_task_packet_id: str | None = None,
        job_id: str | None = None,
        manifest_id: str | None = None,
        now: datetime | None = None,
    ) -> FrontDockResult:
        """Run intake over ``raw_request`` and return the output bundle.

        Steps: intent detection (deterministic + LLM fallback) → risk-flag tagging
        → assumption drafting + missing-info detection → route hint → draft
        acceptance criteria → autonomy-mode interpretation → assemble a
        :class:`DispatchJob` and a draft :class:`DispatchManifest`. When
        ``missing_info`` contains a high-risk item, also create a **blocking**
        :class:`DispatchDecision` and set the manifest status to ``needs_decision``.
        """

        created_at = now or datetime.now(timezone.utc)
        mode = (
            autonomy_mode
            if isinstance(autonomy_mode, AutonomyMode)
            else AutonomyMode(autonomy_mode)
        )

        resolution = self.resolve_intent(raw_request)
        intent = resolution.intent
        route_hint = INTENT_TO_ROUTE_HINT.get(intent)

        risk_flags = detect_risk_flags(raw_request)

        assumptions, missing_info, blocking_gaps = self._draft_assumptions_and_gaps(
            intent=intent, risk_flags=risk_flags
        )

        job_id = job_id or self._derive_id("job", created_at)
        manifest_id = manifest_id or self._derive_id("manifest", created_at)

        # A blocking decision is created iff there is at least one high-risk gap.
        decision: DispatchDecision | None = None
        if blocking_gaps:
            decision = self._build_blocking_decision(
                job_id=job_id,
                blocking_gaps=blocking_gaps,
                created_at=created_at,
            )

        job = DispatchJob(
            id=job_id,
            created_at=created_at,
            raw_request=raw_request,
            source_task_packet_id=source_task_packet_id,
            detected_intent=intent,
            route_hint=route_hint,
            assumptions=assumptions,
            missing_info=missing_info,
            risk_flags=risk_flags,
            autonomy_mode=mode,
            status=DispatchJobStatus.DRAFT,
        )

        manifest = self._build_draft_manifest(
            manifest_id=manifest_id,
            job_id=job_id,
            route_hint=route_hint,
            intent=intent,
            raw_request=raw_request,
            autonomy_mode=mode,
            blocked=decision is not None,
        )

        return FrontDockResult(
            job=job,
            manifest=manifest,
            decision=decision,
            intent_resolution=resolution,
        )

    # ---- assumption drafting + missing-info / Front Dock Rule --------------

    def _draft_assumptions_and_gaps(
        self, *, intent: str, risk_flags: list[str]
    ) -> tuple[list[str], list[str], list[tuple[str, RiskLevel]]]:
        """Draft assumptions + detect missing info, applying the Front Dock Rule.

        Returns ``(assumptions, missing_info, blocking_gaps)``.

        * ``assumptions`` — defaults filled for non-material gaps (don't over-ask).
        * ``missing_info`` — every detected information gap (material + non-material).
        * ``blocking_gaps`` — the subset of gaps that are *material* (tied to a
          HIGH/CRITICAL risk flag); these and only these drive a blocking decision.

        Front Dock Rule (§13 item 3): only material gaps are surfaced as blocking
        questions. Non-material gaps become assumptions instead of questions.
        """

        assumptions: list[str] = list(_INTENT_DEFAULT_ASSUMPTIONS.get(intent, ()))
        missing_info: list[str] = []
        blocking_gaps: list[tuple[str, RiskLevel]] = []

        # 1) Intent-shaped information probes. Each unmet probe is recorded as
        #    missing info; if it carries no material risk flag it is downgraded to
        #    an assumption (Front Dock Rule), otherwise it is a blocking gap.
        for label, probe_flag in _INTENT_MISSING_INFO_PROBES.get(intent, ()):
            missing_info.append(label)
            level = (
                risk_flag_level(probe_flag)
                if probe_flag is not None and is_registered_risk_flag(probe_flag)
                else RiskLevel.LOW
            )
            if level in _BLOCKING_RISK_LEVELS:
                blocking_gaps.append((label, level))
            else:
                assumptions.append(
                    f"Assuming a sensible default for: {label} (no material risk)."
                )

        # 2) Risk-flag-driven material gaps. A HIGH/CRITICAL risk flag detected in
        #    the request is a gap that materially changes the outcome — the Front
        #    Dock asks (never silently assumes) for these (§4.6 + Front Dock Rule).
        for flag in risk_flags:
            level = risk_flag_level(flag)
            if level in _BLOCKING_RISK_LEVELS:
                label = f"explicit confirmation for high-risk surface: {flag}"
                missing_info.append(label)
                blocking_gaps.append((label, level))
            else:
                # Low/medium risk → recorded as an assumption, not a question.
                assumptions.append(
                    f"Proceeding under low/medium-risk handling for: {flag}."
                )

        return assumptions, missing_info, blocking_gaps

    # ---- record builders ---------------------------------------------------

    def _build_draft_manifest(
        self,
        *,
        manifest_id: str,
        job_id: str,
        route_hint: str | None,
        intent: str,
        raw_request: str,
        autonomy_mode: AutonomyMode,
        blocked: bool,
    ) -> DispatchManifest:
        """Assemble the draft :class:`DispatchManifest` (status draft/needs_decision)."""

        acceptance_criteria = self._draft_acceptance_criteria(intent)
        permissions = ManifestPermissions(autonomy_mode=autonomy_mode)
        quality = QualityRequirements(
            test_required=intent == INTENT_CODE_TASK,
            review_required=intent in (INTENT_CODE_TASK, INTENT_DOC_TASK),
            docs_required=intent == INTENT_DOC_TASK,
            evidence_required=False,
        )
        status = ManifestStatus.NEEDS_DECISION if blocked else ManifestStatus.DRAFT
        goal = raw_request.strip() or "(empty request)"
        return DispatchManifest(
            id=manifest_id,
            job_id=job_id,
            # route_id is required on the manifest; for an unknown intent (no
            # route hint) the manifest is a pre-routing draft and records that.
            route_id=route_hint or "route.unresolved.v0",
            goal=goal,
            acceptance_criteria=acceptance_criteria,
            permissions=permissions,
            quality_requirements=quality,
            status=status,
        )

    @staticmethod
    def _draft_acceptance_criteria(intent: str) -> list[AcceptanceCriterion]:
        """Draft starter acceptance criteria per intent (alpha heuristics)."""

        if intent == INTENT_CODE_TASK:
            return [
                AcceptanceCriterion(
                    id="ac_code_1",
                    description="The requested change is implemented in the working tree.",
                ),
                AcceptanceCriterion(
                    id="ac_code_2",
                    description="Existing tests still pass; new behavior is covered.",
                ),
            ]
        if intent == INTENT_DOC_TASK:
            return [
                AcceptanceCriterion(
                    id="ac_doc_1",
                    description="The requested document is created or updated.",
                ),
            ]
        if intent == INTENT_QUICK_ANSWER:
            return [
                AcceptanceCriterion(
                    id="ac_answer_1",
                    description="A direct, correct answer to the question is produced.",
                ),
            ]
        # unknown intent: no criteria can be drafted before routing.
        return []

    @staticmethod
    def _build_blocking_decision(
        *,
        job_id: str,
        blocking_gaps: list[tuple[str, RiskLevel]],
        created_at: datetime,
    ) -> DispatchDecision:
        """Build the blocking :class:`DispatchDecision` for high-risk missing info (§4.6).

        The decision is ``pending`` with ``auto_apply_after = never`` — the Front
        Dock NEVER silently assumes for high-risk gaps. Its risk level is the most
        severe gap's level (critical outranks high).
        """

        highest = (
            RiskLevel.CRITICAL
            if any(level is RiskLevel.CRITICAL for _, level in blocking_gaps)
            else RiskLevel.HIGH
        )
        gap_labels = [label for label, _ in blocking_gaps]
        question = (
            "This request touches high-risk surface(s) and is missing information "
            "that materially changes the outcome. Provide the missing details, or "
            "choose how to proceed: " + "; ".join(gap_labels)
        )
        return DispatchDecision(
            id=f"decision_{job_id}_frontdock_missing_info",
            job_id=job_id,
            created_at=created_at,
            scope=DecisionScope.JOB,
            title="Missing information on a high-risk request",
            question=question,
            reason=(
                "Front Dock Rule: high-risk missing information must be resolved "
                "before dispatch; the Front Dock does not silently assume "
                "(Standards Delta v0 §4.6, §13)."
            ),
            risk_level=highest,
            options=[
                DecisionOption(
                    id="provide_info",
                    label="Provide the missing details",
                    description="Supply the requested information; intake re-runs.",
                    tradeoffs=["Requires the user to answer before work starts."],
                ),
                DecisionOption(
                    id="proceed_with_constraints",
                    label="Proceed with explicit constraints",
                    description=(
                        "Continue under tight, named constraints accepting the "
                        "stated risk."
                    ),
                    tradeoffs=[
                        "Accepts a high-risk surface without full information."
                    ],
                ),
                DecisionOption(
                    id="cancel",
                    label="Cancel the request",
                    description="Do not dispatch this request.",
                    tradeoffs=["No work is performed."],
                ),
            ],
            recommendation=DecisionRecommendation(
                option_id="provide_info",
                rationale=(
                    "The gap materially changes the outcome; supplying the detail "
                    "is the safest path."
                ),
            ),
            default_action=DecisionDefaultAction(
                option_id="provide_info",
                auto_apply_after=AutoApplyAfter.NEVER,
            ),
            status=DecisionStatus.PENDING,
        )

    @staticmethod
    def _derive_id(prefix: str, created_at: datetime) -> str:
        """Derive a stable-ish record id ("<prefix>_<utc-compact>").

        The Standards Delta id form is ``<prefix>_<ulid>``; ULID minting is a Run
        Ledger concern (P-LEDGER-01). For intake-only output a deterministic
        timestamp-derived id is sufficient and keeps this module dependency-free.
        Callers that need real ULIDs pass ``job_id`` / ``manifest_id`` explicitly.
        """

        stamp = created_at.astimezone(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        return f"{prefix}_{stamp}"


__all__ = [
    # Intent vocabulary
    "INTENT_CODE_TASK",
    "INTENT_DOC_TASK",
    "INTENT_QUICK_ANSWER",
    "INTENT_UNKNOWN",
    "KNOWN_INTENTS",
    "INTENT_TO_ROUTE_HINT",
    # Risk-flag registry
    "RISK_FLAG_REGISTRY",
    "UnknownRiskFlagError",
    "is_registered_risk_flag",
    "risk_flag_level",
    # Injectable LLM boundary
    "TipClient",
    # Intent detection
    "IntentResolution",
    "detect_intent_deterministic",
    "detect_risk_flags",
    # Front Dock
    "FrontDock",
    "FrontDockResult",
]
