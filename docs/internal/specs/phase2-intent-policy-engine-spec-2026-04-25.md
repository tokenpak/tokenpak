# Phase 2 — Intent Policy Engine (design / spec only)

**Date**: 2026-04-25
**Status**: design draft, awaiting Kevin ratification
**Authors**: Sue (design) / Kevin (review)
**Supersedes** (in scope): Intent-2 / Intent-3 / Intent-4 sub-phases as enumerated in `~/vault/02_COMMAND_CENTER/proposals/2026-04-24-tokenpak-intent-layer-phase-0.md §11`. This spec unifies the design of the bridge from intent observation to controlled action; the proposal's phase enumeration informed the rollout plan in §8 below but the bridge itself is one engine, not three.
**Implements** (when ratified, in later sub-phases): nothing yet — Phase 2.0 is **spec only**.

---

## 0. Reading guide

This document is structured 1:1 against the Phase 2 directive's ten required sections. Each section answers one question:

| § | Question |
|---|---|
| 1 | What problem does Phase 2 solve? |
| 2 | What is explicitly NOT being designed? |
| 3 | What policy actions can the engine output? |
| 4 | What facts does the engine read? |
| 5 | What does the engine return? |
| 6 | What guarantees stay true regardless of config? |
| 7 | How is the engine configured? |
| 8 | How does this roll out from spec → enforce in safe steps? |
| 9 | How is each property tested? |
| 10 | When is the spec accepted, and what does that NOT include? |

Sections 1, 2, 6, and 10 are normative; the remainder is design.

---

## 1. Problem statement

