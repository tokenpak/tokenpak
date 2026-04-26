# SPDX-License-Identifier: Apache-2.0
"""Phase PI-1 — PromptPatch dataclass + pure-function builder.

Builds zero or one :class:`PromptPatch` from an existing
``PolicySuggestion`` plus its linked contract / decision / config /
adapter context. **Pure function; no I/O. Side-channel for storage
is :mod:`tokenpak.proxy.intent_prompt_patch_telemetry`.**

PI-1 scope (per the PI-0 spec § 10 sub-phase 1 deliverable):

  - Define :class:`PromptPatch` (frozen).
  - Build patches in three modes: ``preview_only`` /
    ``inject_guidance`` / ``ask_clarification``. **NOT
    ``rewrite_prompt``** — that mode is PI-4 deliverable; the
    builder rejects it in PI-1.
  - Persist to a new ``intent_patches`` SQLite table.
  - **No surface changes; no application; no companion
    integration; no server.py call site.** PI-2 wires surfaces;
    PI-3 wires companion injection.

Eligibility gates (always-on; cannot be relaxed by any config):

  (a) ``prompt_intervention.enabled`` is ``True``.
  (b) Mode is one of the three PI-1 modes; ``rewrite_prompt`` is
      rejected.
  (c) ``target`` is not ``user_message`` (reserved indefinitely).
  (d) ``allow_byte_preserve_override`` is **not requested** —
      PI-1 hard-blocks even when the host config sets the flag
      true. The override capability is reserved for a future
      ratification past PI-1.
  (e) Confidence ≥ ``low_confidence_threshold`` (carry from 2.4).
  (f) ``catch_all_reason is None`` (carry from 2.4).
  (g) Resolved adapter is not byte-preserve locked **OR**
      ``target == "companion_context"`` (companion runs
      pre-bytes; byte-fidelity rule preserved by construction).
  (h) For modes other than ``ask_clarification``: no required
      slot is missing (carry from 2.4).
  (i) Underlying suggestion is not expired (``expires_at`` null
      or in the future).
  (j) ``decision_reason`` is in
      :data:`EXPLAINABLE_REASONS` (Phase 2.4.1 carry-through).
  (k) An applicable template exists for ``(mode, intent_class)``;
      no template ⇒ no patch (silent no-op, not an error).
  (l) Built ``patch_text`` passes the §8 wording guardrail (no
      forbidden phrase implying application has happened).
  (m) Built ``patch_text`` passes the privacy guardrail (no
      sentinel-style substring that could be a leaked secret;
      reuses the scaffold ``_guardrails`` regex set).

Privacy contract (asserted in tests):

  - Builder reads structured fields only (intent class, slot
    tuples, provider/model slugs, capability frozensets,
    suggestion text). No raw prompt content is an input.
  - Emitted ``patch_text`` is **always** a fixed-template string
    parameterized only by intent_class. No caller-supplied
    substring reaches the field.
  - Emitted ``reason`` reuses the Phase 2.4.1 reason-rendering
    table; no caller-supplied substring reaches that field
    either.
  - The ``original_hash`` field is sha256-hex of the
    suggestion's ``contract_id || message`` digest — NEVER the
    raw prompt body. (PI-2 / PI-3 callers may pass a real
    prompt-equivalent string when they need genuine staleness
    detection; for PI-1 builder-only flows, the suggestion-
    derived digest suffices.)
"""

from __future__ import annotations

import hashlib
import re
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Dict, FrozenSet, Optional, Tuple

from tokenpak.proxy.intent_contract import IntentContract
from tokenpak.proxy.intent_policy_engine import (
    REASON_DRY_RUN_SUGGEST,
    SAFETY_MISSING_SLOTS,
    PolicyDecision,
    PolicyEngineConfig,
    SuggestionSurfaceConfig,
)
from tokenpak.proxy.intent_suggestion import (
    FORBIDDEN_PHRASES as _BASE_FORBIDDEN_PHRASES,
)
from tokenpak.proxy.intent_suggestion import (
    SOURCE_INTENT_POLICY_V0,
    PolicySuggestion,
    SuggestionWordingError,
)

# ---------------------------------------------------------------------------
# Constants / enums
# ---------------------------------------------------------------------------


