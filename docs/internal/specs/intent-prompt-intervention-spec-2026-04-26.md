# Intent Prompt Intervention — design / spec only (PI-0)

**Date**: 2026-04-26
**Status**: design draft, awaiting Kevin ratification
**Authors**: Sue (design) / Kevin (review)
**Workstream**: Intent Layer follow-on (post-Intent-Advisory-MVP)
**Foundation**:
  - Intent Advisory MVP closeout — `docs/internal/milestones/intent-advisory-mvp-2026-04-26.md`
  - Phase 2 unified policy engine spec — `docs/internal/specs/phase2-intent-policy-engine-spec-2026-04-25.md`
  - Phase 2.4 suggest mode — `docs/internal/specs/phase2.4-suggest-mode-spec-2026-04-26.md`
  - Phase 2.5 confirmation mode — `docs/internal/specs/phase2.5-confirm-mode-spec-2026-04-26.md`
**Implements** (when ratified, in PI-1+ sub-phases): nothing yet — PI-0 (this PR) is **spec only**.

---

## 0. Reading guide

Structured 1:1 against the directive's ten required sections.

| § | Question |
|---|---|
| 1 | What gap in Intent Advisory does this layer close? |
| 2 | What is explicitly NOT being designed? |
| 3 | How does a `PromptPatch` flow through the existing pipeline? |
| 4 | What does a `PromptPatch` look like on the wire? |
| 5 | What modes can a patch operate in? |
| 6 | How does this land in Claude Code (companion-first)? |
| 7 | When is a patch eligible to be constructed / applied? |
| 8 | How is intervention configured? |
| 9 | What stays private even with intervention enabled? |
| 10 | How does this roll out from spec → narrow-rewrite mode? |

Sections 1, 2, 7, 9, and the acceptance block in §11 are normative; the remainder is design.

---

## 1. Problem

The Intent Advisory MVP (closed out 2026-04-26) shipped end-to-end **observation** + **advisory suggestion**:

- Phase 0–1.1: classify every request, write telemetry, surface metrics through CLI / API / dashboard.
- Phase 2.1–2.2: pure-function dry-run policy engine emitting `PolicyDecision` + visible explain/report/dashboard surfaces.
- Phase 2.4.1–2.4.3: `PolicySuggestion` builder + 5 surface integrations + opt-in `mode = suggest` config.
- Phase 2.5 spec (ratified, not implemented): confirmation handshake for one-time approved actions.

What the MVP **cannot** do: change the actual prompt before it reaches the model. A summarize-intent request tagged `confidence = 0.92` shows up in `intent_events`; the engine emits `suggest_compression_profile = aggressive`; the suggestion lands in the dashboard with the canonical advisory label. **The model still sees the original prompt**, unmodified.

This gap is structural by design — Phase 2 / 2.4 / 2.5 are all about **observation and advisory feedback**. The directive scoping kept request-bytes byte-stable so Architecture §5.1 byte-fidelity stayed intact across the workstream.

Intent Prompt Intervention (PI) closes that gap with a **narrow, opt-in, audit-first** layer that lets TokenPak (or a TokenPak-aware companion like Claude Code Companion) inject **guidance** — system-message hints, clarifying questions, optional rewrites — based on the engine's classification. The layer is intentionally bounded:

- **Companion-first.** The first target is Claude Code Companion's `companion_context` block. The proxy itself stays out of the mutation path until later phases.
- **Guidance, not rewrite.** PI-1 / PI-2 / PI-3 only inject guidance into the system surface or companion context. PI-4 introduces the narrow rewrite mode under additional gates.
- **Original message preserved.** PI never deletes or rewrites the user's actual prompt content in PI-1 / PI-2 / PI-3. Even PI-4's rewrite mode produces a parallel string and lets the caller decide which to send.
- **Byte-preserve respected.** Routes whose adapter declares `tip.byte-preserved-passthrough` (Anthropic, Codex Responses, Claude Code paths) are off-limits unless the operator explicitly overrides — and the override has its own additional safety gates.

PI extends Intent Advisory's "visible / advisory / opt-in" posture from *what to show* into *what to do*. The bridge stays narrow and revertible.

---

## 2. Non-goals

The following are explicitly NOT being designed in PI-0. Each is a statement of *what this spec does not authorize*:

| Non-goal | Why it's out of scope for PI-0 |
|---|---|
| **No silent prompt rewriting** | Every patch — even `preview_only` — must be visible in the doctor / explain / report surfaces. There is no code path that produces a patch invisible to the operator. |
| **No default mutation** | Default config is `prompt_intervention.enabled = false`. A host that has not edited config sees zero mutation, zero injection, zero new surfaces. |
| **No provider/model switching** | PI is orthogonal to provider selection. A patch never reroutes; it only modifies the prompt content sent to the resolved provider. The Phase 2 / 2.4 / 2.5 routing-related actions remain in their own scope. |
| **No request mutation without explicit opt-in** | Even when enabled, the `require_confirmation` gate (§8 config) defaults to `true`. Approved patches still surface a confirmation request before applying. |
| **No raw prompt logging** | Prompt content stays out of telemetry. Patch storage carries `original_hash` (sha256) + `patch_text` (the inserted string only — not the prompt it was inserted into). The Phase 0 / 2.4 privacy contract carries through unchanged. |
| **No mutation on byte-preserved routes unless explicitly disabled/overridden by policy** | The Anthropic adapter declares `tip.byte-preserved-passthrough` because billing routing depends on byte-fidelity (cache_control alignment, OAuth-mode quota). Mutation breaks this. PI's eligibility §7 hard-blocks patches on these routes; the `allow_byte_preserve_override` flag (§8 config) re-enables the path with a separate spec-level ratification still required per phase. |
| **No classifier behavior changes** | `tokenpak/proxy/intent_classifier.py` remains untouched in every PI-x sub-phase. PI reads classifier output; it never re-tunes or re-weights. |
| **No TIP wire header emission for patches** | A `PromptPatch` never produces an `X-TokenPak-Patch-*` header. The patch is a body-content concern, not a metadata concern. The Phase 0 §4.3 capability gate already governs intent / contract / suggestion headers; PI doesn't add new wire surfaces. |
| **No autonomous patch approval** | Approval flows through the same Phase 2.5 confirmation handshake — explicit gesture, no implicit time-based approval, no chained approvals. |
| **No cross-request patch inheritance** | Each patch maps to one `contract_id`. An applied patch for request A does NOT silently apply to request B even if the suggestion is identical. |

Tested invariantly under §10 via the structural and sentinel-substring patterns established in Phase 0 / 2.4 / 2.5.

---

## 3. Architecture

PI sits **after** the Phase 2.4.1 suggestion builder and **before** any opt-in execution. It does not replace any existing layer.

```
                 (existing pipeline, unchanged through PI-0/PI-1)
   request ─► classify ─► PolicyDecision ─► PolicySuggestion ─► (telemetry / surfaces)
                                                       │
                                                       │ PI eligibility (§7)
                                                       ▼
                                                ┌──────────────┐
                                                │ build_patch  │   PI-1 deliverable
                                                │ (pure func)  │
                                                └──────┬───────┘
                                                       │ PromptPatch
                                                       ▼
                                                ┌──────────────┐
                                                │ persist to   │
                                                │ intent_      │   PI-1 deliverable
                                                │ patches table│
                                                └──────┬───────┘
                                                       │
                                                       ▼
                              (PI-2: companion preview surface — NO application)
                              (PI-3: opt-in guidance injection — companion_context only)
                              (PI-4: narrow rewrite mode — companion_context only, gated)
```

### 3.1 Pipeline relationship

- **Inputs to PI**: an existing `PolicySuggestion` row (Phase 2.4.1) + its linked `IntentContract` (Phase 0) + its linked `PolicyDecision` (Phase 2.1) + the active `PolicyEngineConfig` (Phase 2.4.3) + the resolved request adapter's capabilities.
- **Outputs from PI**: zero or more `PromptPatch` rows in a new `intent_patches` SQLite table (PI-1 deliverable) + corresponding doctor / explain / report visibility (PI-2 deliverable) + optional one-time application via the Phase 2.5 confirmation pipeline (PI-3 / PI-4).

### 3.2 What the pipeline does NOT do

- **No automatic application.** PI-1 and PI-2 land patches in telemetry only; no surface applies them automatically.
- **No re-classification.** The classifier runs once per request (Phase 0 invariant). PI never invokes it.
- **No fan-out across decisions.** One policy decision → at most one patch (the type taxonomy in §5 is mutually exclusive at any given decision).
- **No proxy-side application in early phases.** The default `surfaces.proxy = false` means even when patches are generated, the proxy doesn't apply them on the wire. Only the companion does, and only when its surface flag is true (§8 config).

### 3.3 Cross-reference invariants

The Phase 0 / 2.4 / 2.5 invariants flow through unchanged:

- **§4.3 capability gate** (Standard #23): wire-side metadata still gated by `tip.intent.contract-headers-v1`. PI doesn't add new wire emissions.
- **Byte-fidelity** (Architecture §5.1): on byte-preserved routes, no mutation. Eligibility gate §7 enforces.
- **Prompt locality** (Architecture §7.1): raw prompt content stays in the per-request log. PI stores a digest, not the prompt.
- **Default-off** (Phase 2.4 spec §10): config default disables PI entirely.
- **Forbidden-wording guardrail** (Phase 2.4.1): every emitted `patch_text` and `reason` field is scanned through the same regex. A patch trying to inject "Applied" / "Changed" / "Routed to" wording would raise `SuggestionWordingError` at build time.

---

## 4. PromptPatch object

```python
@dataclass(frozen=True)
class PromptPatch:
    """One operator-visible prompt-patch candidate. Always advisory
    in PI-0/PI-1; surfaces decide visibility (PI-2); approval gates
    application (PI-3/PI-4).

    All fields except identity, mode, target, original_hash,
    patch_text, applied, source are nullable. JSON serializer
    emits explicit null for unset fields so consumers can rely on
    field presence (matches the Phase 2.4.1 / 2.5 schema-stability
    convention).
    """
    # Identity
    patch_id:               str          # ULID; sortable; per-suggestion
    contract_id:            str          # links to intent_events row
    decision_id:            str          # links to intent_policy_decisions row
    suggestion_id:          str          # links to intent_suggestions row

    # What this patch IS
    mode:                   str          # one of §5: preview_only | inject_guidance | ask_clarification | rewrite_prompt
    target:                 str          # one of §6 / §8: companion_context | system | user_message
    original_hash:          str          # sha256 hex of the original prompt-equivalent string (templated, NOT the raw prompt)
    patch_text:             str          # the templated string to inject. NEVER includes raw prompt content.
    reason:                 str          # rendered decision_reason (templated; same allowlist as Phase 2.4.1)

    # Provenance / risk
    confidence:             float        # echoes contract.confidence; ∈ [0, 1]
    safety_flags:           tuple[str, ...]  # echoes upstream safety flags

    # Lifecycle
    requires_confirmation:  bool         # always True in PI-1; PI-3 honors per the §8 config flag
    applied:                bool         # always False until PI-3 / PI-4 actually applies; tracked here for the explain surface
    source:                 str          # always "intent_prompt_intervention_v0" through PI-x line
```

### 4.1 Field rules

- `patch_id` — 29-char hex (ms timestamp + 16 random hex). Same shape as `decision_id` / `suggestion_id` for sortability.
- `contract_id`, `decision_id`, `suggestion_id` — required join keys to the existing Intent Advisory tables. A patch that can't link back to all three is invalid (eligibility rule §7).
- `mode` — one of the four §5 values. Validated at build time; unknown modes raise `PatchModeError`.
- `target` — one of `companion_context` / `system` / `user_message`. The set of allowed targets per phase is narrower than the set of declared targets (see §6 / §8 / §10). PI-1 / PI-2 / PI-3 limit `target` to `companion_context`; `system` is PI-4-only; `user_message` is reserved indefinitely (and even then, never silently rewrites).
- `original_hash` — sha256 hex of the **prompt-equivalent string** the patch was computed against. NOT the full request body. Used for dedup / staleness detection (a patch built against prompt X must not be applied to a different prompt Y).
- `patch_text` — the actual string the patch will inject when applied. Constructed from a fixed template table (mirrors the Phase 2.4.1 wording-template approach); no caller-supplied substring ever reaches this field. Bounded length (≤ 1024 chars in PI-1) to prevent runaway template expansion.
- `reason` — short, templated, plain-English clause reusing the Phase 2.4.1 reason-rendering table.
- `confidence` — pinned to the contract's confidence; not a separate "patch confidence."
- `safety_flags` — echoes the upstream suggestion's safety flags + any patch-specific ones (e.g. `byte_preserve_locked` if the route is byte-preserved and the override flag isn't set).
- `requires_confirmation` — always `True` in PI-1 (the schema is pinned for forward-compat with PI-3). PI-3 honors the host config flag.
- `applied` — `False` for every patch on every Phase 2.5 path; flips to `True` only after PI-3/PI-4 successfully applies the patch.
- `source` — pinned to `"intent_prompt_intervention_v0"` for the entire PI-x line.

### 4.2 Wire shape

```json
{
  "applied": false,
  "confidence": 0.92,
  "contract_id": "01h0...abc",
  "decision_id": "01h0...def",
  "mode": "inject_guidance",
  "original_hash": "ab3c...e0",
  "patch_id": "01h0...xyz",
  "patch_text": "Note: this request matches the canonical 'summarize' intent — recommended approach is to focus on the period boundary and elide message-level detail.",
  "reason": "the canonical heuristic table recommends it",
  "requires_confirmation": true,
  "safety_flags": [],
  "source": "intent_prompt_intervention_v0",
  "suggestion_id": "01h0...ghi",
  "target": "companion_context"
}
```

### 4.3 Telemetry table

`intent_patches` (PI-1 deliverable). Schema is IDs / hashes / templated text only. No raw prompt content. Linked to `intent_suggestions` by `suggestion_id`, to `intent_policy_decisions` by `decision_id`, to `intent_events` by `contract_id`.

```sql
CREATE TABLE intent_patches (
    patch_id              TEXT PRIMARY KEY,
    contract_id           TEXT NOT NULL,
    decision_id           TEXT NOT NULL,
    suggestion_id         TEXT NOT NULL,
    timestamp             TEXT NOT NULL,
    mode                  TEXT NOT NULL,
    target                TEXT NOT NULL,
    original_hash         TEXT NOT NULL,
    patch_text            TEXT NOT NULL,
    reason                TEXT NOT NULL,
    confidence            REAL NOT NULL,
    safety_flags          TEXT NOT NULL,    -- JSON array
    requires_confirmation INTEGER NOT NULL,
    applied               INTEGER NOT NULL,
    source                TEXT NOT NULL
);
CREATE INDEX idx_patches_suggestion ON intent_patches (suggestion_id);
CREATE INDEX idx_patches_contract   ON intent_patches (contract_id);
CREATE INDEX idx_patches_mode       ON intent_patches (mode, timestamp);
CREATE INDEX idx_patches_applied    ON intent_patches (applied, timestamp);
```

