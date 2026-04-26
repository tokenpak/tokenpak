# Claude Code Companion — Opt-in Intent Guidance Injection (PI-3)

> Phase PI-3 is the **first phase** in which TokenPak may apply a `PromptPatch`. Application is **companion-side only**, **explicitly opt-in**, and limited to `target = companion_context`. The proxy path remains byte-preserved-passthrough; user messages are never rewritten; routing, classification, and provider/model selection are untouched. Default posture: **disabled**.

## How to enable Claude Code companion guidance injection

Add (or edit) `~/.tokenpak/policy.yaml` (or `$TOKENPAK_HOME/policy.yaml`):

```yaml
intent_policy:
  mode: suggest
  prompt_intervention:
    enabled: true
    mode: inject_guidance
    target: companion_context
    require_confirmation: false        # set true to keep PI-3 lib refusing auto-apply
    allow_byte_preserve_override: false  # forced false; can't be flipped
    surfaces:
      claude_code_companion: true
      proxy: false                     # forced false; can't be flipped
```

Reload by re-running any `tokenpak claude-code …` command. The CLI prints:

```
Prompt intervention is enabled for Claude Code companion context only.
User messages are preserved.
```

When `enabled: false` (the default) the CLI prints:

```
Prompt intervention is disabled. Guidance is preview-only.
```

## What gets injected

Only patches that satisfy **every** PI-1 eligibility gate AND every PI-3 application gate are eligible. The PI-1 builder writes `intent_patches` rows with `applied = false`, fixed-template `patch_text`, and forbidden-phrase-clean `reason`. PI-3 then validates at injection time:

- `pi_config.enabled` is `true`
- `pi_config.allow_byte_preserve_override` is `false` (force-clamped)
- `pi_config.surfaces.proxy` is `false` (force-clamped)
- `pi_config.surfaces.claude_code_companion` is `true`
- `pi_config.mode == "inject_guidance"`
- `pi_config.target == "companion_context"`
- `pi_config.require_confirmation` is `false` (PI-3 ships no approval gesture; with `true` the library refuses to auto-apply)
- `patch.applied` is `false`
- `patch.mode == "inject_guidance"`
- `patch.target == "companion_context"`
- `patch.source == "intent_policy_v0"`
- `patch.patch_text` re-passes the wording + privacy guardrails

When all gates align, the library prepends the existing `<TokenPak Intent Guidance>…</TokenPak Intent Guidance>` block to the companion's context string (byte-for-byte; the original context follows unchanged).

Example block:

```
<TokenPak Intent Guidance>
Recommended: preserve the user's original request, make the smallest
safe change, identify touched files, and include verification steps
when practical.
</TokenPak Intent Guidance>
```

## What never gets modified

PI-3 has zero authority over:

- **The user message** — `target = "user_message"` is rejected by the loader and refused by the runtime library.
- **The proxy path** — `surfaces.proxy` is force-clamped to `false`; the AnthropicAdapter byte-preserved-passthrough invariant is unchanged.
- **Provider / model / adapter selection** — the application library imports no routing primitives; structural tests pin this.
- **TIP wire headers** — `tip.intent.contract-headers-v1` capability is unchanged; no header lands on the wire.
- **Classifier behavior** — the same Phase 0 classifier path is used; no PI-3 code touches it.
- **`patch_text` template content** — the PI-1 builder is the single source of truth for what the block says.

## How to inspect applied patches

```bash
tokenpak claude-code patches              # human render
tokenpak claude-code patches --json       # JSON
```

When the latest patch was successfully applied, the surface shows:

- `applied: True`
- `applied_at: <UTC ISO-8601>`
- `applied_surface: claude_code_companion`
- `application_mode: inject_guidance`
- `application_id: <16-hex token>`

And the label set swaps from PI-2's preview labels to:

- `Claude Code Companion Intent Guidance`
- `Applied for this one request`
- `Injected into Claude Code companion context`
- `User messages preserved`
- `Proxy path remains byte-preserved-passthrough`
- `Telemetry-only; no TIP intent headers emitted`

For an unapplied patch (default behavior), the original PI-2 PREVIEW_LABELS still apply: `PREVIEW ONLY`, `NOT APPLIED`, `NO PROMPT MUTATION`, `NO CLAUDE CODE INJECTION YET`.

## How to disable it

Set `enabled: false` in the `prompt_intervention` block (or remove the block entirely):

```yaml
intent_policy:
  prompt_intervention:
    enabled: false
```

Removing the block is equivalent to the default (disabled). Already-applied rows are not retroactively reverted; the audit columns remain.

## Why proxy-level mutation remains off

The Anthropic adapter declares `tip.byte-preserved-passthrough` precisely because Claude Code's billing routing depends on byte fidelity (cache_control alignment, OAuth quota mode, prompt-prefix cache hit rates). Any proxy-side mutation would break that invariant for *every* downstream caller of the adapter — including non-Claude-Code clients. PI-3 keeps the proxy path off-limits by:

1. The loader force-clamps `surfaces.proxy` to `false`.
2. The runtime library has a defense-in-depth check that refuses to apply when `surfaces.proxy = true` (in case a caller hand-builds a config bypassing the loader).
3. The application library lives entirely under `tokenpak/companion/`, with structural tests pinning that it imports no proxy dispatch primitives.

