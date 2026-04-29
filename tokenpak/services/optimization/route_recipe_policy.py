"""Route-class compression policy (TIP-05).

Maps the canonical ``RouteClass`` strings (proposal Phase 3 Component A) to
the safe compression recipes defined in ``recipes/oss/`` plus the protected
span types each route must preserve.

This module is platform- and model-agnostic: route classes are abstract,
recipes are referenced by name, and span lists come from
``protected_spans.SpanType``. No adapter code lives here.

Public API:

    RouteClass        — string constants for canonical route classes
    FidelityTier      — string constants for canonical fidelity tiers
    RoutePolicy       — dataclass holding (recipes, protected_spans, fidelity, lossless)
    DEFAULT_POLICIES  — dict[RouteClass -> RoutePolicy] (read-only)
    get_route_policy(route_class) — returns RoutePolicy with safe defaults
    select_recipes(...)            — filter policy recipes by content sample
    apply_policy(text, policy)    — execute the policy's compression with span protection
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, List, Optional, Tuple

from .protected_spans import (
    ProtectedSpan,
    SpanType,
    detect_protected_spans,
    rewrite_outside_spans,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Canonical RouteClass + FidelityTier strings (proposal Phase 3 Component A)
# ---------------------------------------------------------------------------


class RouteClass:
    GENERAL_CHAT = "general_chat"
    STATUS_CHECK = "status_check"
    CONFIGURATION_INSPECTION = "configuration_inspection"
    CODE_GENERATION = "code_generation"
    CODE_EDIT = "code_edit"
    CODE_REVIEW = "code_review"
    DEBUGGING = "debugging"
    TEST_FAILURE = "test_failure"
    LOG_ANALYSIS = "log_analysis"
    GIT_DIFF_REVIEW = "git_diff_review"
    DOCUMENTATION_GENERATION = "documentation_generation"
    SUMMARIZATION = "summarization"
    RESEARCH = "research"
    PLANNING = "planning"
    SHELL_COMMAND_ANALYSIS = "shell_command_analysis"
    UNKNOWN = "unknown"


ALL_ROUTE_CLASSES: FrozenSet[str] = frozenset({
    RouteClass.GENERAL_CHAT,
    RouteClass.STATUS_CHECK,
    RouteClass.CONFIGURATION_INSPECTION,
    RouteClass.CODE_GENERATION,
    RouteClass.CODE_EDIT,
    RouteClass.CODE_REVIEW,
    RouteClass.DEBUGGING,
    RouteClass.TEST_FAILURE,
    RouteClass.LOG_ANALYSIS,
    RouteClass.GIT_DIFF_REVIEW,
    RouteClass.DOCUMENTATION_GENERATION,
    RouteClass.SUMMARIZATION,
    RouteClass.RESEARCH,
    RouteClass.PLANNING,
    RouteClass.SHELL_COMMAND_ANALYSIS,
    RouteClass.UNKNOWN,
})


class FidelityTier:
    LOSSLESS_REQUIRED = "lossless_required"
    SEMANTIC_SAFE = "semantic_safe"
    AGGRESSIVE_OK = "aggressive_ok"
    CACHE_RESPONSE_SAFE = "cache_response_safe"
    NO_OPTIMIZE = "no_optimize"


ALL_FIDELITY_TIERS: FrozenSet[str] = frozenset({
    FidelityTier.LOSSLESS_REQUIRED,
    FidelityTier.SEMANTIC_SAFE,
    FidelityTier.AGGRESSIVE_OK,
    FidelityTier.CACHE_RESPONSE_SAFE,
    FidelityTier.NO_OPTIMIZE,
})


@dataclass(frozen=True)
class RoutePolicy:
    """Policy describing what compression is safe for one route class.

    route_class:   the RouteClass string this policy applies to
    fidelity:      FidelityTier — strictest tier the route must satisfy
    recipe_names:  ordered list of recipe names from recipes/oss/ that this
                   route is allowed to consult; first applicable wins
    protected_span_types: span types that compression MUST preserve
    lossless_required: when True, only recipes whose ``compression_hint <=
                   max_lossless_hint`` may run; protected spans are also
                   never altered
    max_lossless_hint: the upper bound for ``compression_hint`` when
                   ``lossless_required`` is True (0.0 = never compress)

    The ``fidelity`` and ``lossless_required`` fields are redundant by
    design: ``lossless_required`` is the boolean form callers typically
    branch on, while ``fidelity`` is the canonical tier reported in
    telemetry. They MUST stay consistent (lossless_required iff
    fidelity == LOSSLESS_REQUIRED). ``__post_init__`` enforces this.
    """

    route_class: str
    fidelity: str = FidelityTier.SEMANTIC_SAFE
    recipe_names: Tuple[str, ...] = ()
    protected_span_types: Tuple[str, ...] = ()
    lossless_required: bool = False
    max_lossless_hint: float = 0.20
    target_ratio: Optional[float] = None
    notes: str = ""

    def __post_init__(self) -> None:  # pragma: no cover - tiny invariant
        if self.fidelity == FidelityTier.LOSSLESS_REQUIRED and not self.lossless_required:
            object.__setattr__(self, "lossless_required", True)
        if self.lossless_required and self.fidelity != FidelityTier.LOSSLESS_REQUIRED:
            object.__setattr__(self, "fidelity", FidelityTier.LOSSLESS_REQUIRED)


# ---------------------------------------------------------------------------
# Default policies — proposal Phase 3 Component D "Route → Recipe Mapping"
# ---------------------------------------------------------------------------


_CODE_SPANS: Tuple[str, ...] = (
    SpanType.FILE_PATH,
    SpanType.FUNCTION_SIGNATURE,
    SpanType.CLASS_SIGNATURE,
    SpanType.LINE_NUMBER,
)

_LOG_SPANS: Tuple[str, ...] = (
    SpanType.FILE_PATH,
    SpanType.STACK_TRACE_FRAME,
    SpanType.EXCEPTION_MESSAGE,
    SpanType.EXIT_CODE,
    SpanType.LINE_NUMBER,
    SpanType.URL,
)

_DIFF_SPANS: Tuple[str, ...] = (
    SpanType.FILE_PATH,
    SpanType.DIFF_HUNK_HEADER,
    SpanType.DIFF_ADDED_REMOVED_LINES,
    SpanType.LINE_NUMBER,
    SpanType.FUNCTION_SIGNATURE,
)

_CONFIG_SPANS: Tuple[str, ...] = (
    SpanType.FILE_PATH,
    SpanType.YAML_KEY,
    SpanType.CONFIG_VALUE,
    SpanType.JSON_SCHEMA,
    SpanType.URL,
    SpanType.CREDENTIAL_PLACEHOLDER,
)

_DOC_SPANS: Tuple[str, ...] = (
    SpanType.FILE_PATH,
    SpanType.URL,
    SpanType.FUNCTION_SIGNATURE,
    SpanType.CLASS_SIGNATURE,
)


DEFAULT_POLICIES: Dict[str, RoutePolicy] = {
    RouteClass.GIT_DIFF_REVIEW: RoutePolicy(
        route_class=RouteClass.GIT_DIFF_REVIEW,
        fidelity=FidelityTier.LOSSLESS_REQUIRED,
        recipe_names=("cp-git-diff-compression",),
        protected_span_types=_DIFF_SPANS,
        lossless_required=True,
        max_lossless_hint=0.40,
        notes="diff hunks, file paths, line numbers, +/- lines must survive",
    ),
    RouteClass.DEBUGGING: RoutePolicy(
        route_class=RouteClass.DEBUGGING,
        fidelity=FidelityTier.LOSSLESS_REQUIRED,
        recipe_names=(
            "cp-stack-trace-trimming",
            "cp-log-output-compression",
        ),
        protected_span_types=_LOG_SPANS,
        lossless_required=True,
        max_lossless_hint=0.50,
        notes="exception type, stack frames, exact error text must survive",
    ),
    RouteClass.TEST_FAILURE: RoutePolicy(
        route_class=RouteClass.TEST_FAILURE,
        fidelity=FidelityTier.LOSSLESS_REQUIRED,
        recipe_names=(
            "cp-log-output-compression",
            "cp-stack-trace-trimming",
        ),
        protected_span_types=_LOG_SPANS + (SpanType.COMMAND,),
        lossless_required=True,
        max_lossless_hint=0.50,
        notes="failing assertion, command, file paths, error text must survive",
    ),
    RouteClass.LOG_ANALYSIS: RoutePolicy(
        route_class=RouteClass.LOG_ANALYSIS,
        fidelity=FidelityTier.SEMANTIC_SAFE,
        recipe_names=("cp-log-output-compression",),
        protected_span_types=_LOG_SPANS,
        notes="dedupe repeated log lines; preserve error/exit code/url",
    ),
    RouteClass.CODE_REVIEW: RoutePolicy(
        route_class=RouteClass.CODE_REVIEW,
        fidelity=FidelityTier.LOSSLESS_REQUIRED,
        recipe_names=("cp-git-diff-compression",),
        protected_span_types=_DIFF_SPANS + _CODE_SPANS,
        lossless_required=True,
        max_lossless_hint=0.40,
        notes="symbols and changed lines must survive",
    ),
    RouteClass.CODE_GENERATION: RoutePolicy(
        route_class=RouteClass.CODE_GENERATION,
        fidelity=FidelityTier.LOSSLESS_REQUIRED,
        recipe_names=(),
        protected_span_types=_CODE_SPANS,
        lossless_required=True,
        max_lossless_hint=0.0,
        notes="user-supplied code/snippets must not be altered",
    ),
    RouteClass.CODE_EDIT: RoutePolicy(
        route_class=RouteClass.CODE_EDIT,
        fidelity=FidelityTier.LOSSLESS_REQUIRED,
        recipe_names=(),
        protected_span_types=_CODE_SPANS + _DIFF_SPANS,
        lossless_required=True,
        max_lossless_hint=0.0,
        notes="code-edit instructions must round-trip exactly",
    ),
    RouteClass.DOCUMENTATION_GENERATION: RoutePolicy(
        route_class=RouteClass.DOCUMENTATION_GENERATION,
        fidelity=FidelityTier.SEMANTIC_SAFE,
        recipe_names=(
            "md-code-block-compression",
            "md-table-compression",
        ),
        protected_span_types=_DOC_SPANS,
        notes="prose can be summarized; code blocks remain lossless",
    ),
    RouteClass.CONFIGURATION_INSPECTION: RoutePolicy(
        route_class=RouteClass.CONFIGURATION_INSPECTION,
        fidelity=FidelityTier.LOSSLESS_REQUIRED,
        recipe_names=(
            "cfg-cicd-compression",
            "cfg-yaml-comment-stripping",
        ),
        protected_span_types=_CONFIG_SPANS,
        lossless_required=True,
        max_lossless_hint=0.20,
        notes="keys, values, indentation, credentials must survive",
    ),
    RouteClass.STATUS_CHECK: RoutePolicy(
        route_class=RouteClass.STATUS_CHECK,
        fidelity=FidelityTier.SEMANTIC_SAFE,
        recipe_names=("gen-filler-phrase-removal",),
        protected_span_types=(),
        notes="lightweight summarization; response reuse is encouraged",
    ),
    RouteClass.SUMMARIZATION: RoutePolicy(
        route_class=RouteClass.SUMMARIZATION,
        fidelity=FidelityTier.SEMANTIC_SAFE,
        recipe_names=("gen-filler-phrase-removal",),
        protected_span_types=(SpanType.URL, SpanType.FILE_PATH),
        notes="prose-heavy; aggressive trimming OK as long as URLs/paths survive",
    ),
    RouteClass.GENERAL_CHAT: RoutePolicy(
        route_class=RouteClass.GENERAL_CHAT,
        fidelity=FidelityTier.SEMANTIC_SAFE,
        recipe_names=(),
        protected_span_types=(),
        notes="no compression by default; routing is the responsibility",
    ),
    RouteClass.RESEARCH: RoutePolicy(
        route_class=RouteClass.RESEARCH,
        fidelity=FidelityTier.SEMANTIC_SAFE,
        recipe_names=("gen-filler-phrase-removal",),
        protected_span_types=(SpanType.URL, SpanType.FILE_PATH),
    ),
    RouteClass.PLANNING: RoutePolicy(
        route_class=RouteClass.PLANNING,
        fidelity=FidelityTier.SEMANTIC_SAFE,
        recipe_names=(),
        protected_span_types=(SpanType.FILE_PATH,),
    ),
    RouteClass.SHELL_COMMAND_ANALYSIS: RoutePolicy(
        route_class=RouteClass.SHELL_COMMAND_ANALYSIS,
        fidelity=FidelityTier.LOSSLESS_REQUIRED,
        recipe_names=("cp-log-output-compression",),
        protected_span_types=_LOG_SPANS + (SpanType.COMMAND,),
        lossless_required=True,
        max_lossless_hint=0.40,
    ),
    RouteClass.UNKNOWN: RoutePolicy(
        route_class=RouteClass.UNKNOWN,
        fidelity=FidelityTier.NO_OPTIMIZE,
        recipe_names=(),
        protected_span_types=(),
        notes="fail-safe: when route detection is uncertain, do nothing",
    ),
}


def get_route_policy(route_class: Optional[str]) -> RoutePolicy:
    """Look up the default policy for a route class.

    Unknown / None / empty route classes return the UNKNOWN policy
    (``no_optimize``), which the stage will treat as ineligible. This is the
    safe default the proposal calls out in the "Non-Negotiable Rule" — when
    we can't classify a request, we don't compress it.
    """
    if not route_class:
        return DEFAULT_POLICIES[RouteClass.UNKNOWN]
    return DEFAULT_POLICIES.get(route_class, DEFAULT_POLICIES[RouteClass.UNKNOWN])


# ---------------------------------------------------------------------------
# Recipe selection: filter the policy's recipes by what the engine has loaded
# ---------------------------------------------------------------------------


def select_recipes(
    policy: RoutePolicy,
    *,
    content_sample: str = "",
    engine: Any = None,
) -> List[Any]:
    """Resolve the policy's named recipes against the OSS recipe engine.

    Returns recipe objects (CompressionRecipe instances) in policy order.
    Missing recipe names are silently skipped — the engine determines what
    is actually loadable. When ``policy.lossless_required`` is True, recipes
    whose ``compression_hint`` exceeds ``policy.max_lossless_hint`` are also
    dropped.

    ``engine`` is normally the singleton from
    ``tokenpak.compression.recipes.get_oss_engine()``; tests can pass a
    stub engine that exposes ``.get_recipe(name)``.
    """
    if not policy.recipe_names:
        return []
    eng = engine if engine is not None else _get_default_engine()
    if eng is None:
        return []
    out: List[Any] = []
    for name in policy.recipe_names:
        recipe = None
        try:
            recipe = eng.get_recipe(name)
        except Exception:
            recipe = None
        if recipe is None:
            continue
        hint = float(getattr(recipe, "compression_hint", 0.0) or 0.0)
        if policy.lossless_required and hint > policy.max_lossless_hint:
            log.debug(
                "select_recipes: dropping %s (hint=%.2f > max_lossless_hint=%.2f)",
                name, hint, policy.max_lossless_hint,
            )
            continue
        # Only apply ``recipe.matches`` filtering for content-mode recipes;
        # extension/filename/path-pattern modes need a filename and are
        # selected by name from the policy author, not by content sniffing.
        match_mode = str(getattr(recipe, "match_mode", "any") or "any")
        if (
            content_sample
            and match_mode == "content"
            and hasattr(recipe, "matches")
        ):
            try:
                if not recipe.matches(content_sample=content_sample):
                    continue
            except Exception:
                pass
        out.append(recipe)
    return out


def _get_default_engine() -> Any:
    """Lazily fetch the OSS recipe engine. Returns None if unavailable."""
    try:
        from tokenpak.compression.recipes import get_oss_engine
        return get_oss_engine()
    except Exception as exc:
        log.debug("OSS recipe engine unavailable: %s", exc)
        return None


# ---------------------------------------------------------------------------
# apply_policy — execute the compression with span protection
# ---------------------------------------------------------------------------


@dataclass
class CompressionResult:
    """Result of applying a route policy to a piece of text."""

    text: str
    bytes_in: int
    bytes_out: int
    bytes_saved: int
    recipes_applied: Tuple[str, ...] = field(default_factory=tuple)
    spans_preserved: int = 0
    skipped_reason: str = ""

    @property
    def applied(self) -> bool:
        return bool(self.recipes_applied)

    @property
    def ratio(self) -> float:
        if self.bytes_in <= 0:
            return 0.0
        return self.bytes_saved / self.bytes_in

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bytes_in": self.bytes_in,
            "bytes_out": self.bytes_out,
            "bytes_saved": self.bytes_saved,
            "recipes_applied": list(self.recipes_applied),
            "spans_preserved": self.spans_preserved,
            "skipped_reason": self.skipped_reason,
            "ratio": round(self.ratio, 4),
        }


# Lightweight, span-aware text rewrites. Each function corresponds to a
# subset of the recipe operation types declared in recipe_sdk's schema; we
# intentionally only support a safe subset here, since the route compression
# stage is supposed to favor protection over aggressive minimization.

_RE_REPEATED_NEWLINES = re.compile(r"\n{3,}")
_RE_TRAILING_WS = re.compile(r"[ \t]+\n")


def _collapse_whitespace(seg: str) -> str:
    seg = _RE_TRAILING_WS.sub("\n", seg)
    seg = _RE_REPEATED_NEWLINES.sub("\n\n", seg)
    return seg


def _strip_filler(seg: str) -> str:
    fillers = (
        "Let me ",
        "Just ",
        "Basically, ",
        "Actually, ",
        "Honestly, ",
        "I think that ",
        "Please note that ",
    )
    for token in fillers:
        seg = seg.replace(token, "")
    return seg


_SAFE_REWRITERS: Dict[str, Any] = {
    "collapse_whitespace": _collapse_whitespace,
    "remove_filler_phrases": _strip_filler,
    "remove_empty_lines": lambda s: re.sub(r"\n\s*\n", "\n", s),
}


def _operations_safe_for_route(
    recipe: Any, lossless_required: bool
) -> List[str]:
    """Pick the rewrite operation names this stage is willing to execute.

    Lossy operations (e.g. ``regex_replace``, ``deduplicate_lines``) are
    handled by the recipe engines themselves; this scaffold sticks to the
    span-safe whitelist above so the stage cannot corrupt code blocks.
    """
    safe = []
    for op in getattr(recipe, "operations", []) or []:
        if not isinstance(op, dict):
            continue
        kind = str(op.get("type", "")).strip()
        if kind in _SAFE_REWRITERS:
            safe.append(kind)
        elif kind == "deduplicate_lines" and not lossless_required:
            safe.append("remove_empty_lines")
    return safe


def apply_policy(
    text: str,
    policy: Optional[RoutePolicy] = None,
    *,
    route_class: Optional[str] = None,
    spans: Optional[List[ProtectedSpan]] = None,
    engine: Any = None,
) -> CompressionResult:
    """Compress ``text`` per ``policy`` while preserving protected spans.

    Either ``policy`` or ``route_class`` must be provided. ``policy`` wins
    when both are supplied. The compression itself is intentionally modest
    (whitespace collapse, filler removal, empty-line removal). For
    aggressive compression the upstream pipeline should call into
    ``tokenpak.compression`` directly; this stage's job is the *policy*
    of what may run, not the heavy lifting.

    ``spans`` lets callers pre-compute protected spans (useful in tests).
    """
    bytes_in = len(text.encode("utf-8"))
    if policy is None:
        policy = get_route_policy(route_class)

    if policy.fidelity == FidelityTier.NO_OPTIMIZE:
        return CompressionResult(
            text=text,
            bytes_in=bytes_in,
            bytes_out=bytes_in,
            bytes_saved=0,
            skipped_reason=f"fidelity:{policy.fidelity}",
        )

    recipes = select_recipes(
        policy, content_sample=text, engine=engine
    )
    if not recipes:
        return CompressionResult(
            text=text,
            bytes_in=bytes_in,
            bytes_out=bytes_in,
            bytes_saved=0,
            skipped_reason="no-recipes-applicable",
        )

    if spans is None:
        spans = detect_protected_spans(
            text, types=policy.protected_span_types or None
        )

    rewriter_chain: List[Any] = []
    applied_names: List[str] = []
    for recipe in recipes:
        ops = _operations_safe_for_route(recipe, policy.lossless_required)
        if not ops:
            continue
        for op_name in ops:
            fn = _SAFE_REWRITERS.get(op_name)
            if fn:
                rewriter_chain.append(fn)
        applied_names.append(getattr(recipe, "name", "<unknown>"))

    if not rewriter_chain:
        return CompressionResult(
            text=text,
            bytes_in=bytes_in,
            bytes_out=bytes_in,
            bytes_saved=0,
            skipped_reason="no-safe-operations",
            spans_preserved=len(spans),
        )

    def _composed(seg: str) -> str:
        for fn in rewriter_chain:
            seg = fn(seg)
        return seg

    new_text = rewrite_outside_spans(text, spans, _composed)
    bytes_out = len(new_text.encode("utf-8"))
    bytes_saved = max(0, bytes_in - bytes_out)
    return CompressionResult(
        text=new_text,
        bytes_in=bytes_in,
        bytes_out=bytes_out,
        bytes_saved=bytes_saved,
        recipes_applied=tuple(applied_names),
        spans_preserved=len(spans),
    )


__all__ = [
    "RouteClass",
    "ALL_ROUTE_CLASSES",
    "FidelityTier",
    "ALL_FIDELITY_TIERS",
    "RoutePolicy",
    "DEFAULT_POLICIES",
    "get_route_policy",
    "select_recipes",
    "apply_policy",
    "CompressionResult",
]