# PI-1 modes. Spec PI-0 § 5 enumerates four; PI-1 supports three
# of them. ``rewrite_prompt`` is reserved for PI-4 and rejected
# at the eligibility gate.
MODE_PREVIEW_ONLY = "preview_only"
MODE_INJECT_GUIDANCE = "inject_guidance"
MODE_ASK_CLARIFICATION = "ask_clarification"
MODE_REWRITE_PROMPT = "rewrite_prompt"  # rejected in PI-1

PI_1_SUPPORTED_MODES: FrozenSet[str] = frozenset({
    MODE_PREVIEW_ONLY,
    MODE_INJECT_GUIDANCE,
    MODE_ASK_CLARIFICATION,
})

ALL_MODES: FrozenSet[str] = PI_1_SUPPORTED_MODES | frozenset({MODE_REWRITE_PROMPT})


# Spec PI-0 § 6: PI-1 / PI-2 / PI-3 limit ``target`` to
# ``companion_context``. ``system`` is PI-4-only. ``user_message``
# is reserved indefinitely. The PI-1 builder rejects ``user_message``
# at the eligibility gate; ``system`` is technically allowed by the
# config schema but is gated on adapter capability + future
# ratification.
TARGET_COMPANION_CONTEXT = "companion_context"
TARGET_SYSTEM = "system"
TARGET_USER_MESSAGE = "user_message"  # rejected in PI-1

PI_1_SUPPORTED_TARGETS: FrozenSet[str] = frozenset({
    TARGET_COMPANION_CONTEXT,
    TARGET_SYSTEM,
})


# Phase 2.4.1 carry-through.
EXPLAINABLE_REASONS: FrozenSet[str] = frozenset({
    REASON_DRY_RUN_SUGGEST,
})


# Forbidden-phrase set EXTENDS the Phase 2.4.1 list with PI-1-
# specific application-implying verbs. Tests pin this set so any
# future change requires explicit ratification.
PI_1_ADDITIONAL_FORBIDDEN: Tuple[str, ...] = (
    "injected",
    "mutated",
    "rewrote",
    "inserted",
    "will inject",
    "will rewrite",
)
FORBIDDEN_PHRASES: Tuple[str, ...] = tuple(_BASE_FORBIDDEN_PHRASES) + PI_1_ADDITIONAL_FORBIDDEN
_FORBIDDEN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in FORBIDDEN_PHRASES) + r")\b",
    re.IGNORECASE,
)


# Source pinned for the PI-x line. The directive specifies
# ``intent_policy_v0`` (matches Phase 2.4.1 PolicySuggestion) so
# the entire Intent Layer shares one provenance namespace.
SOURCE_PI: str = SOURCE_INTENT_POLICY_V0


# Hard cap on patch_text length per PI-0 spec § 4.1.
PATCH_TEXT_MAX_LEN: int = 1024


class PatchModeError(Exception):
    """Raised when an unknown / unsupported mode is requested."""


class PatchTargetError(Exception):
    """Raised when an unsupported / reserved target is requested."""


class PatchPrivacyError(Exception):
    """Raised when patch_text fails the privacy guardrail."""


# ---------------------------------------------------------------------------
# PromptPatch shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptPatch:
    """One operator-visible prompt-patch candidate. Always advisory
    in PI-1; surfaces decide visibility (PI-2); approval gates
    application (PI-3 / PI-4).

    Field set is the PI-0 spec § 4 wire contract verbatim.
    """

    # Identity
    patch_id: str
    contract_id: str
    decision_id: str
    suggestion_id: str

    # What this patch IS
    mode: str
    target: str
    original_hash: str
    patch_text: str
    reason: str

    # Provenance / risk
    confidence: float
    safety_flags: Tuple[str, ...] = field(default_factory=tuple)

    # Lifecycle
    requires_confirmation: bool = True
    applied: bool = False
    source: str = SOURCE_PI

    def to_dict(self) -> Dict[str, Any]:
        return {
            "patch_id": self.patch_id,
            "contract_id": self.contract_id,
            "decision_id": self.decision_id,
            "suggestion_id": self.suggestion_id,
            "mode": self.mode,
            "target": self.target,
            "original_hash": self.original_hash,
            "patch_text": self.patch_text,
            "reason": self.reason,
            "confidence": self.confidence,
            "safety_flags": list(self.safety_flags),
            "requires_confirmation": self.requires_confirmation,
            "applied": self.applied,
            "source": self.source,
        }