The companion runs **pre-bytes**: the patch is composed into the companion's context string before that string ever becomes a request body, so the byte-preserved-passthrough invariant downstream is preserved by construction.

## Why byte-preserve override remains blocked

`allow_byte_preserve_override = true` is the single most dangerous knob in the prompt-intervention schema — it would let an operator bypass the byte-fidelity invariant on *any* adapter. PI-3 force-clamps it to `false` for the same reason PI-1 hard-blocks it: future ratification (with explicit Kevin sign-off + a separate phase) is required before the bit can be flipped, and even then only for non-byte-preserved adapters. The runtime library has a defense-in-depth refusal that returns `byte_preserve_override_blocked` even if a caller hand-builds a config with the flag set.

## How application is performed

The companion calls:

```python
from tokenpak.companion.intent_injection import (
    apply_patch_to_companion_context,
)
from tokenpak.proxy.intent_policy_config_loader import (
    load_prompt_intervention_config_safely,
)
from tokenpak.proxy.intent_prompt_patch_telemetry import (
    get_default_patch_store,
)

cfg = load_prompt_intervention_config_safely()
store = get_default_patch_store()
patch = store.fetch_latest()  # or fetch_for_suggestion(...)

result = apply_patch_to_companion_context(
    patch_dict=patch,
    pi_config=cfg,
    existing_context=companion_context,
    store=store,
)

if result.success:
    companion_context = result.injected_context
```

`result.success` is `True` only when every gate aligned, the patch passed both guardrails, and the audit row was persisted. On failure, `result.reason` is one of the documented enum values (`disabled`, `claude_code_companion_disabled`, `wrong_mode`, `wrong_target`, `wrong_source`, `byte_preserve_override_blocked`, `proxy_surface_forced_off`, `requires_confirmation`, `patch_missing`, `already_applied`, `wording_guardrail_blocked`, `privacy_guardrail_blocked`, `persist_failed`).

The library is **idempotent**: a second call on an already-applied row returns `already_applied` without persisting again. The audit columns (`applied_at`, `applied_surface`, `application_mode`, `application_id`) record the first successful application only.

## Privacy contract

Same as Phase 0 / 2.4 / PI-1 / PI-2:

- Only structured fields cross the application boundary (IDs, hashes, templated text, numeric / boolean fields).
- The patch_text template is fixed and parameterized only by `intent_class`; no caller-supplied substring reaches it.
- The PI-3 library re-runs the privacy guardrail (scaffold credential-pattern regex set) against `patch_text` before injecting.
- The library emits no `print()` and no `logger.info` / `logger.warning` lines that include patch content; the only side-effect is the SQLite update on the audit columns.

The sentinel-substring privacy contract is asserted in `tests/test_intent_prompt_intervention_phase_pi_3.py::TestPrivacyContract`.

## Forbidden wording — before vs after

PI-1 / PI-2 hard-block these phrases inside `patch_text` and `reason` (PI-1 builder enforces at build time):

- `applied`, `inserted`, `injected`, `mutated`, `rewrote`
- `will inject`, `will rewrite`
- `changed`, `routed to`, `switched to`, `now using`, `updated`, `will route`, `will switch`

PI-3 keeps the same builder-side ban: `patch_text` continues to be a fixed template that contains none of these words. What PI-3 adds is an **operator-facing surface** that *may* render `Applied`, `Inserted`, `Injected` — but only on the audit columns of a row that has actually been applied. Pre-application, the CLI never says these words about the row; post-application, the labels include `Applied for this one request` and `Injected into Claude Code companion context`.

The pre/post distinction is asserted in `tests/test_intent_prompt_intervention_phase_pi_3.py::TestForbiddenWordingBeforeApplication` and `::TestAppliedWordingAfterApplication`.

## Files

| Path | Purpose |
|---|---|
| `tokenpak/companion/intent_injection.py` | application library + `ApplicationResult` |
| `tokenpak/proxy/intent_policy_config_loader.py` | `prompt_intervention` block parser + `PromptInterventionRuntimeConfig` |
| `tokenpak/proxy/intent_prompt_patch_telemetry.py` | `intent_patches` schema + `IntentPatchStore.mark_applied` |
| `tokenpak/proxy/intent_claude_code_preview.py` | applied-state-aware label selection |
| `tokenpak/cli/_impl.py` | CLI render of audit columns + intervention status banner |
| `tests/test_intent_prompt_intervention_phase_pi_3.py` | 17-category test suite |
| `docs/reference/claude-code-intent-injection.md` | this document |

## Cross-references

- `docs/reference/claude-code-intent-preview.md` — PI-2 read-only preview (predecessor)
- `docs/internal/specs/intent-prompt-intervention-spec-2026-04-26.md` — PI-0 unified spec (§10 covers the rollout)
- `docs/reference/intent-layer-phase-0.md` — Phase 0 fundamentals

## What's next

PI-4 is the next sub-phase that may extend the intervention scope (target = `system`, mode = `rewrite_prompt`). It requires explicit Kevin approval before any code lands; until then, both are rejected by the loader.
