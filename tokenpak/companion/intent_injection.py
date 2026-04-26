# SPDX-License-Identifier: Apache-2.0
"""Phase PI-3 — companion-side opt-in PromptPatch injection.

This is the **first phase where TokenPak may apply** PromptPatch
guidance — but only through the Claude Code companion path, only
when the operator explicitly opts in via
``intent_policy.prompt_intervention``, only with
``target = "companion_context"``, only with
``mode = "inject_guidance"``, and only on patch rows the PI-1
builder already emitted with ``applied = False``.

What PI-3 explicitly does NOT do
--------------------------------

  - **No proxy-level injection.** ``surfaces.proxy`` is
    force-clamped to ``False`` in the loader; the proxy path
    remains byte-preserved-passthrough.
  - **No user_message rewriting.** ``target = "user_message"``
    is rejected by the loader.
  - **No rewrite_prompt mode.** Rejected by the loader.
  - **No routing changes.** The application library never
    touches provider / model / adapter selection.
  - **No classifier changes.** Same intent-classification path
    that PI-1 / PI-2 use.
  - **No TIP wire-header emission.** The ``tip.intent.contract-headers-v1``
    capability is unchanged; no header lands on the wire.
  - **No byte-preserve override.** Force-clamped.
  - **No confirmation-mode execution.** When the host config
    sets ``require_confirmation = True``, the library refuses to
    auto-apply (PI-3 ships no approval gesture). The companion
    must call this library only after its own confirmation
    handshake — and even then, the library still validates that
    the operator opted out of ``require_confirmation`` for the
    auto-apply branch.

Idempotency + audit
-------------------

The library calls
:meth:`tokenpak.proxy.intent_prompt_patch_telemetry.IntentPatchStore.mark_applied`
which is **idempotent**: it only flips ``applied = 0`` rows to
``applied = 1`` and stamps the four audit columns. Calling
``apply_patch_to_companion_context`` twice on the same patch row
yields one application; the second call returns
``ApplicationResult(success=False, reason="already_applied")``.

Privacy contract
----------------

The library reads only the structured fields stored in
``intent_patches`` — no raw prompt text, no secrets. The patch
text is itself a fixed-template string the PI-1 builder emitted
under the wording + privacy guardrails. The library re-runs the
privacy guardrail before injecting.
"""

from __future__ import annotations

import datetime as _dt
import re
import secrets
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from tokenpak.proxy.intent_policy_config_loader import (
    PromptInterventionRuntimeConfig,
)
from tokenpak.proxy.intent_prompt_patch import (
    FORBIDDEN_PHRASES as _PATCH_FORBIDDEN_PHRASES,
)
from tokenpak.proxy.intent_prompt_patch import (
    SOURCE_PI as _PATCH_SOURCE,
)
from tokenpak.proxy.intent_prompt_patch_telemetry import IntentPatchStore

# ---------------------------------------------------------------------------
# Constants — surface labels + reasons
# ---------------------------------------------------------------------------


SURFACE_CLAUDE_CODE_COMPANION: str = "claude_code_companion"
APPLICATION_MODE_INJECT_GUIDANCE: str = "inject_guidance"

# PI-3 reasons surfaced via :class:`ApplicationResult.reason`.
REASON_OK: str = "ok"
REASON_DISABLED: str = "disabled"
REASON_CLAUDE_CODE_COMPANION_DISABLED: str = "claude_code_companion_disabled"
REASON_PROXY_FORCED_OFF: str = "proxy_surface_forced_off"
REASON_REQUIRES_CONFIRMATION: str = "requires_confirmation"
REASON_PATCH_MISSING: str = "patch_missing"
REASON_ALREADY_APPLIED: str = "already_applied"
REASON_WRONG_MODE: str = "wrong_mode"
REASON_WRONG_TARGET: str = "wrong_target"
REASON_WRONG_SOURCE: str = "wrong_source"
REASON_BYTE_PRESERVE_OVERRIDE_BLOCKED: str = "byte_preserve_override_blocked"
REASON_PRIVACY_GUARDRAIL: str = "privacy_guardrail_blocked"
REASON_WORDING_GUARDRAIL: str = "wording_guardrail_blocked"
REASON_PERSIST_FAILED: str = "persist_failed"


# Same forbidden-phrase regex the PI-1 builder uses. We re-run it
# here so a patch row tampered with after the builder still gets
# blocked at injection time.
_FORBIDDEN_RE = re.compile(
    r"\b(?:" + "|".join(re.escape(p) for p in _PATCH_FORBIDDEN_PHRASES) + r")\b",
    re.IGNORECASE,
)

