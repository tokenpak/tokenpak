# Milestone — Claude Code Intent Guidance Injection MVP

**Date**: 2026-04-26
**Status**: ✅ **complete** (closeout)
**Workstream**: TokenPak Intent Layer — Prompt Intervention sub-arc (PI-0 → PI-3)
**Owner**: Kevin (direction) / Sue (execution)
**Origin**: PI-0 unified spec (`docs/internal/specs/intent-prompt-intervention-spec-2026-04-26.md`), built on top of the Intent Advisory MVP (`docs/internal/milestones/intent-advisory-mvp-2026-04-26.md`)
**Foundation specs**:
  - `docs/internal/specs/intent-prompt-intervention-spec-2026-04-26.md` — PI-0 unified design (10 sections covering modes, targets, eligibility, surfaces, rollout)
  - `docs/internal/specs/phase2.5-confirm-mode-spec-2026-04-26.md` — adjacent confirmation-mode spec (referenced from §10 sub-phase plan)

This document closes out the **Claude Code Intent Guidance Injection MVP**. The milestone is reached when an operator can: (a) inspect a per-request `PromptPatch` preview through the Claude Code Companion surface; (b) opt in to companion-side guidance injection via `intent_policy.prompt_intervention` config; (c) read an audit trail of every applied patch (when, where, how); (d) trust that no proxy mutation, no `user_message` rewriting, no routing/classifier change occurred. All four are now true.

This is a **closeout document**, not an implementation gate. The next item on the roadmap is PI-4 (`target = system` + `mode = rewrite_prompt`); it requires explicit Kevin approval before any code lands.

---

## 1. What shipped

Four PRs across the workstream, all merged onto `main`:

| PR | Title | Merge SHA | Layer |
|---|---|---|---|
| #57 | `docs(specs): Intent Prompt Intervention — design / spec only (PI-0)` | `8bc31d8c` | PI-0 — unified design spec (10 sections; modes / targets / eligibility / surfaces / rollout sub-phase plan) |
| #58 | `feat(proxy,cli): PI-1 — PromptPatch builder + intent_patches table` | `31dfb40b` | PI-1 — `PromptPatch` dataclass + pure builder + `intent_patches` SQLite store + 13 eligibility gates + forbidden-wording + privacy guardrails |
| #59 | `feat(proxy,cli,docs): PI-2 — Claude Code Companion intent preview surface` | `5ab86f64` | PI-2 — read-only join over the four Intent Layer tables; `tokenpak claude-code intent / patches`; identity-separation labels (no fake `ClaudeCodeAdapter`) |
| #60 | `feat(companion,proxy,cli,docs): PI-3 — Claude Code companion-side opt-in PromptPatch injection` | `fbb82cae` | PI-3 — opt-in companion-side injection; schema migration (4 audit columns); `apply_patch_to_companion_context`; `APPLIED_LABELS` swap; operator-safety banner |