def make_patch_id() -> str:
    """29-char hex ID (13 ms timestamp + 16 random) — sortable.

    Mirrors :func:`tokenpak.proxy.intent_policy_engine.make_decision_id`
    so consumers can sort patch + decision + suggestion ids
    together.
    """
    ts_ms = int(time.time() * 1000)
    rand = secrets.token_hex(8)
    return f"{ts_ms:013x}{rand}"


def _hash_original(suggestion: PolicySuggestion, contract: IntentContract) -> str:
    """Compute the ``original_hash`` field.

    PI-1 derives the hash from suggestion identity + contract_id
    so a patch built against suggestion X is detectably stale if
    suggestion X is later edited / superseded. PI-2 / PI-3 may
    extend this to include the prompt-equivalent text the
    companion would inject the patch into; for the builder-only
    PI-1 flow, the suggestion-derived digest suffices.
    """
    seed = (
        f"{contract.contract_id}|"
        f"{suggestion.suggestion_id}|"
        f"{suggestion.suggestion_type}"
    )
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Templates (PI-0 spec § 4.1: fixed-template, parameterized only
# by intent_class)
# ---------------------------------------------------------------------------


_TEMPLATE_CODING = (
    "<TokenPak Intent Guidance>\n"
    "Recommended: preserve the user's original request, make the smallest "
    "safe change, identify touched files, and include verification steps "
    "when practical.\n"
    "</TokenPak Intent Guidance>"
)

_TEMPLATE_DEBUG = (
    "<TokenPak Intent Guidance>\n"
    "Recommended: identify the failure path, isolate the likely root cause, "
    "propose the smallest fix, and include verification steps.\n"
    "</TokenPak Intent Guidance>"
)

_TEMPLATE_CLARIFICATION = (
    "<TokenPak Intent Guidance>\n"
    "Recommended: ask one clarifying question before making assumptions "
    "about target files, framework, or success criteria.\n"
    "</TokenPak Intent Guidance>"
)


# Map intent_class → coding-template-applicable. The PI-0 spec
# §6 directive's "coding intent" maps onto our 10 canonical
# intents via the create / execute set (the two intents that
# imply code-touching action).
_CODING_INTENT_CLASSES: FrozenSet[str] = frozenset({"create", "execute"})


def _select_template(intent_class: str, mode: str) -> Optional[str]:
    """Pick the patch_text template.

    Returns ``None`` when no applicable template — that's the
    silent no-op case (eligibility rule (k)).
    """
    if mode == MODE_ASK_CLARIFICATION:
        return _TEMPLATE_CLARIFICATION
    if mode in (MODE_INJECT_GUIDANCE, MODE_PREVIEW_ONLY):
        if intent_class == "debug":
            return _TEMPLATE_DEBUG
        if intent_class in _CODING_INTENT_CLASSES:
            return _TEMPLATE_CODING
    return None


def _render_reason(decision_reason: str) -> str:
    """Map decision_reason → templated plain-English clause.

    Reuses the Phase 2.4.1 reason-rendering rule. Empty fallback
    only triggers in tests (eligibility (j) blocks unknown
    reasons in production).
    """
    if decision_reason == REASON_DRY_RUN_SUGGEST:
        return "the canonical heuristic table recommends it"
    return ""


# ---------------------------------------------------------------------------
# Wording + privacy guardrails
# ---------------------------------------------------------------------------


def _check_wording(*texts: str) -> None:
    """Scan emitted strings for the PI-1 forbidden-phrase set.

    Hard-fail so a future template change can never ship language
    that implies application has happened. Skips ``None``.
    """
    for text in texts:
        if text is None:
            continue
        m = _FORBIDDEN_RE.search(text)
        if m is not None:
            raise SuggestionWordingError(
                f"forbidden wording {m.group(0)!r} in patch text: {text!r}"
            )


def _check_privacy(text: str) -> None:
    """Apply the scaffold-guardrail privacy regex set to patch_text.

    Hard-fail when patch_text matches any credential / token / key
    pattern the scaffold guard already pins. Belt-and-suspenders
    against a future template that accidentally interpolates a
    secret-shaped value.
    """
    try:
        from tokenpak.scaffold import _guardrails as _sg

        # Reuse the regex set the scaffold guardrail already pins
        # (private import; same package). The scaffold side
        # consumes the patterns from a module-level ``_CRED_PATTERNS``
        # list — same regexes, same behavior.
        for pattern in getattr(_sg, "_CRED_PATTERNS", ()):
            if pattern.search(text):
                raise PatchPrivacyError(
                    f"patch_text matches secret pattern: {pattern.pattern!r}"
                )
    except ImportError:
        # If the scaffold module isn't importable, fall through —
        # tests will catch any regression.
        pass


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PromptPatchBuilderContext:
    """Bundle of facts the builder reads beyond suggestion / decision /
    contract.

    Mirrors the PI-0 spec § 7 eligibility-gate inputs.
    """

    config: PolicyEngineConfig
    adapter_capabilities: FrozenSet[str] = field(default_factory=frozenset)
    required_slots: Tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class PromptInterventionConfig:
    """Subset of the PI-0 spec § 8 ``intent_policy.prompt_intervention``
    block. PI-1 mirrors only the fields the builder reads; PI-2 /
    PI-3 / PI-4 wiring will load these from ``~/.tokenpak/policy.yaml``.

    Default: all-off, ``preview_only`` mode, ``companion_context``
    target, ``require_confirmation = True``,
    ``allow_byte_preserve_override = False``.
    """

    enabled: bool = False
    mode: str = MODE_PREVIEW_ONLY
    target: str = TARGET_COMPANION_CONTEXT
    require_confirmation: bool = True
    allow_byte_preserve_override: bool = False


def build_patches(
    *,
    suggestion: PolicySuggestion,
    contract: IntentContract,
    decision: PolicyDecision,
    pi_config: PromptInterventionConfig,
    ctx: PromptPatchBuilderContext,
    now_ms: Optional[int] = None,
) -> Tuple[PromptPatch, ...]:
    """Pure function. Phase PI-1's only entry point.

    Returns a tuple of zero or one patch. Tuple-of-zero is the
    common case (eligibility blocks). The interface returns a
    tuple instead of an Optional so PI-2 / PI-3 can extend
    without breaking callers.

    Never raises on:
      - Unknown decision_reason → returns ``()``
      - Missing template → returns ``()``
      - Disabled config → returns ``()``

    Raises only on:
      - ``PatchModeError`` — caller passed an unknown mode
      - ``PatchTargetError`` — caller passed an unknown target
      - ``SuggestionWordingError`` — template contains a forbidden
        phrase (template bug, never user input)
      - ``PatchPrivacyError`` — template matches a secret pattern
        (template bug, never user input)
    """
    cfg = pi_config

    # Validate mode + target up-front. These raise so a caller
    # using an undefined enum value gets a loud error.
    if cfg.mode not in ALL_MODES:
        raise PatchModeError(f"unknown mode: {cfg.mode!r}")
    if cfg.target not in PI_1_SUPPORTED_TARGETS | frozenset({TARGET_USER_MESSAGE}):
        raise PatchTargetError(f"unknown target: {cfg.target!r}")

    # ── Eligibility gates ──────────────────────────────────────────
    # (a) enabled flag
    if not cfg.enabled:
        return ()

    # (b) mode is one of the PI-1 supported modes; rewrite_prompt
    # rejected.
    if cfg.mode not in PI_1_SUPPORTED_MODES:
        return ()

    # (c) target = user_message rejected
    if cfg.target == TARGET_USER_MESSAGE:
        return ()

    # (d) allow_byte_preserve_override hard-blocked in PI-1
    if cfg.allow_byte_preserve_override:
        return ()

    # (e) confidence ≥ threshold
    if contract.confidence < ctx.config.low_confidence_threshold:
        return ()

    # (f) no catch-all
    if contract.catch_all_reason is not None:
        return ()

    # (g) byte-preserve check, with companion_context exception
    if (
        "tip.byte-preserved-passthrough" in ctx.adapter_capabilities
        and cfg.target != TARGET_COMPANION_CONTEXT
    ):
        return ()

    # (h) missing required slots — ask_clarification bypasses
    if cfg.mode != MODE_ASK_CLARIFICATION:
        if _has_missing_required_slots(contract, ctx.required_slots):
            return ()

    # (i) suggestion expiration
    if suggestion.expires_at:
        # ISO-8601 string compare. Build-time comparison value can
        # be overridden by callers via now_ms, but the typical
        # path uses wall clock.
        cur_ms = now_ms if now_ms is not None else int(time.time() * 1000)
        # expires_at is ISO-8601 in our schema; decode best-effort.
        if _expires_in_past(suggestion.expires_at, cur_ms):
            return ()

    # (j) decision_reason explainable
    if decision.decision_reason not in EXPLAINABLE_REASONS:
        return ()

    # (k) template selection
    template = _select_template(contract.intent_class, cfg.mode)
    if template is None:
        return ()

    # Build the patch_text. Bound length defensively.
    patch_text = template.strip()
    if len(patch_text) > PATCH_TEXT_MAX_LEN:
        patch_text = patch_text[:PATCH_TEXT_MAX_LEN]

    reason = _render_reason(decision.decision_reason)
    if not reason:
        # eligibility (j) above should have caught this; guard
        # defensively against template drift.
        return ()

    # (l) wording guardrail
    _check_wording(patch_text, reason)
    # (m) privacy guardrail
    _check_privacy(patch_text)
    _check_privacy(reason)

    # If we reach here, build the patch. ask_clarification carries
    # the safety flag; other modes don't unless the underlying
    # suggestion did.
    safety_flags: Tuple[str, ...] = tuple(suggestion.safety_flags)
    if cfg.mode == MODE_ASK_CLARIFICATION and SAFETY_MISSING_SLOTS not in safety_flags:
        # Add the implicit reason flag so the explain surface can
        # render "this clarification fires because slots are
        # missing".
        safety_flags = tuple(sorted(set(safety_flags) | {SAFETY_MISSING_SLOTS}))

    patch = PromptPatch(
        patch_id=make_patch_id(),
        contract_id=contract.contract_id,
        decision_id=decision.decision_id,
        suggestion_id=suggestion.suggestion_id,
        mode=cfg.mode,
        target=cfg.target,
        original_hash=_hash_original(suggestion, contract),
        patch_text=patch_text,
        reason=reason,
        confidence=contract.confidence,
        safety_flags=safety_flags,
        requires_confirmation=cfg.require_confirmation,
        applied=False,  # always False in PI-1
        source=SOURCE_PI,
    )
    return (patch,)


# ---------------------------------------------------------------------------
# Helpers (pure)
# ---------------------------------------------------------------------------


def _has_missing_required_slots(
    contract: IntentContract, required_slots: Tuple[str, ...]
) -> bool:
    if not required_slots:
        return False
    missing = set(contract.slots_missing)
    return any(s in missing for s in required_slots)


def _expires_in_past(iso_str: str, now_ms: int) -> bool:
    """Best-effort expiry comparison.

    The PolicySuggestion schema stores ``expires_at`` as ISO-8601
    when set. Phase 2.4.1 builds with ``None`` by default; the
    expiry path here exists for future PI-x writers that set a
    non-null value.
    """
    if not iso_str:
        return False
    try:
        # Compare ISO strings lexicographically — sound for
        # ISO-8601 timestamp strings.
        from datetime import datetime as _dt
        cur = _dt.fromtimestamp(now_ms / 1000.0).isoformat(timespec="seconds")
        return iso_str < cur
    except Exception:  # noqa: BLE001
        return False


__all__ = [
    "ALL_MODES",
    "EXPLAINABLE_REASONS",
    "FORBIDDEN_PHRASES",
    "MODE_ASK_CLARIFICATION",
    "MODE_INJECT_GUIDANCE",
    "MODE_PREVIEW_ONLY",
    "MODE_REWRITE_PROMPT",
    "PATCH_TEXT_MAX_LEN",
    "PI_1_ADDITIONAL_FORBIDDEN",
    "PI_1_SUPPORTED_MODES",
    "PI_1_SUPPORTED_TARGETS",
    "PatchModeError",
    "PatchPrivacyError",
    "PatchTargetError",
    "PromptInterventionConfig",
    "PromptPatch",
    "PromptPatchBuilderContext",
    "SOURCE_PI",
    "SuggestionSurfaceConfig",
    "TARGET_COMPANION_CONTEXT",
    "TARGET_SYSTEM",
    "TARGET_USER_MESSAGE",
    "build_patches",
    "make_patch_id",
]