Phase 0 / 0.1 / 1 / 1.1 (PRs #44 → #47, merged 2026-04-25) shipped an end-to-end **observation pipeline**:

- Every request that flows through the proxy is classified upstream into one of the 10 canonical intents declared in `tokenpak.proxy.intent_policy.CANONICAL_INTENTS`.
- A `TIP Intent Contract` is built per request and written to `~/.tokenpak/telemetry.db` `intent_events` (Phase 0).
- The five wire headers (`X-TokenPak-Intent-Class`, `Confidence`, `Subtype`, `Contract-Risk`, `Contract-Id`) attach **only** when the resolved request adapter declares `tip.intent.contract-headers-v1` per Standard #23 §4.3 (no first-party adapter declares this in Phase 0 default).
- Operators inspect the system through `tokenpak doctor --intent` / `--explain-last` (Phase 0.1), the `tokenpak intent report` CLI (Phase 1), and `GET /api/intent/report?window=14d` + the dashboard panel (Phase 1.1).

The pipeline is **purely observational**. The classifier cannot influence routing, model selection, compression, caching, delivery, or budget. Phase 2 designs the **safe bridge** that lets it do those things — but only under explicit operator opt-in, with safety rules that cannot be turned off, and with every decision auditable through the same doctor/report/dashboard surfaces operators are already familiar with.

**The bridge problem is dual.** The downside risk of automatic policy actions is real — wrong intent inference at scale can degrade routing quality, surprise users, or burn budget. The downside risk of *no* policy actions is also real — observation without action means the baseline data only feeds future design conversations, never product behavior. Phase 2 closes that gap with a five-step rollout (§8) and four hard safety rules (§6) that no config can override.

---

## 2. Non-goals

The following are explicitly NOT being designed in Phase 2 and MUST NOT appear in any Phase 2.0–2.6 PR. Each non-goal is a statement of *what this spec does not authorize*, not a claim that the item is permanently out of scope:

| Non-goal | Why it's out of scope for Phase 2 |
|---|---|
| **No classifier behavior changes** | Phase 0's rule-based classifier is the substrate Phase 2 builds on. Changing classifier behavior in the same release as policy actions makes attribution ambiguous: "did routing change because intent inference improved or because the engine changed?" Lock the classifier; release one variable at a time. |
| **No automatic provider switching yet** | The directive enumerates `suggest_route` and `require_confirmation` as policy actions, but **automatic** rerouting (the user's request goes to a different provider without their knowledge) is reserved for a future phase past 2.6. Even 2.6's "limited enforce" only covers budget caps, not provider selection. |
| **No LLM classifier** | The classifier remains `rule_based_v0`. Option B (LLM-assisted classification) is gated on the Phase 0 baseline report; Phase 2 explicitly does not unlock it. The `intent_source` row stays `"rule_based_v0"` through every Phase 2 sub-phase. |
| **No hidden user-facing behavior changes** | Every Phase 2 sub-phase 2.4 onward is **opt-in by config**. A user who hasn't enabled `intent_policy.mode = suggest \| confirm \| enforce` sees zero behavior change vs. Phase 1.1. |
| **No prompt / content logging** | The privacy contract from Phase 0 (Architecture §7.1: prompts stay in the per-request log; cross-request stores see only `raw_prompt_hash`) flows through unchanged. Policy decisions MUST be reproducible from the contract + adapter capabilities + config; raw prompts MUST NOT be persisted to drive a decision. |

These non-goals are tested invariantly under §9 ("test strategy") via the `test_no_behavior_change_default` and privacy-sentinel pattern already established in `tests/test_intent_layer_phase01_invariant.py`.

---

## 3. Intent policy decision model

The engine takes a **policy input bundle** (§4) and returns a **policy decision** (§5) drawn from a fixed enum of nine possible actions. Each action is composable along orthogonal axes; a single decision may carry several actions (e.g. `warn_only` + `choose_compression_profile`).

### Action enum

| Action | Semantics | Active in sub-phase |
|---|---|---|
| `observe_only` | Default. Engine ran, decision recorded in telemetry; **request flows unchanged**. | 2.0 → 2.6 (always available; the safe fallback) |
| `warn_only` | Decision recorded + a `warning_message` returned to the caller (logged via doctor/report; surfaced in `X-TokenPak-Intent-Warning` header IF the adapter declares the gate label). Request flows unchanged. | 2.1+ |
| `suggest_route` | Engine surfaces a `recommended_provider` / `recommended_model`. Caller's request still goes to its declared provider. The suggestion lands in telemetry + the `X-TokenPak-Intent-Suggested-Route` header (gated). **Never reroutes.** | 2.4+ (opt-in) |
| `require_confirmation` | Engine returns `requires_user_confirmation = true` with a `decision_reason`. The CLI / dashboard / SDK surface MUST present this to the user; the request does not proceed until the user confirms. | 2.5+ (opt-in) |
| `enforce_budget_cap` | Engine returns a `budget_action` that the dispatcher MUST honor (e.g. block the request, downsize to a cheaper model, downgrade to compress-then-retry). Limited to budget caps only in 2.6. | 2.6 (opt-in, budget caps only) |
| `choose_compression_profile` | Engine selects a compression profile (e.g. `aggressive`, `conservative`, `none`) for the request. The compression hook reads this. | 2.4+ (opt-in) |
| `choose_cache_policy` | Engine selects a cache policy (`proxy_managed`, `client_observed`, `bypass`) consistent with the resolved adapter's `tip.cache.*` declarations. Never violates Architecture §5.1 byte-fidelity. | 2.4+ (opt-in) |
| `choose_delivery_policy` | Engine selects a delivery profile (e.g. `streaming`, `non_streaming`, `streaming_with_progress`) consistent with the request's declared `stream` field. Never inverts the user's `stream` choice. | 2.4+ (opt-in) |
| `block_or_fail_closed` | **Safety-only.** Engine returns a hard block when an unsafe / misconfigured input would otherwise produce dangerous behavior — e.g. `live_verified=False` provider selected without the explicit allow flag, or budget cap exceeded with no fallback recipe. **Never** triggered by classifier confidence alone. | 2.0+ (always available; no opt-in needed) |

### Composition rules

- A decision MAY carry multiple actions. The serialized form is a list, e.g. `["warn_only", "choose_compression_profile"]`.
- `block_or_fail_closed` is mutually exclusive with every other action: when present, the request fails with `decision_reason` populated and no other action runs.
- `observe_only` is the default when no other action applies; it is implicit when the action list is empty.

### Decision flow (informative)

```
                  ┌──────────────────────┐
   request ─────► │ classify (Phase 0)   │
                  └─────────┬────────────┘
                            │
                            ▼
                  ┌──────────────────────┐
                  │ build policy inputs  │ ← config (§7) + adapter caps + budget
                  └─────────┬────────────┘
                            │
                            ▼
                  ┌──────────────────────┐
                  │ evaluate safety (§6) │ ── any rule trips ─► block_or_fail_closed
                  └─────────┬────────────┘
                            │ all safety rules pass
                            ▼
                  ┌──────────────────────┐
                  │ apply class_rules    │ ── per-class config
                  │ (§7 config model)    │ ── selects 0..n actions
                  └─────────┬────────────┘
                            │
                            ▼
                  ┌──────────────────────┐
                  │ emit PolicyDecision  │ → telemetry + (if gated) wire headers
                  │ (§5 outputs)         │ → caller dispatches per actions
                  └──────────────────────┘
```

The flow is purely additive on top of Phase 0/1/1.1. A request that hits an `intent_policy.mode = observe_only` config (the default) sees the same code path as today; the engine evaluates and records, then returns `observe_only` and the dispatcher proceeds unchanged.

---

## 4. Policy inputs

The engine reads **only** the following inputs. Any future input must be added to this list with a corresponding test under §9. Inputs are bundled into a `PolicyInput` structure passed to the engine; no implicit globals, no hidden state.

| Input | Source | Type |
|---|---|---|
| `intent_class` | `IntentContract.intent_class` (Phase 0) | one of `CANONICAL_INTENTS` |
| `confidence` | `IntentContract.confidence` | `float` ∈ `[0.0, 1.0]` |
| `slots_present` | `IntentContract.slots_present` | `tuple[str, ...]` |
| `slots_missing` | `IntentContract.slots_missing` | `tuple[str, ...]` |
| `catch_all_reason` | `IntentContract.catch_all_reason` | `str \| None` |
| `provider` | resolved by `services.routing_service` | `str` (provider slug) |
| `model` | resolved from request body | `str` |
| `estimated_cost_usd` | derived from `tokens_in` × per-model rate (existing telemetry helper) | `float` |
| `budget_policy` | `intent_policy.budget_caps` config (§7) | `BudgetCaps` dataclass |
| `adapter_capabilities` | `request_adapter.capabilities` | `frozenset[str]` |
| `delivery_target_capabilities` | resolved via the platform-bridge / delivery target | `frozenset[str]` |
| `live_verified_status` | provider's `live_verified` class attribute (Standard #23 §6.4) | `bool` |

Notably absent — and **not allowed** — as inputs:

- Raw prompt text or any prompt-derived value beyond the classifier's structured output.
- Per-request tokens already-spent counters that aren't part of the budget policy abstraction.
- The user's identity beyond the per-host config (TokenPak is single-user per host).

---

## 5. Policy outputs

The engine returns a `PolicyDecision` carrying every field below. Fields are nullable when unused; serializers MUST emit explicit `null` rather than omit. This pins the wire shape so dashboards / explain views / tests can rely on field presence.

```
PolicyDecision
  decision_id:                str         # ULID, sortable
  actions:                    list[str]   # subset of §3 enum (lowercase, sorted)
  recommended_provider:       str | null  # set iff suggest_route or enforce_budget_cap
  recommended_model:          str | null  # set iff suggest_route or enforce_budget_cap
  budget_action:              str | null  # one of: block | downsize | downgrade
  compression_profile:        str | null  # one of: aggressive | conservative | none
  cache_strategy:             str | null  # one of: proxy_managed | client_observed | bypass
  delivery_strategy:          str | null  # one of: streaming | non_streaming | streaming_with_progress
  warning_message:            str | null  # human-readable; never includes prompt content
  requires_user_confirmation: bool        # true iff "require_confirmation" in actions
  decision_reason:            str         # short identifier (e.g. "low_confidence_block")
  decision_id:                see top
```

### Decision reason taxonomy

`decision_reason` is a stable identifier, not free text. The Phase 2 enum:

| `decision_reason` | When emitted |
|---|---|
| `default_observe_only` | No class rule matched; safe fallback |
| `class_rule_matched` | A `class_rules.<intent>` config produced the actions |
| `low_confidence_blocked_routing` | Safety §6 rule 1 prevented suggest/enforce |
| `catch_all_blocked_routing` | Safety §6 rule 2 prevented suggest/enforce |
| `missing_slots_blocked_routing` | Safety §6 rule 3 prevented suggest/enforce |
| `unverified_provider_blocked` | Safety §6 rule 4 prevented suggest/enforce |
| `gate_capability_missing` | Safety §6 rule 5 — adapter doesn't declare the contract-headers label, so no header-side suggestion was emitted |
| `budget_cap_enforce` | A budget cap forced a `block` / `downsize` / `downgrade` |
| `confirmation_required_by_config` | `intent_policy.mode = confirm` and the class rule asked for confirmation |
| `unsafe_input_fail_closed` | A safety rule + a non-recoverable misconfig combined |

### Explainability contract

Every `PolicyDecision` is intended to be rendered through:

- `tokenpak doctor --explain-last` (extended in Phase 2.2 to include policy fields)
- `tokenpak intent report` (extended to break out `decision_reason` distribution + `actions` distribution)
- `GET /api/intent/report` (same payload, dashboard-shaped)
- A new `tokenpak intent policy --dry-run` (Phase 2.1) that runs the engine without executing actions and prints the decision.

If a future change makes a `PolicyDecision` field non-renderable through the above surfaces, that change MUST be rejected. This is the explainability invariant from §6 rule 6.

---

## 6. Safety rules

Six rules are **always on**. No config can disable them. Each rule corresponds to a `decision_reason` from §5 and a test under §9.

### Rule 1 — Low confidence cannot trigger automatic routing

If `confidence < intent_policy.low_confidence_threshold` (default `0.65`, configurable upward only), the engine MUST NOT emit `suggest_route`, `enforce_budget_cap`, `choose_compression_profile`, `choose_cache_policy`, or `choose_delivery_policy`. Allowed: `observe_only`, `warn_only`, `block_or_fail_closed`.

`decision_reason = low_confidence_blocked_routing`.

### Rule 2 — Catch-all cannot trigger automatic routing

If `catch_all_reason is not None` (i.e. classification fell back to `query`), the engine MUST NOT emit any policy action that selects a provider, model, or compression/cache/delivery profile. The catch-all is by definition the "I don't know what this is" landing pad; it is never a basis for inferring a request's needs.

`decision_reason = catch_all_blocked_routing`.

### Rule 3 — Missing required slots cannot trigger automatic routing

If `intent_class` declares one or more required slots and any of those appear in `slots_missing`, the engine MUST NOT emit routing-affecting actions. The required-slots set per intent comes from `tokenpak/agent/compression/slot_definitions.yaml` (existing); the engine reads it but does not modify it.

`decision_reason = missing_slots_blocked_routing`.

### Rule 4 — `live_verified=False` providers cannot be auto-selected unless explicitly allowed

If a candidate `recommended_provider` carries `live_verified = False` (Standard #23 §6.4), the engine MUST NOT propose it unless `intent_policy.allow_unverified_providers = true`. Default is `false`. The flag exists for development hosts where unverified providers are explicitly trusted; it MUST be opt-in per host.

`decision_reason = unverified_provider_blocked`.

### Rule 5 — TIP headers still require adapter capability declaration

The Phase 0 §4.3 gate from Standard #23 stays load-bearing. If the engine emits actions that would normally surface in wire headers (e.g. `suggest_route` → `X-TokenPak-Intent-Suggested-Route`), those headers attach **only** when `tip.intent.contract-headers-v1 in request_adapter.capabilities`. The decision still records to local telemetry. This is the same invariant tested in `tests/test_intent_layer_phase01_invariant.py`.

`decision_reason = gate_capability_missing` (when the action ran but the wire-side suppression applied).

### Rule 6 — All decisions must be explainable through doctor/explain/report/dashboard

Every `PolicyDecision` MUST be reproducible by replaying its inputs. The engine is pure (no I/O during evaluation; reads config + inputs, returns decision). The decision lands in telemetry via the existing `intent_events` row (extended in Phase 2.1 with new columns; schema compatibility per `10 §E1`). Doctor / report / dashboard surfaces the decision. If any decision can't be rendered through these surfaces, the engine change is invalid.

### Rule 7 — No raw prompt storage

The engine MUST NOT cause any new path that persists raw prompt text. Inputs (§4) are all aggregations or structured fields. Outputs (§5) include `warning_message` — that field MUST be a templated string from the engine; it MUST NOT include any substring of the raw prompt. Tests under §9 enforce this with the same sentinel pattern from Phase 1 / 1.1.

---

## 7. Config model

Config lives in `~/.tokenpak/policy.yaml` (new file; merged with existing `~/.tokenpak/config.yaml` via the existing config-pipeline pattern). The shape:

```yaml
intent_policy:
  # Engine mode. observe_only = baseline; suggest = surface recommendations
  # without acting; confirm = pause + ask user; enforce = act (limited to
  # budget caps in Phase 2.6, expandable in later phases).
  mode: observe_only        # one of: observe_only | suggest | confirm | enforce

  # Default action when no class rule matches. observe_only is the safe
  # default; warn_only is the next step up.
  default_action: observe_only

  # Confidence floor below which routing-affecting actions are
  # suppressed (Safety §6 rule 1). Increasing this number is allowed;
  # decreasing it requires the user to also pass --confirm-low-threshold
  # at the CLI to acknowledge the safety implications.
  low_confidence_threshold: 0.65

  # Master kill-switch for routing actions. When false, the engine
  # may still emit warn_only / observe_only / block_or_fail_closed.
  # Required to be true for suggest_route / enforce_budget_cap to
  # activate in any class rule.
  allow_auto_routing: false

  # Permits Safety §6 rule 4 to be relaxed for explicitly-trusted
  # development hosts. Stays false on production fleets.
  allow_unverified_providers: false

  # Budget caps applied by enforce_budget_cap (Phase 2.6). nulls
  # disable the cap.
  budget_caps:
    daily_usd: null         # number | null
    per_request_usd: null   # number | null

  # Per-class rules. Keys are intent classes from CANONICAL_INTENTS.
  # Each value is a small dict declaring the actions the engine MAY
  # take for this class. Unlisted classes inherit `default_action`.
  class_rules:
    # Examples (none active until 2.4+ opt-in):
    coding:        {actions: [observe_only]}
    research:      {actions: [observe_only]}
    summarization: {actions: [observe_only]}
    extraction:    {actions: [observe_only]}
    chat:          {actions: [observe_only]}
    unknown:       {actions: [observe_only]}
```

### Class-name reconciliation

The 10 canonical intents from Phase 0 are `status`, `usage`, `debug`, `summarize`, `plan`, `execute`, `explain`, `search`, `create`, `query`. The directive's example `class_rules` uses higher-level groupings (`coding`, `research`, `summarization`, `extraction`, `chat`, `unknown`). These are **not** the same vocabulary.

Phase 2.0 (this spec) reconciles by:

- Class-rule keys are CANONICAL_INTENTS values, since the classifier emits those.
- A second-level mapping `intent_policy.class_groups` (added in Phase 2.1) maps groupings → intent sets, so an operator can write rules at the higher level without having to enumerate the canonical intents:

```yaml
intent_policy:
  class_groups:
    coding:        [create, debug, execute]
    research:      [search, explain]
    summarization: [summarize]
    extraction:    [search]                # search dominates today; revisit post-baseline
    chat:          [query]                 # catch-all is "chat" in colloquial terms
    unknown:       [query]                 # alias for catch-all
```

The mapping is editable per host. The default mapping is shipped in `tokenpak/proxy/intent_policy_defaults.py` (Phase 2.1 deliverable) and reads from the proposal's measurement-plan output.

### Validation

A new `tokenpak doctor --policy-config` view (Phase 2.2) validates the config against the schema and reports unknown keys, conflicting flags (e.g. `mode = enforce` with `allow_auto_routing = false`), and unverified providers.

---

## 8. Phase rollout plan

Each sub-phase is its own PR with its own acceptance criteria. **Phase 2.0 (this spec) is no-code.** Subsequent sub-phases land sequentially; nothing skips.

| Sub-phase | Deliverable | New runtime behavior | Default off? |
|---|---|---|---|
| **2.0** | This spec; ratification | None | n/a |
| **2.1** | `tokenpak/proxy/intent_policy_engine.py`: pure engine running in **dry-run mode**. Reads inputs (§4), returns `PolicyDecision` (§5), enforces safety rules (§6). Writes a new `intent_policy_decisions` row alongside `intent_events`. **No dispatch effect.** Includes `intent_policy_defaults.py` + `class_groups` defaults. | None visible to users; one new SQLite table. | yes |
| **2.2** | CLI: `tokenpak intent policy --explain` (latest decision); `tokenpak intent report` extended with `decision_reason` distribution + `actions` distribution; `tokenpak doctor --policy-config` validator. | None visible; new explain output. | yes |
| **2.3** | Dashboard: policy preview panel under the existing Intent Layer card section. Reads the new table, surfaces decision-reason / actions / blocked-by-safety counts. Schema bumped to `intent-dashboard-v2`; old consumers see a backward-compatible v1 view via `?schema=v1`. | None visible; one new dashboard panel. | yes |
| **2.4** | Opt-in **suggest mode**. With `intent_policy.mode = suggest` and `allow_auto_routing = true`, the engine emits `suggest_route` / `choose_compression_profile` / `choose_cache_policy` / `choose_delivery_policy`; these surface in `X-TokenPak-Intent-Suggested-*` wire headers (gated by §6 rule 5). **Caller's request still routes to its declared provider.** No automatic rerouting. | New wire headers when adapter declares the gate label. | yes |
| **2.5** | Opt-in **confirm mode**. With `mode = confirm`, decisions carrying `require_confirmation` pause the request; a new SDK / CLI surface presents the decision_reason + recommended_action and waits for user response before dispatching. | New synchronous pause point (only on opt-in classes). | yes |
| **2.6** | Opt-in **limited enforce** for budget caps only. With `mode = enforce` AND `budget_caps.daily_usd` / `per_request_usd` set, the engine MAY block / downsize / downgrade per `enforce_budget_cap`. **No other actions become enforce-mode in 2.6.** | Requests can be blocked or auto-downgraded by budget. | yes |

### Out of scope for the entire Phase 2 rollout

- Automatic provider rerouting beyond the budget-cap downgrades in 2.6.
- LLM-assisted classification (Option B from the proposal). Decisions stay deterministic.
- Per-user / per-team policies. TokenPak remains single-user per host.

A Phase 3 would address those if and when the Phase 2.6 baseline produces clean signal.

---

## 9. Test strategy

Each Phase 2.0–2.6 PR ships with a regression suite class structured 1:1 against the directive's nine test categories. The categories are spec-pinned here so no sub-phase can ship without all nine.

| # | Category | Asserts |
|---|---|---|
| 1 | **Policy engine dry-run tests** | Engine produces a `PolicyDecision` for representative inputs without writing through to any dispatch path. Output round-trips through JSON cleanly. (Phase 2.1+) |
| 2 | **Confidence threshold tests** | Inputs at `confidence < threshold` produce `low_confidence_blocked_routing` and the action list contains only `observe_only` / `warn_only`. (Phase 2.1+) |
| 3 | **Catch-all safety tests** | Inputs where `catch_all_reason is not None` produce `catch_all_blocked_routing` and never include routing-affecting actions. (Phase 2.1+) |
| 4 | **Missing-slot tests** | Inputs where any required slot is in `slots_missing` produce `missing_slots_blocked_routing`. Per-intent slot-required sets read from `slot_definitions.yaml` (no duplicate authority). (Phase 2.1+) |
| 5 | **`live_verified=False` tests** | Engine refuses to recommend an unverified provider when `allow_unverified_providers = false`. Symmetry assertion: same input with the flag flipped on emits the recommendation. (Phase 2.1+) |
| 6 | **Privacy tests** | Sentinel-substring assertion across the new `intent_policy_decisions` table + dashboard / report payloads. The `warning_message` field is built from a template; the test plants a sentinel in the prompt and confirms it appears nowhere in the engine output. (Phase 2.1+) |
| 7 | **Explainability tests** | Every emitted `PolicyDecision` is renderable through `doctor --explain-last` extended view, `intent report` extended view, dashboard payload, and the JSON of the new table. Failure mode: a field that can't be rendered should fail the test, not the production renderer. (Phase 2.2+) |
| 8 | **No-behavior-change-default tests** | With `intent_policy` absent or `mode = observe_only`, the engine runs but produces `actions = []` and `decision_reason = default_observe_only`. The dispatcher sees the same code path as Phase 1.1. End-to-end test: send 100 requests through the proxy with default config; assert 100 responses are byte-identical to the same 100 requests without the engine present. (Phase 2.1+) |
| 9 | **Wire-emission gate symmetry** | The §6 rule 5 invariant — already pinned in `tests/test_intent_layer_phase01_invariant.py` for Phase 0 — is extended to cover the new policy-driven headers (`X-TokenPak-Intent-Suggested-*`). Same gate; same test pattern. (Phase 2.4+) |

The `tests/test_intent_layer_phase01_invariant.py` file remains canonical for the cross-phase invariant. Each Phase 2 sub-phase MAY add tests there or in a new file, but the invariants from §6 of this spec MUST be enforced by *some* test in *every* sub-phase.

---

## 10. Acceptance

### What this spec accepting means

- The 10-section design above is ratified as the working contract for Phase 2.0–2.6.
- The §6 safety rules are normative and become part of `tokenpak/CLAUDE.md` standing rules + (optionally) a new `~/vault/02_COMMAND_CENTER/tokenpak-standards-internal/24-intent-policy-engine.md` standards entry. Standards uplift is a separate ratification step.
- The §8 sub-phase plan governs sequencing. No Phase 2.x PR may ship without an updated sub-phase entry checking off the deliverable.
- Phase 2.1 (the first code-shipping sub-phase) MAY proceed once this spec is ratified.

### What this spec accepting does NOT mean

- **No runtime behavior changes.** This PR adds one markdown file. No Python, no JSON, no dashboard, no test, no proxy code, no CLI command.
- **No classifier behavior changes.** `tokenpak/proxy/intent_classifier.py` remains untouched.
- **No routing changes.** Phase 2.0 has zero effect on dispatch, forward headers, or response bodies.
- **No provider backlog work.** Bedrock generic, Anthropic-on-Vertex, IBM watsonx, SigV4 / OAuth scaffolding, `--llm-assist`, and additional scaffold-renderer expansion remain held per the standing directive.
- **No ratification of Phase 2.1+ designs.** Each sub-phase comes back for explicit approval. This spec is the foundation, not a pre-approval.

### Clear rollout path

```
[2.0 spec — ratify]   ←  this PR
    ↓
[2.1 engine dry-run]  ←  needs Kevin go-ahead
    ↓
[2.2 explain / report extend]
    ↓
[2.3 dashboard preview]
    ↓
[2.4 opt-in suggest mode]   ←  first PR with new wire headers
    ↓
[2.5 opt-in confirm mode]
    ↓
[2.6 opt-in limited enforce (budget caps only)]
```

Each step gated on the previous step's acceptance. Each step's default state is **off**. Each step's tests cover §9 categories applicable to that step. The path can stop or pause at any point without leaving the system in an unsafe state — every sub-phase's exit criterion is "still observation-only at runtime if config wasn't flipped on".

---

## 11. References

### Phase 0–1.1 (already merged)

- PR #44 (Phase 0): `feat(proxy): Phase I0-1 — Intent Layer Phase 0 (telemetry-only)` — merge `57af1f18`
- PR #45 (Phase 0.1): `feat(cli): Phase 0.1 — Intent Layer doctor view + explain output + docs` — merge `3c88f2b6`
- PR #46 (Phase 1): `feat(cli): Phase 1 — tokenpak intent report (observation-only)` — merge `d6537f02`
- PR #47 (Phase 1.1): `feat(proxy,dashboard): Phase 1.1 — intent dashboard / read-model` — merge `e7d7d2e1`

### Documents

- `~/vault/02_COMMAND_CENTER/proposals/2026-04-24-tokenpak-intent-layer-phase-0.md` — origin proposal; Adj-1 + Adj-2 banner block aligned with Standard #23 §4.3.
- `~/vault/02_COMMAND_CENTER/tokenpak-standards-internal/23-provider-adapter-standard.md` — the §4.3 capability gate, the §6.4 `live_verified` semantics, the §1.5 grandfather clause.
- `docs/reference/intent-layer-phase-0.md` — Phase 0 fundamentals (operator-facing).
- `docs/reference/intent-reporting.md` — Phase 1 CLI report (operator-facing).
- `docs/reference/intent-dashboard.md` — Phase 1.1 dashboard / API (operator-facing).
- This document — `docs/internal/specs/phase2-intent-policy-engine-spec-2026-04-25.md` — Phase 2 design (internal).

### Standards (vault, internal)

- `00-product-constitution.md §13` — TokenPak as TIP-1.0 reference implementation.
- `01-architecture-standard.md §5.1` — byte-fidelity rule.
- `01-architecture-standard.md §7.1` — prompt-locality rule.
- `10` (telemetry-store schema compatibility) — `§E1` migration rule, applicable to Phase 2.1's new `intent_policy_decisions` columns.
- `21 §9.8` — process-enforced gating (CI).
- `23-provider-adapter-standard.md §4.3` — the canonical capability-gated middleware-activation pattern this spec builds on.

---

## Appendix A — open design questions deferred to sub-phases

These are intentionally NOT resolved in Phase 2.0. They surface here so the sub-phase designs know to address them.

| Question | Sub-phase to resolve |
|---|---|
| Does the policy engine read its config every request, or cache for N seconds? | 2.1 (with a `policy.cache_ttl_s` field in the config) |
| Should `class_groups` be derivable from the proposal's baseline-report distributions, or operator-edited? | 2.1 (defaults shipped from baseline; user-editable) |
| Should `decision_id` be exposed on the wire, or telemetry-only? | 2.4 (likely wire when the gate is declared, telemetry otherwise — parallels `Contract-Id`) |
| How do `confirm` mode pauses interact with streaming requests? | 2.5 (the SDK / CLI surfaces a synchronous gate before `streaming` opens; non-streaming requests pause inline) |
| What's the budget-cap "downgrade" recipe — model swap, compression bump, or both? | 2.6 (likely composable; ratified in 2.6's spec) |
| Does Phase 2.6 work with Pro tier-only models, or is enforcement blind to tier? | 2.6 (deferred — the OSS / Pro split is a separate canonical decision) |

---

## Appendix B — what changes if Phase 0 baseline data invalidates an assumption

The Phase 0 baseline-report deliverable from the original proposal (§4 deliverable 5) is the input to Phase 2.1's engine defaults. If the baseline shows:

- **Catch-all > 50 %** → keyword table revisit comes BEFORE 2.1 engine work; the engine is useless against a classifier that can't classify.
- **Confidence histogram concentrated below 0.65** → the `low_confidence_threshold` default may need adjustment (down to 0.5? up to 0.75?). Sub-phase 2.1 makes the value configurable; the *default* is the open question.
- **Per-class slot-fill rates very low** → §6 rule 3 will block routing for most classes, defeating the purpose. Either slot definitions need broadening (in a separate PR upstream of 2.1) or the rule needs a "warn_only_when_slots_missing" softening (which would need to be ratified back into §6).

Phase 2.0 explicitly does NOT depend on the baseline being in. The spec stands as a design contract; sub-phase tuning happens against real data.