Cumulative diff stat (approximate, PI-0 spec excluded since it's pure docs):

- ~1,300 lines of production Python across `tokenpak/proxy/intent_prompt_patch*.py`, `tokenpak/proxy/intent_claude_code_preview.py`, and `tokenpak/companion/intent_injection.py`
- ~1,000 lines of CLI extensions in `tokenpak/cli/_impl.py` (`claude-code intent` + `claude-code patches` subcommands)
- ~1,800 lines of pytest tests across 3 new test files
- ~1,000 lines of operator-facing documentation in `docs/reference/claude-code-intent-{preview,injection}.md`
- ~500 lines of internal design documentation in `docs/internal/specs/intent-prompt-intervention-spec-2026-04-26.md`

---

## 2. Current capabilities

Listed by what an operator can do **today** on a host running `tokenpak` from `main`:

### 2.1 Patch builder + telemetry (PI-1)

- Every classified Claude Code request is eligible for a `PromptPatch` candidate when `intent_policy.prompt_intervention.enabled = true` AND every PI-1 eligibility gate aligns (13 gates, including byte-preserve respect, `target != user_message`, `mode != rewrite_prompt`, confidence ≥ threshold, no catch-all, suggestion not expired, decision_reason explainable, applicable template exists).
- The `PromptPatch` dataclass is **frozen + always advisory**: `applied = false` at build time. The PI-1 builder writes rows to a fourth Intent Layer SQLite table:

  - `intent_patches` (PI-1) — joined to `intent_events` by `contract_id`, `intent_policy_decisions` by `decision_id`, `intent_suggestions` by `suggestion_id`.

- Privacy contract: `patch_text` is a **fixed template parameterized only by `intent_class`**; no caller-supplied substring reaches the field. `original_hash` is a sha256 of the suggestion's identity tuple — explicitly NOT a hash of the user's prompt.

### 2.2 Claude Code Companion preview surface (PI-2)

Read-only join over the four Intent Layer tables, surfaced through two CLI commands and a JSON API output:

```bash
tokenpak claude-code intent [--last] [--json]    # full chain
tokenpak claude-code patches [--last] [--json]   # patch-only
```

Identity-separation labels (per PI-2 directive § 3):

| Label | Value | Purpose |
|---|---|---|
| `source_client` | `"claude_code"` | Client identity (added by the read model when the operator asks for the Claude-Code-shaped view) |
| `format_adapter` | `"AnthropicAdapter"` | Wire format — Claude Code uses Anthropic Messages; **no fake `ClaudeCodeAdapter`** |
| `credential_provider` | `"tokenpak-claude-code"` | OAuth + claude-code-20250219 beta auth path |
| `wire_emission` | `"telemetry_only"` | Pinned by Phase 0 §4.3 capability gate |

### 2.3 Companion-side opt-in injection (PI-3)

The first phase where TokenPak may apply a `PromptPatch`. Application is **companion-side only**, **explicitly opt-in**, and limited to `target = companion_context`. The library entry point is:

```python
from tokenpak.companion.intent_injection import (
    apply_patch_to_companion_context,
)
result = apply_patch_to_companion_context(
    patch_dict=patch,
    pi_config=cfg,
    existing_context=companion_context,
    store=store,
)
if result.success:
    companion_context = result.injected_context
```

The library is **idempotent**: a second call on an already-applied row returns `already_applied` without re-persisting.

Audit trail (4 new columns on `intent_patches`):

- `applied_at` — UTC ISO-8601 of successful injection
- `applied_surface` — `"claude_code_companion"`
- `application_mode` — `"inject_guidance"` (PI-3 only mode)
- `application_id` — opaque caller-side application token (16-hex)

### 2.4 CLI applied/preview labels

The CLI swaps label sets based on the latest patch row's `applied` state:

- **Unapplied** (default — every row when `prompt_intervention.enabled = false`):
  - `Claude Code Companion Intent Preview`
  - `PREVIEW ONLY`
  - `NOT APPLIED`
  - `NO PROMPT MUTATION`
  - `NO CLAUDE CODE INJECTION YET`
  - `Telemetry-only; no TIP intent headers emitted`
- **Applied** (PI-3 successful application):
  - `Claude Code Companion Intent Guidance`
  - `Applied for this one request`
  - `Injected into Claude Code companion context`
  - `User messages preserved`
  - `Proxy path remains byte-preserved-passthrough`
  - `Telemetry-only; no TIP intent headers emitted`

### 2.5 Operator safety banner

Every render of `tokenpak claude-code {intent,patches}` prints one of:

- `Prompt intervention is disabled. Guidance is preview-only.` (default — config not present or `enabled: false`)
- `Prompt intervention is enabled for Claude Code companion context only. User messages are preserved.` (only when every gate aligns: `enabled` + `mode = inject_guidance` + `target = companion_context` + `surfaces.claude_code_companion = true` + `surfaces.proxy = false` + `allow_byte_preserve_override = false` + `require_confirmation = false`)

### 2.6 Opt-in config

```yaml
intent_policy:
  prompt_intervention:
    enabled: true                          # default false
    mode: inject_guidance                  # rewrite_prompt rejected
    target: companion_context              # user_message rejected; system reserved
    require_confirmation: false            # set true to keep PI-3 lib refusing auto-apply
    allow_byte_preserve_override: false    # forced false by loader
    surfaces:
      claude_code_companion: true
      proxy: false                         # forced false by loader
```

Loader force-clamps three invariants regardless of file content:

1. `surfaces.proxy` → always `false`
2. `allow_byte_preserve_override` → always `false`
3. Both `target = user_message` and `mode = rewrite_prompt` are downgraded with a warning

---

## 3. What is intentionally not active

Listed by what is **explicitly out of scope** at this milestone:

- ❌ **No proxy injection.** `surfaces.proxy` is force-clamped to `false`. The AnthropicAdapter byte-preserved-passthrough invariant is unchanged. Structural test pins that the application library imports zero proxy dispatch primitives.
- ❌ **No `user_message` rewriting.** `target = user_message` is rejected by both the loader and the runtime library. There is no code path in PI-3 that touches user message bodies.
- ❌ **No `rewrite_prompt` mode.** Loader downgrades to `preview_only` with a warning; runtime library returns `wrong_mode` if a caller hand-builds the config.
- ❌ **No `target = system`.** Loader downgrades to `companion_context` with a warning; PI-4 reserved.
- ❌ **No TIP wire-header emission.** AnthropicAdapter does not declare `tip.intent.contract-headers-v1`; nothing changed in PI-3. Capability set is unchanged.
- ❌ **No byte-preserve override.** Force-clamped at the loader; defense-in-depth refusal in the runtime library.
- ❌ **No routing changes.** No code path under `tokenpak/companion/intent_injection.py` imports any routing primitive (`forward_headers`, `pool.request`, `pool.stream`, `RoutingService`, `credential_injector`). Structural test pins this.
- ❌ **No model/provider changes.** No code path resolves or switches providers based on patch content.
- ❌ **No classifier behavior changes.** The Phase 0 `rule_based_v0` keyword classifier is unchanged. `tokenpak/proxy/intent_classifier.py` is byte-identical to its Phase 0 (PR #44) state.
- ❌ **No automatic confirmation execution.** With `require_confirmation = true`, the PI-3 library refuses to auto-apply (returns `requires_confirmation`). PI-3 ships no approval gesture.
- ❌ **No MCP auto-injection.** PI-3 is library-only; the existing companion MCP server is unchanged. Any MCP-side resource that wraps the application library is a follow-up sub-phase requiring its own ratification.

---

## 4. Safety guarantees (always-on)

The following invariants hold at every host, every config, every code path through the Prompt Intervention sub-arc. Structural-import tests, sentinel-substring tests, and the Phase 2.4.1 + PI-1 forbidden-phrase regex pin them in CI.

### 4.1 Privacy

- Raw prompt text never enters `intent_patches`. The `original_hash` column is sha256 of `(contract_id || suggestion_id || suggestion_type)` — never a hash of the prompt body.
- The `patch_text` field is a fixed template (3 templates, parameterized only by `intent_class`); no caller-supplied substring reaches it.
- The PI-3 application library re-runs the privacy guardrail (scaffold credential-pattern regex) against `patch_text` before injecting. If a future template ever drifted to interpolate a credential, injection would be refused with `privacy_guardrail_blocked`.
- The PI-3 library emits no `print()` or `logger.{info,warning}` lines that include patch content; only the SQLite update on the audit columns is a side-effect.
- Sentinel-substring privacy tests across the read model, CLI human render, CLI JSON render, and the post-injection context string — **zero leaks across all surfaces** (`tests/test_intent_claude_code_preview_phase_pi_2.py::TestPrivacyContract`, `tests/test_intent_prompt_intervention_phase_pi_3.py::TestPrivacyContract`, `tests/test_intent_prompt_patch_phase_pi_1.py::TestPrivacyContract`).

### 4.2 Wire-fidelity

- `tip.byte-preserved-passthrough` invariant on AnthropicAdapter is unchanged. PI-3's application path runs **pre-bytes** (in the companion's context-assembly stage), so the byte-preserved-passthrough invariant downstream is preserved by construction.
- The PI-1 eligibility rule (g) hard-blocks any patch on a byte-preserved adapter UNLESS `target = companion_context`. Companion-side injection is the only authorized exit from this gate.
- No first-party adapter declares `tip.intent.contract-headers-v1` — pinned in `tests/test_intent_layer_phase01_invariant.py`. PI-3 does not change adapter capabilities; the structural assertion still holds.
- No new request-body / forward-header / target-URL writes were added in any PI-x PR. The proxy path is unchanged.

### 4.3 Forbidden wording — pre vs post

Two regexes pinned in tests:

1. **Phase 2.4.1 base set**: `applied`, `changed`, `routed to`, `switched to`, `now using`, `updated`, `will route`, `will switch`. Enforced at suggestion-build time by `_check_wording`.
2. **PI-1 additions**: `injected`, `mutated`, `rewrote`, `inserted`, `will inject`, `will rewrite`. Enforced at patch-build time.

Combined regex blocks all 14 phrases inside `patch_text` and `reason` of every `PromptPatch`. PI-3 keeps the same builder-side ban: `patch_text` continues to be a fixed template that contains none of these words.

The CLI surface MAY render `Applied`, `Inserted`, `Injected` — but **only** on rows where `applied = true` AND `applied_surface = claude_code_companion`. Pre-application, the CLI never says these words about the row. The pre/post distinction is asserted in `tests/test_intent_prompt_intervention_phase_pi_3.py::TestForbiddenWordingBeforeApplication` and `::TestAppliedWordingAfterApplication`.

### 4.4 Default-off posture

- Default config: `prompt_intervention.enabled = false`, `mode = preview_only`, `target = companion_context`, `require_confirmation = true`, `allow_byte_preserve_override = false`, `surfaces.claude_code_companion = false`, `surfaces.proxy = false`.
- A host that has not edited `~/.tokenpak/policy.yaml` sees zero prompt-intervention behavior. Every patch row stays `applied = false`. The CLI shows `Prompt intervention is disabled. Guidance is preview-only.`

### 4.5 Force-applied loader overrides

The PI-3 loader force-clamps three invariants regardless of file content:

1. `surfaces.proxy` is forced `false`. (Locked through every PI-x sub-phase.)
2. `allow_byte_preserve_override` is forced `false`. (Locked indefinitely until a future spec authorizes it.)
3. `target = user_message` is rejected and downgraded to `companion_context` with a warning. (Reserved indefinitely.)

Each emits a warning at load time when an override is attempted; the warning is logged at `WARNING` level.

### 4.6 Defense-in-depth runtime refusals

Even when a caller hand-builds a `PromptInterventionRuntimeConfig` that bypasses the loader, the application library refuses to apply when:

- `pi_config.surfaces.proxy` is `true` → returns `proxy_surface_forced_off`
- `pi_config.allow_byte_preserve_override` is `true` → returns `byte_preserve_override_blocked`
- `pi_config.target == "user_message"` → returns `wrong_target`
- `pi_config.mode == "rewrite_prompt"` → returns `wrong_mode`
- `pi_config.require_confirmation == true` → returns `requires_confirmation`
- `patch.applied == true` → returns `already_applied`
- `patch.source != "intent_policy_v0"` → returns `wrong_source`

Each refusal is asserted in `tests/test_intent_prompt_intervention_phase_pi_3.py`.

---

## 5. CI / test coverage summary

Suite size at the milestone (relative to the Intent Advisory MVP closeout baseline):

| Phase | Test file | Tests | Cumulative `pytest tests/` |
|---|---|---|---|
| Intent Advisory MVP closeout baseline | (existing) | — | 1208 |
| PI-0 spec | (no code) | 0 | 1208 |
| PI-1 | `test_intent_prompt_patch_phase_pi_1.py` | 40 | 1248 |
| PI-2 | `test_intent_claude_code_preview_phase_pi_2.py` | 24 | 1272 |
| PI-3 | `test_intent_prompt_intervention_phase_pi_3.py` | 32 | 1304 |
| **Claude Code Intent Guidance MVP total** | **3 test files** | **96 tests** | **1304** |

CI workflows that gated every PI-x PR:

- `CI — Lint & Test` (Lint Ruff + Test 3.10 / 3.11 / 3.12 / 3.13 + Import contracts + bandit + cli-docs-in-sync + headline-benchmark)
- `TIP-1.0 Self-Conformance` (3.10 / 3.11 / 3.12)
- `Repo Hygiene Check` (×2)

Every PR in the workstream merged with **all 17 checks green**, no CI round-trips required across PI-1 / PI-2 / PI-3.

### 5.1 Cross-phase invariants pinned in tests

- **PI-1 forbidden-phrase regex** (`injected` / `mutated` / `rewrote` / `inserted` / `will inject` / `will rewrite` + Phase 2.4.1 base set) — pinned in `tests/test_intent_prompt_patch_phase_pi_1.py::TestForbiddenWording` + re-asserted in PI-2 and PI-3.
- **Privacy sentinel** — sentinel-substring test in every PI-x test file.
- **Structural "no dispatch primitives imported" tests** — PI-3 module under `tokenpak/companion/intent_injection.py` is structurally verified to NOT import `forward_headers`, `pool.request`, `pool.stream`, `credential_injector`, or any routing-service primitive.
- **No `ClaudeCodeAdapter`** — PI-2 + PI-3 tests pin that the format adapter remains `AnthropicAdapter` and only the `source_client` label distinguishes Claude Code traffic.
- **No TIP intent header emission** — structural test in PI-3 verifies the application library has zero references to `X-TIP-Intent` / `tip-intent-headers`.
- **Byte-preserve respect** — PI-1 eligibility test (g) + PI-3 force-clamp + runtime defense-in-depth refusal.
- **Idempotent application** — PI-3 test asserts second call on an already-applied row returns `already_applied` without re-persisting.

---

## 6. CLI / surface inventory

Quick reference for operators. Every surface is read-only except the PI-3 application library (which is library-only — no CLI verb applies a patch).

### CLI

```bash
# Full chain (event + decision + suggestion + patch) Claude-Code-shaped
tokenpak claude-code intent
tokenpak claude-code intent --last
tokenpak claude-code intent --json

# Patch-only view
tokenpak claude-code patches
tokenpak claude-code patches --last
tokenpak claude-code patches --json
```

Every render prints the operator-safety banner at the top of the output, the six-label set (PREVIEW or APPLIED depending on the latest patch row's state), and the identity-separation block.

### Library

```python
from tokenpak.companion.intent_injection import (
    apply_patch_to_companion_context,
    ApplicationResult,
)
from tokenpak.proxy.intent_policy_config_loader import (
    load_prompt_intervention_config_safely,
)
from tokenpak.proxy.intent_prompt_patch_telemetry import (
    get_default_patch_store,
)
```

The library is the **single application entry point** for PI-3. There is no CLI verb that triggers `applied = true`; the companion subsystem is the only authorized caller.

### Schema

`~/.tokenpak/telemetry.db` — `intent_patches` table with PI-1 base columns + PI-3 audit columns:

```
patch_id, contract_id, decision_id, suggestion_id, created_at,
mode, target, original_hash, patch_text, reason, confidence,
safety_flags, requires_confirmation, applied, source,
applied_at, applied_surface, application_mode, application_id
```

Indices: `idx_patches_suggestion`, `idx_patches_contract`, `idx_patches_mode`, `idx_patches_applied`, `idx_patches_applied_surface` (PI-3 added the last one).

---

## 7. Deferred roadmap

The following phases are designed but not implemented. **None of them may proceed without explicit Kevin approval.**

| Phase | Scope | Spec status |
|---|---|---|
| **PI-4** | `target = system` + `mode = rewrite_prompt`. Adapter-level injection for adapters that do NOT declare `tip.byte-preserved-passthrough`. Requires a separate ratification because it lands on an adapter wire path (vs. companion-side pre-bytes). | PI-0 spec § 6 sketches; full sub-spec to be written |
| **PI-x — MCP-side resource wrapper** | Read-only MCP resources (e.g. `tokenpak.claude_code.intent.latest`, `tokenpak.claude_code.patches.latest`) that mirror the CLI surface. Rules in `docs/reference/claude-code-intent-preview.md` § "MCP integration (deferred)". | Not started |
| **PI-x — Approval gesture** | A confirmation handshake that lets the operator approve a single patch application when `require_confirmation = true`. Either a CLI verb or an MCP tool; rules from Phase 2.5 confirm-mode spec apply. | Not started; depends on Phase 2.5.1 + 2.5.3 ratification |
| **PI-x — Cross-host audit aggregation** | A read-model API endpoint that surfaces `intent_patches` rows across a fleet (multi-host telemetry already centralized in monitor.db). Read-only. | Not started |

---

## 8. Future implementation entry points

When work resumes on a deferred phase, these are the canonical entry points:

| Deferred phase | First file to edit / create |
|---|---|
| PI-4 (target=system) | New sub-spec at `docs/internal/specs/intent-prompt-intervention-pi-4-spec-<date>.md` first; THEN extend `tokenpak/proxy/intent_prompt_patch.py::PI_1_SUPPORTED_TARGETS` after the spec is ratified |
| PI-4 (rewrite_prompt) | Same — sub-spec first; THEN extend `tokenpak/proxy/intent_prompt_patch.py::PI_1_SUPPORTED_MODES` |
| PI-4 (adapter wire injection) | `tokenpak/proxy/adapters/anthropic_adapter.py::inject_system_context` already exists for vault context; a PI-4 caller would need a new `IntentInjectionStrategy` plumbing layer |
| MCP-side resource | `tokenpak/companion/mcp_server/_impl.py` — add a new resource handler that calls `collect_latest_preview` / `collect_latest_patch_preview` |
| Approval gesture | `tokenpak/cli/_impl.py::cmd_intent_confirm` (new) OR `tokenpak/companion/mcp_server/_impl.py` — gated on Phase 2.5.3 ratification |
| Cross-host audit | `tokenpak/dashboard/intent_*.js` panel + new `/api/intent/patches-report` endpoint |

Each entry point links back to the relevant spec for context. **None of these files should be created or edited without explicit approval.**

---

## 9. Do not implement without explicit approval

The following capabilities are intentionally **NOT** part of the Claude Code Intent Guidance MVP and **MUST NOT** be implemented without explicit Kevin approval per phase:

- ❌ **PI-4** — `target = system` + `mode = rewrite_prompt`. Spec is sketched in PI-0 § 6; full sub-spec must be written and ratified before any code lands.
- ❌ **System prompt targeting** — any code path that resolves `target = system` past the loader's downgrade. The PI-1 builder rejects unknown targets; do NOT extend `PI_1_SUPPORTED_TARGETS` without ratification.
- ❌ **`rewrite_prompt` mode** — any code path that resolves `mode = rewrite_prompt` past the loader's downgrade. The PI-1 builder rejects unknown modes; do NOT extend `PI_1_SUPPORTED_MODES` without ratification.
- ❌ **Proxy-level injection** — any code path that mutates `body`, `fwd_headers`, or `target_url` based on engine output, on any adapter. The `surfaces.proxy` flag is force-clamped to `false`; do NOT add a code path that bypasses this clamp.
- ❌ **Byte-preserve override** — any code path that flips `allow_byte_preserve_override` to `true` at runtime, regardless of config-file state. The loader force-clamps it; do NOT add a code path that respects a `true` value.
- ❌ **Automatic prompt mutation** — any code path that injects guidance without an explicit operator opt-in (`prompt_intervention.enabled = true` AND `surfaces.claude_code_companion = true` AND `require_confirmation = false`). Default-off remains the only authorized posture.
- ❌ **Routing changes based on intent** — any code path that re-routes a request to a different provider/model/adapter based on `intent_class` or `recommended_provider` / `recommended_model`. Auto-routing is locked off by the Intent Advisory MVP §4.4 invariant; PI-3 inherits and extends that lock.

These rules are not tested directly by code (since the absence-of-feature can't be tested) but ARE pinned by:

- The PI-3 loader's three force-applied safety overrides (`surfaces.proxy`, `allow_byte_preserve_override`, `target = user_message`).
- The PI-3 runtime library's six defense-in-depth refusals (`proxy_surface_forced_off`, `byte_preserve_override_blocked`, `wrong_target`, `wrong_mode`, `requires_confirmation`, `wrong_source`).
- The PI-1 builder's 13 eligibility gates including (b) mode rejection, (c) `target = user_message` rejection, (d) byte-preserve-override rejection, (g) byte-preserve-respect with companion-context exception.
- The structural "no dispatch primitives imported" tests across `tokenpak/companion/intent_injection.py`.
- The Phase 0 invariant test that asserts no first-party adapter declares the gate label.

Any PR that attempts to add behavior in the above list MUST cite a fresh, explicit Kevin approval in the PR description. Without that approval, the PR should not be opened.

---

## 10. Document index

The Claude Code Intent Guidance MVP is documented in five linked surfaces. Each links the others; this section is the canonical entry table for future readers:

| Document | Type | Audience |
|---|---|---|
| [`docs/reference/claude-code-intent-preview.md`](../../reference/claude-code-intent-preview.md) | Operator-facing reference (PI-2) | Operators inspecting the preview surface |
| [`docs/reference/claude-code-intent-injection.md`](../../reference/claude-code-intent-injection.md) | Operator-facing reference (PI-3) | Operators enabling companion-side injection |
| [`docs/internal/specs/intent-prompt-intervention-spec-2026-04-26.md`](../specs/intent-prompt-intervention-spec-2026-04-26.md) | Internal design spec (PI-0) | Future contributors understanding the design |
| [`docs/internal/milestones/intent-advisory-mvp-2026-04-26.md`](intent-advisory-mvp-2026-04-26.md) | Milestone closeout (Phase 0 → Phase 2.5 spec) | Predecessor milestone |
| **This document** | Milestone closeout (PI-0 → PI-3) | The MVP this closes out |

---

## 11. Acknowledgements + provenance

The Claude Code Intent Guidance Injection sub-arc began on 2026-04-26 with the PI-0 directive (immediately after the Intent Advisory MVP closeout) and ended on 2026-04-26 with the PI-3 merge. Four PRs across approximately 8 hours of execution time, four directives (PI-0 spec, PI-1, PI-2, PI-3), zero behavioral regressions, zero CI round-trips.

Origin: PI-0 directive on 2026-04-26 — Kevin's request to scope a unified Prompt Intervention design spec before any builder / surface / injection code was written.

Foundation pattern: every behavior change in this sub-arc rests on:

1. The Intent Advisory MVP (`docs/internal/milestones/intent-advisory-mvp-2026-04-26.md`) — provided the four upstream tables (`intent_events`, `intent_policy_decisions`, `intent_suggestions`, plus this sub-arc's `intent_patches`) and the suggestion-builder pattern that PI-1 extended.
2. Standard #23's capability-gated middleware-activation pattern — `tip.byte-preserved-passthrough` is the load-bearing structural invariant that allows companion-side injection to coexist with proxy-side byte-fidelity.
3. The Phase 2.4.3 config-loader's three force-applied safety overrides — PI-3 added three more (`surfaces.proxy`, `allow_byte_preserve_override`, `target = user_message`), following the same "fail closed" pattern.

Closeout: this milestone is **complete** at 2026-04-26 22:48:57Z (PR #60 merge timestamp). The next deferred phase (PI-4) requires explicit Kevin approval before code lands.

After this closeout, the workstream pivots away from Intent work.