# Match the scaffold-side credential patterns. We import inside
# the function so a missing scaffold module isn't a hard import
# error here.

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApplicationResult:
    """Outcome of one
    :func:`apply_patch_to_companion_context` call."""

    success: bool
    reason: str
    patch_id: Optional[str] = None
    injected_context: Optional[str] = None
    applied_at: Optional[str] = None
    applied_surface: Optional[str] = None
    application_mode: Optional[str] = None
    application_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _new_application_id() -> str:
    """Opaque caller-side application token. 16 hex chars."""
    return secrets.token_hex(8)


def _check_privacy_guardrail(text: str) -> bool:
    """Re-run the scaffold credential-pattern regex set against
    ``text``. Returns ``True`` when no pattern matches.
    """
    try:
        from tokenpak.scaffold import _guardrails as _sg
    except ImportError:
        return True
    for pattern in getattr(_sg, "_CRED_PATTERNS", ()):
        if pattern.search(text):
            return False
    return True


def _check_wording_guardrail(text: str) -> bool:
    """Return ``True`` when ``text`` contains no forbidden phrase.

    PI-3 still blocks ``Applied`` / ``Inserted`` / ``Injected`` in
    the patch_text *itself* — those words become permitted only in
    operator-facing surface text *after* an application succeeds
    (see CLI render in :mod:`tokenpak.cli._impl`). The patch row's
    ``patch_text`` continues to carry the PI-1 fixed template,
    which is forbidden-phrase clean by construction.
    """
    return _FORBIDDEN_RE.search(text) is None


def _build_block(patch_text: str) -> str:
    """Return the ``<TokenPak Intent Guidance>`` block to inject.

    The PI-1 builder already emits ``patch_text`` as a complete
    ``<TokenPak Intent Guidance>...</TokenPak Intent Guidance>``
    block, so the library returns it as-is. We do not wrap or
    re-template — that would risk wording-guardrail drift.
    """
    return patch_text.strip()


def _splice_into_context(existing_context: str, block: str) -> str:
    """Prepend the guidance block to ``existing_context``.

    Order matters: PI-3 places the guidance *first* so the model
    reads operator-approved guidance before any prior companion
    state. The original context is preserved byte-for-byte.
    """
    if not existing_context:
        return block
    sep = "\n\n" if not existing_context.startswith("\n") else "\n"
    return block + sep + existing_context


# ---------------------------------------------------------------------------
# Eligibility
# ---------------------------------------------------------------------------