---

## 5. Modes

Four modes. Each maps to a different intervention strategy and a different set of allowed targets.

| Mode | What it does | Allowed `target` | Phase rollout |
|---|---|---|---|
| `preview_only` | Builds the patch + persists to `intent_patches`. **No application** anywhere. The doctor / explain / report surfaces show the patch with `applied = false`. | Any (validated downstream) | PI-1 |
| `inject_guidance` | Adds a templated guidance string to the targeted surface (e.g. companion_context). Original user message preserved. | `companion_context` only in PI-3; `system` only in PI-4 (with extra gates) | PI-3 |
| `ask_clarification` | Generates a clarification question the surface presents to the user. Does not auto-inject; the surface decides whether to surface the question. Bypasses the missing-required-slots eligibility gate (§7), since the whole point of `ask_clarification` is to fill in missing slots. | Any (the question is a separate render concern; doesn't mutate the prompt body) | PI-3 |
| `rewrite_prompt` | Generates a rewritten version of the prompt-equivalent string. **Does NOT replace the original**; produces a parallel string that the caller can choose to send. PI-4 introduces this mode under additional gates beyond the rest. | `companion_context` only — never `system` or `user_message` until a future explicit ratification | PI-4 |

### 5.1 Mode invariants

- **`preview_only` is always available.** It's the safe baseline and the default mode. Hosts that want patch generation but no application configure `mode = preview_only`.
- **`inject_guidance` and `ask_clarification` honor `target`.** The §8 config field decides where guidance lands.
- **`rewrite_prompt` never replaces text in PI-4.** The patch carries a rewritten parallel string; the surface (companion or proxy) decides whether to use it. PI-4's surface code prefers the original by default and requires explicit operator action (in addition to the Phase 2.5 confirmation gesture) to switch.
- **No mode can produce a patch with `target = user_message` in PI-1 → PI-4.** The user-message target is reserved for a future phase past PI-4 with its own ratification.

---

## 6. Claude Code strategy

Claude Code Companion is the first target because:

1. The companion already owns a context-injection surface (`companion_context` block) — guidance has a natural home there.
2. The companion runs **client-side**, before the request hits the proxy. PI-3 / PI-4 in the companion happen pre-byte-preserve-lock, sidestepping the byte-fidelity concern.
3. The Anthropic adapter declares `tip.byte-preserved-passthrough` precisely because Claude Code's caching / quota routing depends on byte fidelity. Companion-side injection inserts guidance **before** the prompt becomes the Anthropic byte payload — the resulting bytes carry the guidance + original message and are byte-preserved through the rest of the path. The proxy-side path stays clean.
4. Claude Code traffic is the highest-volume Anthropic-OAuth route on Kevin's fleet; baseline data + opt-in coverage will be richest here.

### 6.1 First-target strategy

| Element | Strategy |
|---|---|
| **Surface** | `companion_context` field in the companion's prompt-assembly path. PI-2 surfaces the patch as preview; PI-3 wires opt-in injection. |
| **System message handling** | PI-1 / PI-2 / PI-3 do **not** modify the system message. PI-4 introduces a narrow `target = system` mode behind extra gates; even then, the patch only **prepends** a labeled guidance block — never replaces the user's existing system message. |
| **User message handling** | **Never touched in PI-1 → PI-4.** The user's original message bytes flow through unchanged. Eligibility §7 explicitly blocks `target = user_message`. |
| **Source labeling** | Every injected guidance block carries the literal prefix `[TokenPak Intent Guidance]` so the model and any downstream tool can identify the source. The label is part of the templated `patch_text`. |
| **Adapter coordination** | The companion knows the eventual adapter (Anthropic Messages) by request path. PI-3 / PI-4 only emit when the adapter capability set + the host's `allow_byte_preserve_override` flag align. |

### 6.2 Companion changes (deferred to PI-2 / PI-3)

PI-0 (this spec) reserves these companion-side hooks; the actual code lands in PI-2 / PI-3:

- New companion module (likely `tokenpak/companion/intent_guidance.py`) that fetches the latest applicable patch via the existing `/api/intent/...` API surface (or directly from `intent_patches` SQLite when companion + proxy share a host).
- Hook-point in the companion's prompt-assembly stage where the patch (when present + eligible + opted-in) inserts its guidance block before the request leaves the companion.
- A new companion-CLI verb (e.g. `tokenpak companion patch --preview` / `--apply`) to drive the same operations end-to-end.

### 6.3 Why companion-first instead of proxy-first

- **Proxy-side mutation breaks byte-preserved routes.** Anthropic OAuth quota routing, cache_control alignment, and the existing byte-fidelity invariant all depend on the request body being identical to what the caller sent. Mutating in the proxy breaks the invariant unconditionally.
- **Companion-side mutation operates pre-bytes.** The companion is the prompt assembler for Claude Code; mutating its output produces *new* bytes that are then byte-preserved through the proxy. No byte-fidelity violation.
- **Operator visibility is symmetric.** Whether the patch lands companion-side or (one day) proxy-side, every patch is recorded in `intent_patches` and visible through the CLI / API / dashboard surfaces. The mutation point doesn't affect auditability.

PI's `surfaces.proxy = false` default mirrors this. Proxy-side mutation is reserved for a future ratification well beyond PI-4.

---

## 7. Eligibility gates

A `PolicySuggestion` becomes a `PromptPatch` only when **all** of the following hold. Each rule maps to a §6 safety rule from Phase 2 / 2.4 / 2.5 plus three new gates specific to prompt mutation.

| # | Rule | Always on? | Maps to |
|---|---|---|---|
| (a) | `intent_policy.prompt_intervention.enabled = true` (i.e. host explicitly opted in) | yes | new — PI-specific |
| (b) | `confidence >= low_confidence_threshold` (default 0.65) | yes | Phase 2.4 §4 rule (a) carry-through |
| (c) | `catch_all_reason is None` | yes | Phase 2.4 §4 rule (b) carry-through |
| (d) | No required slot in `slots_missing` — **except** when `mode = ask_clarification` (the whole point of which is to ask for missing slots) | yes (with the documented exception) | Phase 2.4 §4 rule (c) carry-through |
| (e) | Route is not byte-preserve locked, OR `allow_byte_preserve_override = true` AND a separate per-phase ratification has authorized the override | yes | new — load-bearing safety. Concretely: when the resolved adapter declares `tip.byte-preserved-passthrough`, PI rejects the patch unless override is set. |
| (f) | User / workspace policy allows mutation — i.e. config file enables it AND any per-workspace overrides agree | yes | new — covers multi-tenant or shared-host hosts |
| (g) | `decision_reason` is in the `EXPLAINABLE_REASONS` allowlist (Phase 2.4.1 carry-through; new reasons specific to PI like `prompt_intervention_recommended` get added to the allowlist in PI-1) | yes | Phase 2.4 §4 rule (g) carry-through |
| (h) | `patch_text` passes the privacy + wording guardrails (no caller substring, no forbidden phrase, ≤ 1024 chars) | yes | Phase 2.4.1 forbidden-phrase regex carry-through |

### 7.1 Byte-preserve enforcement detail

The byte-preserve check operates on the resolved `FormatAdapter.capabilities` set. Adapters declaring `tip.byte-preserved-passthrough` (in the current registry: `AnthropicAdapter`, `PassthroughAdapter`) are byte-preserve locked. Without `allow_byte_preserve_override = true`, the eligibility builder returns no patch and instead populates `safety_flags = ["byte_preserve_locked"]` on the underlying suggestion (where the surface can show it).

Companion-side injection avoids this concern because it runs **before** the proxy resolves the adapter. The `target = companion_context` mode is therefore **not** subject to rule (e); the eligibility code special-cases this so a companion-side patch on an Anthropic-bound request still builds.

### 7.2 ask_clarification special-case

The `mode = ask_clarification` path exists precisely to ask the user about missing slots. Rule (d) would otherwise block it (since `slots_missing` would be non-empty). The eligibility code special-cases `ask_clarification` to bypass rule (d); all other rules apply.

### 7.3 Eligibility evaluation order

```python
# Pseudocode — implemented in PI-1
def is_eligible_for_patch(suggestion, contract, decision, adapter, config) -> bool:
    pi = config.prompt_intervention
    if not pi.enabled:
        return False  # rule (a)
    if not _passes_phase_2_4_eligibility(suggestion, contract, decision, config):
        return False  # rules (b) / (c) / (d) carry-through
    if pi.mode != "ask_clarification" and contract.slots_missing_required:
        return False  # rule (d) — special-cased above
    if (
        "tip.byte-preserved-passthrough" in adapter.capabilities
        and pi.target != "companion_context"
        and not pi.allow_byte_preserve_override
    ):
        return False  # rule (e)
    if not _workspace_policy_allows_mutation(config):
        return False  # rule (f)
    if decision.decision_reason not in EXPLAINABLE_REASONS:
        return False  # rule (g)
    # Rule (h) is enforced at template time inside _build_patch_text().
    return True
```

---

## 8. Config model

Extends the Phase 2.4.3 schema with a new `prompt_intervention` block.

```yaml
intent_policy:
  # (existing fields from Phase 2.4.3 — mode / dry_run / etc — unchanged)
  mode: observe_only

  # NEW in PI: prompt-intervention block. Default-off.
  prompt_intervention:
    # Master kill-switch. False by default; required to be true
    # for any patch to be constructed.
    enabled: false

    # Mode the host wants the builder to use. Defaults to the
    # safest available — preview_only — even when enabled.
    mode: preview_only          # preview_only | inject_guidance | ask_clarification | rewrite_prompt

    # Where the guidance lands. Default companion_context per §6
    # — the companion-first strategy.
    target: companion_context   # companion_context | system | user_message

    # Reuses the Phase 2.5 confirmation handshake. Default true:
    # an applied patch must pass through Phase 2.5's confirmation
    # gate. Setting this false skips the gate (still requires
    # explicit prompt_intervention.enabled = true plus an explicit
    # mode != preview_only).
    require_confirmation: true

    # SAFETY OVERRIDE. Default false: byte-preserved routes are
    # off-limits regardless of mode. Setting this true is the only
    # way a Phase 2.4 / 2.5 / PI patch reaches an Anthropic-OAuth
    # path. The loader emits an audible warning when this flag is
    # set and the host has not also passed an explicit
    # acknowledgement flag (TBD — likely a per-phase ratification
    # marker file under ~/.tokenpak/).
    allow_byte_preserve_override: false

    # Per-surface visibility flags. Default: companion only.
    surfaces:
      claude_code_companion: true
      proxy: false
```

### 8.1 Config defaults

| Field | Default | Notes |
|---|---|---|
| `prompt_intervention.enabled` | `false` | Master switch |
| `prompt_intervention.mode` | `preview_only` | Safest default even when enabled |
| `prompt_intervention.target` | `companion_context` | Companion-first strategy |
| `prompt_intervention.require_confirmation` | `true` | Phase 2.5 gate |
| `prompt_intervention.allow_byte_preserve_override` | `false` | Hard safety; default-off |
| `prompt_intervention.surfaces.claude_code_companion` | `true` | Default companion surface (when intervention enabled) |
| `prompt_intervention.surfaces.proxy` | `false` | Default proxy off |

### 8.2 Forced-safe overrides (loader)

The Phase 2.4.3 loader's three force-applied invariants (dry_run / allow_auto_routing / response_headers) all stay enforced. PI adds two more:

1. `prompt_intervention.allow_byte_preserve_override` clamps to `false` until a separate ratification flag is set (the exact mechanism — TBD per PI-1).
2. `prompt_intervention.target = user_message` is rejected by the loader through PI-4 (and reserved for a future ratification past PI-4).

Both emit warnings via `tokenpak intent config --validate`.

### 8.3 Mode interaction matrix

| `mode` | `prompt_intervention.enabled` | Behavior |
|---|---|---|
| (any) | `false` | No patches built. Default state. |
| `observe_only` (parent) | `true` | Patches built, surfaced via doctor / explain / report; `applied` always `false`. |
| `suggest` (parent) | `true` | Same as observe_only PLUS suggest-mode badging on the patch (mirrors Phase 2.4.3 badging behavior). |
| `confirm` (parent, reserved) | (any) | Phase 2.5 confirmation handshake gates patch application via the Phase 2.5 path. |
| `enforce` (parent, reserved for 2.6) | (any) | Out of scope; loader still rejects. |

---

## 9. Safety / privacy

The Phase 0 / 2.4 / 2.5 contracts carry through unchanged:

- **No raw prompt storage anywhere.** `original_hash` is sha256 hex; the prompt itself stays in the per-request log per Architecture §7.1. Tested with the sentinel-substring pattern across `intent_patches` table + every render path.
- **Hash original prompt only.** Never the prompt body, never tokenized form, never excerpts.
- **`patch_text` only stores the inserted string.** Templated; no caller-supplied substring can reach this field. Bounded to ≤ 1024 chars.
- **`patch_text` MUST NOT include any user secrets.** The same guardrail regex set used by `tokenpak/scaffold/_guardrails.py` extends to scan `patch_text` at build time. A hit raises `PatchPrivacyError` (a subclass of `SuggestionWordingError`) and the patch isn't built.
- **No hidden mutation.** Every patch — regardless of mode — is recorded in `intent_patches` with a `applied` flag that doctor / explain / report surface. A patch that flips `applied = true` lands in the explain output as "Applied for this one request" (scoped present tense; never the unscoped "applied").
- **Visible everywhere.** `tokenpak doctor --explain-last` shows the linked patch (if any). `tokenpak intent report` aggregates patch counts per mode / target / applied. `/api/intent/policy-report` carries a `patches` section. The dashboard's Intent Policy panel shows a "Pending patches" sub-section.

### 9.1 Cross-references

- Architecture §5.1 — byte-fidelity rule (driver of eligibility rule (e)).
- Architecture §7.1 — prompt-locality rule.
- Standard #23 §4.3 — capability gate (still load-bearing for any wire-side header a future phase adds; PI-x does not introduce new wire surfaces).
- Standard #23 §6.4 — `live_verified` semantics (carries through via the underlying suggestion).
- Phase 2.4.1 forbidden-wording guardrail.
- Phase 2.5 §6 lifecycle invariants (driver of `require_confirmation`).

---

## 10. Rollout

Each sub-phase is its own PR with its own acceptance criteria. **PI-0 (this spec) is no-code.** Subsequent sub-phases land sequentially.

| Sub-phase | Deliverable | New runtime behavior | Default off? |
|---|---|---|---|
| **PI-0** | This spec; ratification | None | n/a |
| **PI-1** | `tokenpak/proxy/intent_prompt_patch.py` — pure-function `PromptPatch` builder + `intent_patches` SQLite table + eligibility gates §7. **No surface changes; no application; no companion integration.** | One new SQLite table; nothing visible. | yes |
| **PI-2** | Surfaces wired (`tokenpak doctor --explain-last` adds a "Linked patches" block; `tokenpak intent report` adds patch summary; `tokenpak intent policy-preview` shows linked patches; `/api/intent/policy-report` adds a `patches` section; dashboard Intent Policy panel adds a "Patches" sub-section). All surfaces honor §7 wording rules and label patches as advisory + `applied = false`. **No application.** | Surfaces show empty patch sections; nothing actionable. | yes |
| **PI-3** | Opt-in **guidance injection** for Claude Code Companion. Companion-side hook (`tokenpak/companion/intent_guidance.py`) reads applicable patches and injects them into `companion_context` when `prompt_intervention.enabled = true` AND `mode = inject_guidance` AND the Phase 2.5 confirmation passes. **`target = companion_context` only.** | Companion injects labeled guidance into its own context block on opt-in. Original user message untouched. | yes |
| **PI-4** | Narrow **rewrite mode** (`mode = rewrite_prompt`, `target = companion_context` only). Companion presents both the original and the rewritten prompt-equivalent; user/operator chooses via the Phase 2.5 confirmation gesture. **Even with approval, the original is the default; the rewrite is opt-in per gesture.** No `target = system` rewrites in PI-4 — system-target stays guidance-only. | Optional companion-side rewrite path on opt-in. | yes |

### 10.1 Out of scope for the entire PI line

- Proxy-side mutation. `surfaces.proxy = false` is the default; flipping it true is a future-ratification gate beyond PI-4.
- `target = user_message` mutation. Reserved indefinitely.
- `mode = rewrite_prompt` for `target = system`. Reserved past PI-4.
- Cross-request patch inheritance. Each patch maps to one `contract_id`.
- Bulk approval for patches. Each patch requires its own Phase 2.5 confirmation gesture.
- Wire-side `X-TokenPak-Patch-*` headers. PI is a body-content concern.

---

## 11. Acceptance

### What this spec accepting means

- The 10-section design above is ratified as the working contract for PI-0 → PI-4.
- The §7 eligibility rules and §9 privacy contracts are normative and become part of the implementer's reference. Standards uplift (a possible `26-intent-prompt-intervention.md` standards entry in the vault) is a separate ratification step.
- The §10 sub-phase plan governs sequencing. No PI-x PR may ship without an updated sub-phase entry checking off the deliverable.
- PI-1 (the first code-shipping sub-phase) MAY proceed once this spec is ratified.

### What this spec accepting does NOT mean

- **No runtime behavior changes.** This PR adds one markdown file. No Python, no JSON, no dashboard, no test, no proxy code, no CLI command, no companion code.
- **No classifier behavior changes.** `tokenpak/proxy/intent_classifier.py` remains untouched.
- **No routing behavior changes.** PI-0 has zero effect on dispatch, forward headers, response bodies, or provider/model resolution.
- **No production request mutation.** PI is bounded to companion_context (PI-3 / PI-4) and a future system-target case (PI-4 only); user_message mutation is reserved indefinitely.
- **No TIP wire header emission.** PI-x does not introduce new wire-side headers.
- **No byte-preserved passthrough behavior change.** The `allow_byte_preserve_override` flag stays force-locked off; flipping it requires a separate per-phase ratification beyond this spec.

### Clear rollout path

```
[PI-0 spec — ratify]                     ←  this PR
    ↓
[PI-1 PromptPatch + builder]             ←  needs Kevin go-ahead
    ↓
[PI-2 surfaces wired (no-op default)]
    ↓
[PI-3 opt-in companion guidance injection]   ←  first PR with mutation, companion-side
    ↓
[PI-4 narrow rewrite mode (companion only)]  ←  first PR with rewrite-mode, gated
    ↓
(future ratification — proxy-side / system-target / etc)
```

Each step gated on the previous step's acceptance. Each step's default state is **off**. Each step's tests cover §7 / §9 invariants applicable to that step. The path can stop or pause at any point without leaving the system in an unsafe state — every sub-phase's exit criterion is "still observation-only at runtime if config wasn't flipped on, and bounded to companion_context / target = companion_context when it is".

---

## 12. References

### Intent Advisory MVP (already merged, foundation)

- PR #44 (Phase 0): `feat(proxy): Phase I0-1 — Intent Layer Phase 0 (telemetry-only)` — merge `57af1f18`
- PR #45 (Phase 0.1): doctor `--intent` / `--explain-last` — merge `3c88f2b6`
- PR #46 (Phase 1): `tokenpak intent report` — merge `d6537f02`
- PR #47 (Phase 1.1): dashboard / read-model — merge `e7d7d2e1`
- PR #48 (Phase 2 spec): unified policy engine design — merge `38daf997`
- PR #49 (Phase 2.1): dry-run policy engine — merge `0da0eed6`
- PR #50 (Phase 2.2): policy explain / report / dashboard preview — merge `353db9ec`
- PR #51 (Phase 2.4 spec): suggest mode design — merge `fdaac3a1`
- PR #52 (Phase 2.4.1): PolicySuggestion builder — merge `22719e6c`
- PR #53 (Phase 2.4.2): suggestion display surfaces — merge `1d7c313b`
- PR #54 (Phase 2.4.3): opt-in suggest mode config — merge `2eca2ed2`
- PR #55 (Phase 2.5 spec): confirmation mode design — merge `2702eb79`
- PR #56 (milestone closeout): Intent Advisory MVP — merge `7bf9c296`

### Foundation specs

- `~/vault/02_COMMAND_CENTER/proposals/2026-04-24-tokenpak-intent-layer-phase-0.md` — origin proposal.
- `~/vault/02_COMMAND_CENTER/tokenpak-standards-internal/23-provider-adapter-standard.md` — capability gate (§4.3), `live_verified` semantics (§6.4).
- `docs/internal/specs/phase2-intent-policy-engine-spec-2026-04-25.md` — parent Phase 2 design.
- `docs/internal/specs/phase2.4-suggest-mode-spec-2026-04-26.md` — Phase 2.4 sub-spec.
- `docs/internal/specs/phase2.5-confirm-mode-spec-2026-04-26.md` — Phase 2.5 sub-spec (PI uses Phase 2.5's confirmation handshake for application).
- `docs/internal/milestones/intent-advisory-mvp-2026-04-26.md` — milestone closeout (PI's foundation).
- This document — `docs/internal/specs/intent-prompt-intervention-spec-2026-04-26.md` — PI-0 design (internal).

### Standards (vault, internal)

- `00-product-constitution.md §13` — TokenPak as TIP-1.0 reference implementation.
- `01-architecture-standard.md §5.1` — byte-fidelity rule (load-bearing for §7 rule (e)).
- `01-architecture-standard.md §7.1` — prompt-locality rule (driver of §9 privacy).
- `10` (telemetry-store schema compatibility) — `§E1` migration rule, applicable to PI-1's new `intent_patches` table.
- `21 §9.8` — process-enforced gating (CI).
- `23-provider-adapter-standard.md §4.3` — capability gate.
- `23-provider-adapter-standard.md §6.4` — `live_verified` semantics.

---

## Appendix A — open design questions deferred to sub-phases

| Question | Sub-phase to resolve |
|---|---|
| Exact mechanism for `allow_byte_preserve_override` ratification (per-phase marker file vs explicit signed config)? | PI-1 (likely a `~/.tokenpak/PROMPT_INTERVENTION_BYTE_PRESERVE_OVERRIDE_RATIFIED` marker file with a Kevin-signed acknowledgement; final design lands in PI-1) |
| Should `ask_clarification` surface clarification questions in the doctor / explain output, or only on the companion / dashboard? | PI-2 (likely both — the doctor would show the question text, but the actionable surface is the companion / dashboard) |
| Does `mode = rewrite_prompt` require its own confidence threshold (above the parent `low_confidence_threshold`)? | PI-4 (probably yes — rewrite is the most invasive mode and warrants a higher floor, e.g. ≥ 0.85) |
| For multi-turn conversations, does PI patch only the latest turn or the whole context? | PI-3 (likely latest turn only — each `contract_id` maps to one classification, and one classification matches one turn) |
| Should the companion preview surface a side-by-side diff of `(original, patched)` for `rewrite_prompt`? | PI-4 (yes; this is the operator's main scan target for rewrites) |
| How does PI interact with Phase 2.6 budget enforcement? An `inject_guidance` patch is small, but a `rewrite_prompt` could be longer than the original. | PI-4 / Phase 2.6 (need cost-impact estimation in PI-4; the Phase 2.6 enforce path then has the right inputs) |

---

## Appendix B — what changes if Phase 0 baseline data invalidates an assumption

PI inherits Phase 0's baseline-report dependency from the Phase 2.4 / 2.5 chain:

- **Catch-all dominance > 50 %** → most decisions never become suggestions, so most never become patches either. PI stands; the operator-facing impact is "you opted into PI and aren't seeing many patches" — the correct, safe behavior.
- **Confidence histogram concentrated below 0.65** → most eligible suggestions get blocked at rule (b). Same outcome.
- **Per-class slot-fill rates very low** → `ask_clarification` mode becomes the dominant patch type. Operator-facing impact: the companion surfaces clarification questions instead of routing recommendations. This is exactly the right pattern — the system asks for the missing information rather than guessing.
- **Operator complaint: companion-side latency from patch evaluation** → PI's read path (companion → API or local SQLite) is bounded; if it adds noticeable latency, the response is to cache patches per-contract for the request lifetime, not to soften eligibility. PI-3 will add the cache.

PI explicitly does NOT depend on the baseline being in. The spec stands as a design contract; sub-phase tuning happens against real data.