def _eligibility_check(
    *,
    patch_dict: Optional[Mapping[str, Any]],
    pi_config: PromptInterventionRuntimeConfig,
) -> Optional[str]:
    """Return a reason string when the patch is **not** eligible,
    otherwise ``None``."""
    if not pi_config.enabled:
        return REASON_DISABLED
    if pi_config.allow_byte_preserve_override:
        # Defense-in-depth — the loader force-clamps this to False,
        # but if a caller hand-builds a config we still refuse.
        return REASON_BYTE_PRESERVE_OVERRIDE_BLOCKED
    if pi_config.surfaces.proxy:
        # Same defense-in-depth as above.
        return REASON_PROXY_FORCED_OFF
    if not pi_config.surfaces.claude_code_companion:
        return REASON_CLAUDE_CODE_COMPANION_DISABLED
    if pi_config.mode != "inject_guidance":
        return REASON_WRONG_MODE
    if pi_config.target != "companion_context":
        return REASON_WRONG_TARGET
    if pi_config.require_confirmation:
        # PI-3 ships no approval gesture; refuse auto-apply.
        return REASON_REQUIRES_CONFIRMATION
    if patch_dict is None:
        return REASON_PATCH_MISSING
    if patch_dict.get("applied"):
        return REASON_ALREADY_APPLIED
    if patch_dict.get("mode") != "inject_guidance":
        return REASON_WRONG_MODE
    if patch_dict.get("target") != "companion_context":
        return REASON_WRONG_TARGET
    if patch_dict.get("source") != _PATCH_SOURCE:
        return REASON_WRONG_SOURCE
    text = patch_dict.get("patch_text") or ""
    if not _check_wording_guardrail(text):
        return REASON_WORDING_GUARDRAIL
    if not _check_privacy_guardrail(text):
        return REASON_PRIVACY_GUARDRAIL
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def apply_patch_to_companion_context(
    *,
    patch_dict: Optional[Mapping[str, Any]],
    pi_config: PromptInterventionRuntimeConfig,
    existing_context: str,
    store: Optional[IntentPatchStore] = None,
    application_id: Optional[str] = None,
    now_utc_iso: Optional[str] = None,
) -> ApplicationResult:
    """Inject a patch's guidance block into the Claude Code companion context.

    Args:
        patch_dict: A row from
            :meth:`IntentPatchStore.fetch_latest` /
            :meth:`fetch_for_suggestion` (already-decoded dict —
            ``safety_flags`` as list, booleans as bool). May be
            ``None`` to surface the empty-row failure mode.
        pi_config: The active host's prompt-intervention config —
            from :func:`load_prompt_intervention_config_safely`.
        existing_context: The companion's current context string,
            built by the rest of the companion (capsules / journal /
            etc). Preserved byte-for-byte; the guidance block is
            prepended.
        store: The patch store the audit row gets persisted to.
            Defaults to the process-wide default store.
        application_id: Opaque caller-side token. Generated when
            ``None``.
        now_utc_iso: Override for :func:`_now_iso` (test hook).

    Returns:
        :class:`ApplicationResult` with ``success = True`` only
        when every gate aligned, the patch_text passed both
        guardrails, and the row was persisted with
        ``applied = 1``.

    The library never raises on the caller path; failures are
    returned as ``ApplicationResult`` with the relevant
    ``reason``.
    """
    reason = _eligibility_check(patch_dict=patch_dict, pi_config=pi_config)
    if reason is not None:
        patch_id = (
            patch_dict.get("patch_id") if isinstance(patch_dict, Mapping) else None
        )
        return ApplicationResult(success=False, reason=reason, patch_id=patch_id)

    assert patch_dict is not None  # narrowed by _eligibility_check
    patch_id = patch_dict.get("patch_id")
    text = patch_dict.get("patch_text") or ""

    # Compose the new context. Pure-string — no I/O yet.
    block = _build_block(text)
    new_context = _splice_into_context(existing_context, block)

    # Persist the audit columns. Only mark applied if the DB
    # update succeeded; otherwise we surface persist_failed and
    # the caller MUST NOT use new_context (the patch isn't
    # auditable, so the operator-safety contract isn't met).
    target_store = store if store is not None else _default_store()
    app_id = application_id or _new_application_id()
    applied_at = now_utc_iso or _now_iso()
    ok = target_store.mark_applied(
        patch_id=patch_id,
        applied_surface=SURFACE_CLAUDE_CODE_COMPANION,
        application_mode=APPLICATION_MODE_INJECT_GUIDANCE,
        application_id=app_id,
        applied_at=applied_at,
    )
    if not ok:
        return ApplicationResult(
            success=False,
            reason=REASON_PERSIST_FAILED,
            patch_id=patch_id,
        )

    return ApplicationResult(
        success=True,
        reason=REASON_OK,
        patch_id=patch_id,
        injected_context=new_context,
        applied_at=applied_at,
        applied_surface=SURFACE_CLAUDE_CODE_COMPANION,
        application_mode=APPLICATION_MODE_INJECT_GUIDANCE,
        application_id=app_id,
    )


def _default_store() -> IntentPatchStore:
    """Lazy default store. Imported here so test hooks
    (:func:`set_default_patch_store`) take effect.
    """
    from tokenpak.proxy.intent_prompt_patch_telemetry import (
        get_default_patch_store,
    )

    return get_default_patch_store()


__all__ = [
    "APPLICATION_MODE_INJECT_GUIDANCE",
    "ApplicationResult",
    "REASON_ALREADY_APPLIED",
    "REASON_BYTE_PRESERVE_OVERRIDE_BLOCKED",
    "REASON_CLAUDE_CODE_COMPANION_DISABLED",
    "REASON_DISABLED",
    "REASON_OK",
    "REASON_PATCH_MISSING",
    "REASON_PERSIST_FAILED",
    "REASON_PRIVACY_GUARDRAIL",
    "REASON_PROXY_FORCED_OFF",
    "REASON_REQUIRES_CONFIRMATION",
    "REASON_WORDING_GUARDRAIL",
    "REASON_WRONG_MODE",
    "REASON_WRONG_SOURCE",
    "REASON_WRONG_TARGET",
    "SURFACE_CLAUDE_CODE_COMPANION",
    "apply_patch_to_companion_context",
]
