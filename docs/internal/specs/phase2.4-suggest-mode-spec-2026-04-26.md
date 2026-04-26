# Phase 2.4 — Opt-in Suggest Mode (design / spec only)

**Date**: 2026-04-26
**Status**: design draft, awaiting Kevin ratification
**Authors**: Sue (design) / Kevin (review)
**Parent spec**: `docs/internal/specs/phase2-intent-policy-engine-spec-2026-04-25.md` (the unified Phase 2 design). This sub-spec elaborates §8 sub-phase 2.4 of the parent.
**Builds on (already merged)**:
  - PR #44 (Phase 0): intent classifier + contract telemetry (`57af1f18`)
  - PR #45 (Phase 0.1): doctor `--intent` / `--explain-last` (`3c88f2b6`)
  - PR #46 (Phase 1): `tokenpak intent report` (`d6537f02`)
  - PR #47 (Phase 1.1): dashboard / read-model (`e7d7d2e1`)
  - PR #48 (Phase 2 spec): unified policy engine design (`38daf997`)
  - PR #49 (Phase 2.1): dry-run policy engine (`0da0eed6`)
  - PR #50 (Phase 2.2): policy explain / report / dashboard preview (`353db9ec`)
**Implements** (when ratified, in 2.4.1+ sub-sub-phases): nothing yet — Phase 2.4 (this PR) is **spec only**.

---

## 0. Reading guide

Structured 1:1 against the Phase 2.4 directive's eleven required sections.

| § | Question |
|---|---|
| 1 | What gap in 2.1/2.2 does suggest mode close? |
| 2 | What is explicitly NOT being designed? |
| 3 | How is suggest mode configured? |
| 4 | When is a suggestion eligible to be surfaced? |
| 5 | What kinds of suggestions exist? |
| 6 | What does a `PolicySuggestion` look like on the wire? |
| 7 | Where do suggestions appear? |
| 8 | What language rules govern operator-facing wording? |
| 9 | What stays private even in suggest mode? |
| 10 | How does this roll out from spec → display → opt-in? |
| 11 | How is each property tested? |

Sections 1, 2, 4, 8, 9, and the acceptance block in §12 are normative; the remainder is design.

---

## 1. Problem statement

Phases 2.1 and 2.2 (PRs #49 and #50) shipped the **dry-run policy engine** end-to-end. Today, every classified request flowing through the proxy:

1. Generates an `IntentContract` (Phase 0).
2. Is evaluated by the dry-run engine, which emits a `PolicyDecision` (`observe_only` / `warn_only` / `suggest_*`) per request.
3. Has its decision recorded in `intent_policy_decisions` and surfaced through three explainability channels: `tokenpak doctor --explain-last` (linked by `contract_id`), `tokenpak intent report` (with a "Policy summary" section), and the dashboard "Intent Policy" panel + `GET /api/intent/policy-report?window=14d` (clearly labeled DRY-RUN / PREVIEW ONLY).

Decisions exist; they are visible to the operator who looks; they have **zero effect on the request**. That is the correct posture for the observation phase.

The gap Phase 2.4 closes is the bridge from *visible to operator who looks* to *visible to the user as a recommendation*. A "policy decision" recorded in a SQLite table and shown only on a debug page is not the same thing as a recommendation surfaced at the moment the request happens. Operators have already asked: *"if the engine thinks compression='aggressive' is right for this prompt, why isn't that hint in the response or in the dashboard's live feed?"*

Phase 2.4 answers: it can be — once the operator opts in, with explicit eligibility rules, explicit UX wording rules, and a clean upgrade path to 2.5 (confirmation) and 2.6 (limited enforcement, budget caps only).

The bridge is intentionally narrow:

- **Suggestions are recommendations, not actions.** Until 2.5/2.6, no suggestion ever causes a request mutation.
- **Suggestions are opt-in.** A host that has not flipped `intent_policy.mode = suggest` sees zero new behavior.
- **Suggestions are eligible only when the safety rules from §6 of the parent spec are satisfied.** The §4 eligibility filter in this spec is a strict superset of the engine's own safety rules.
- **Suggestions surface through the same explainability channels as 2.2 plus three new wire surfaces.** No hidden side-doors.

---

## 2. Non-goals

The following are explicitly NOT being designed in Phase 2.4. Each is a statement of *what this spec does not authorize*, not a permanent exclusion:

| Non-goal | Why it's out of scope |
|---|---|
| **No automatic provider/model switching** | Suggestions surface a `recommended_provider` and `recommended_model`. The dispatcher does NOT read those fields. The only thing that changes when a suggestion is emitted is what the operator sees on the doctor / report / dashboard / API surfaces. Auto-routing is reserved for Phase 2.6+ behind a separate ratification. |
| **No request mutation** | Body bytes, header set, target URL, dispatch order — all preserved exactly as in Phase 2.2. The §11 test strategy enforces this with a structural test that diffs `fwd_headers` / `body` before vs after the suggestion code path. |
| **No classifier behavior changes** | The Phase 0 rule-based classifier is the substrate. Phase 2.4 reads its output; it does not re-run, re-tune, or re-weight any classifier path. `intent_classifier.py` MUST NOT be edited in any 2.4.x sub-phase. |
| **No enforcement** | "Suggest mode" never blocks, never downgrades, never reroutes. The engine's existing `block_or_fail_closed` action is reserved for the parent spec §3 hard-failure path; Phase 2.4 does not introduce a new blocking action. |
| **No hidden behavior changes** | A user who has not enabled `intent_policy.mode = suggest` is identical to a user on Phase 2.2 in every observable way. The opt-in is a config flag; flipping it on is the *only* way to see suggestions. |
| **No prompt / content logging** | The privacy contract from Phase 0 (Architecture §7.1) flows through unchanged. Suggestions are derived from the structured `PolicyDecision` + the `IntentContract` field set; the `message` body is built from a templated string (per §6 below) and never includes raw prompt content. |
| **No confirm mode behavior** | `requires_confirmation` is a field on `PolicySuggestion` (§6) so the shape is forward-compatible with 2.5. In 2.4, the field is always `false`; the synchronous pause point is 2.5's responsibility. |
| **No enforce mode behavior** | Even budget caps (the most narrow enforce target per the parent spec §8 sub-phase 2.6) are not in 2.4. `flag_budget_risk` decisions become `budget_warning` suggestions — operator-visible warnings, never blocks. |

Tested invariantly in §11 via the no-runtime-mutation pattern from `tests/test_intent_layer_phase01_invariant.py` and the privacy-sentinel pattern from `tests/test_intent_policy_phase22.py::TestPrivacyContract`.

---

## 3. Suggest mode config

Config surface lives in `~/.tokenpak/policy.yaml` (the file the parent spec §7 introduced; Phase 2.4.3 wires the loader). The Phase 2.4 schema:

```yaml
intent_policy:
  # Engine mode. Phase 2.4 introduces "suggest" as a new value.
  # Older values stay valid; "confirm" and "enforce" are reserved
  # for 2.5 and 2.6 respectively.
  mode: observe_only        # one of: observe_only | suggest | confirm | enforce

  # Always true through Phase 2.6. The dry-run flag becomes false
  # only when a future phase explicitly authorizes it.
  dry_run: true

  # Phase 2 spec safety flag — still off by default in 2.4. When
  # FALSE, the engine still emits decisions but suggest_route /
  # provider/model recommendations are eligibility-blocked per §4
  # rule (a). When TRUE, those suggestions become eligible (subject
  # to all other §4 rules). Independent of `mode`: a host can run
  # mode=suggest with allow_auto_routing=false to see only
  # compression / cache / delivery / budget suggestions.
  allow_auto_routing: false

  # Same semantics as 2.2: relax the unverified-provider safety rule
  # for explicitly-trusted dev hosts.
  allow_unverified_providers: false

  # NEW in 2.4. Master kill-switch for the entire suggest pipeline.
  # When false, the engine still records decisions to telemetry
  # but emits NO PolicySuggestion objects. This is the safe default
  # for hosts that want the dry-run engine running but the suggest
  # surfaces silent.
  show_suggestions: true

  # NEW in 2.4. Per-surface visibility. A host can opt in to CLI
  # suggestions while keeping the dashboard panel suggestion-free,
  # or vice versa. The response_headers default is FALSE — wire-
  # side header emission stays gated by the existing Standard #23
  # §4.3 capability check on top of this flag (both must be true).
  suggestion_surface:
    cli: true
    dashboard: true
    api: true
    response_headers: false
```

### Mode interaction matrix

| `mode` | `show_suggestions` | Behavior |
|---|---|---|
| `observe_only` | (any) | Engine records decisions; emits no suggestions; identical to Phase 2.2. |
| `suggest` | `false` | Engine records decisions + still no suggestions (operator wants the dry-run telemetry but not the visible surfaces). |
| `suggest` | `true` | Engine records decisions; eligible decisions produce `PolicySuggestion` objects; surfaces enabled in `suggestion_surface` render them. |
| `confirm` | (any) | **Reserved for 2.5.** Validation in 2.4.3 rejects `mode = confirm` with a clear "not yet implemented" error. |
| `enforce` | (any) | **Reserved for 2.6.** Same validation pattern as `confirm`. |

The matrix is enforced by `tokenpak doctor --policy-config` (the Phase 2.2 validator extended in 2.4.3).

### Per-surface visibility rules

- `cli: true` enables suggestion rendering in `tokenpak doctor --explain-last`, `tokenpak intent report`, and `tokenpak intent policy-preview`.
- `dashboard: true` enables the "Suggestions" sub-section under the Intent Policy panel.
- `api: true` enables suggestions to appear in `GET /api/intent/policy-report` payloads (under a new `suggestions` key — schema bumped to `intent-policy-dashboard-v2`; v1 consumers see the absence of the key, no breakage).
- `response_headers: false` is the **default**. Even with `mode = suggest`, suggestions do NOT surface as `X-TokenPak-Suggestion-*` wire headers unless the host explicitly opts in AND the resolved request adapter declares `tip.intent.contract-headers-v1` per Standard #23 §4.3. Both conditions must hold.

---

## 4. Suggestion eligibility rules

A `PolicyDecision` becomes a `PolicySuggestion` only when **all** of the following hold. Each rule maps to a §6 safety rule from the parent spec, plus three new gates specific to suggest mode.

| # | Rule | Always on? | Maps to |
|---|---|---|---|
| (a) | Confidence above threshold (`confidence >= low_confidence_threshold`, default `0.65`) | yes | parent §6 rule 1 |
| (b) | No catch-all (`catch_all_reason is None`) | yes | parent §6 rule 2 |
| (c) | No required slot in `slots_missing` | yes | parent §6 rule 3 |
| (d) | Provider's `live_verified` is `True`, OR `allow_unverified_providers=true` | yes | parent §6 rule 4 |
| (e) | Adapter is not in a misconfigured state (e.g. failed to resolve, or declares no capabilities at all) | yes | new in 2.4 — closes a gap not covered by parent §6 |
| (f) | TIP wire-emission only when adapter declares `tip.intent.contract-headers-v1` per Standard #23 §4.3 | yes | parent §6 rule 5 — same gate, applied to the new `X-TokenPak-Suggestion-*` headers |
| (g) | `decision_reason` is in the explainable taxonomy (`default_observe_only` / `class_rule_matched` / `dry_run_suggest`); a future reason that hasn't been added to the taxonomy MUST suppress the suggestion | yes | parent §6 rule 6 |

A suggestion that fails any rule is **not generated**. Telemetry still records the decision (via Phase 2.1's `intent_policy_decisions` row); only the `PolicySuggestion` shape is suppressed.

### Eligibility evaluation order

```python
# Pseudocode — implemented in Phase 2.4.1
def is_eligible(decision, contract, adapter, config) -> bool:
    if not config.show_suggestions:
        return False
    if decision.action == "warn_only":
        # warn_only itself becomes a suggestion of type
        # "missing_slot_improvement" / "adapter_capability" /
        # "budget_warning" — but only when the underlying decision
        # carries safety_flags that map to a constructive type.
        return _warn_to_constructive_type(decision) is not None
    if decision.action == "observe_only":
        return False  # nothing to suggest
    # suggest_* actions reach here. Apply eligibility rules.
    if contract.confidence < config.low_confidence_threshold:
        return False
    if contract.catch_all_reason is not None:
        return False
    if _has_missing_required_slots(contract):
        return False
    if (
        decision.recommended_provider
        and not _provider_live_verified(decision.recommended_provider)
        and not config.allow_unverified_providers
    ):
        return False
    if not adapter or not adapter.capabilities:
        return False  # rule (e)
    if decision.decision_reason not in EXPLAINABLE_REASONS:
        return False  # rule (g)
    return True
```

The rule (e) gap is intentional: even a high-confidence, fully-classified, slot-complete decision MUST NOT produce a suggestion when the adapter has failed to resolve. A missing or empty-capabilities adapter signals "we don't know what this provider can do"; surfacing a suggestion in that state would be misleading.

The rule (g) gap protects against future engine changes that introduce a `decision_reason` not yet in the rendered taxonomy. Suggest mode renders `decision_reason` user-facingly; an unknown reason surfaces as raw machine identifier, which is bad UX and a potential leak vector.

---

## 5. Suggestion types

Seven types. Five map to engine `suggest_*` actions; two are constructive interpretations of `warn_only` decisions.

| Type | Source | Example | Phase 2.4.x |
|---|---|---|---|
| `provider_model_recommendation` | `suggest_route` | "Could route this `summarize` request to `tokenpak-mistral / mistral-large` based on prior latency" | 2.4.2 (gated on `allow_auto_routing=true`) |
| `compression_profile` | `suggest_compression_profile` | "Could apply `aggressive` compression for this `summarize` request" | 2.4.2 |
| `cache_policy` | `suggest_cache_policy` | "Could enable `proxy_managed` cache for adapters declaring `tip.cache.proxy-managed`" | 2.4.2 |
| `delivery_strategy` | `suggest_delivery_policy` | "Could request `non_streaming` delivery for this `summarize` to reduce token bursts" | 2.4.2 |
| `budget_warning` | `flag_budget_risk` (reserved in 2.1; emitted in 2.4.1) | "This request's estimated cost is $0.85 — near the per-request soft cap" | 2.4.2 (warning only; enforcement is 2.6) |
| `missing_slot_improvement` | `warn_only` with `safety_flags = ['missing_slots']` | "Adding a `target` slot to this `debug` request would unlock the safety gate and surface routing suggestions" | 2.4.2 |
| `adapter_capability` | derived from adapter not declaring useful capabilities | "Adapter `MistralAdapter` doesn't declare `tip.cache.proxy-managed`; declaring it would unlock proxy-managed cache suggestions" | 2.4.2 |

The two `warn_only`-derived types are deliberately constructive — they give the operator a path forward instead of a dead-end "we couldn't help with this." Each maps to a single safety flag; multi-flag warnings produce no suggestion (the operator-panel review areas already cover the multi-flag case).

### Type coverage matrix

| Decision action | safety_flags | Eligible suggestion type |
|---|---|---|
| `suggest_route` | `()` | `provider_model_recommendation` |
| `suggest_compression_profile` | `()` | `compression_profile` |
| `suggest_cache_policy` | `()` | `cache_policy` |
| `suggest_delivery_policy` | `()` | `delivery_strategy` |
| `flag_budget_risk` | `()` | `budget_warning` |
| `warn_only` | `("missing_slots",)` | `missing_slot_improvement` |
| `warn_only` | `("low_confidence",)` | (none — would imply we know the right answer) |
| `warn_only` | `("catch_all",)` | (none — same reason) |
| `warn_only` | `("unverified_provider",)` | (none — already a hard signal) |
| `warn_only` | multi-flag | (none — operator panel covers) |
| `observe_only` | `()` | (none — nothing to suggest) |
| (any other) | (any) | (none — eligibility rule (g) suppresses) |

---

## 6. Suggestion object shape

```python
@dataclass(frozen=True)
class PolicySuggestion:
    """Operator-visible recommendation derived from a PolicyDecision.

    All fields are nullable except identity, type, title, message,
    confidence, source. JSON serializer emits explicit null for
    unset fields so consumers can rely on field presence (matches
    Phase 1.1 schema-stability convention).
    """
    # Identity
    suggestion_id:        str          # ULID; sortable; per-suggestion
    decision_id:          str          # links to intent_policy_decisions row
    contract_id:          str          # links to intent_events row

    # What it is
    suggestion_type:      str          # one of the seven §5 types
    title:                str          # short label (≤ 60 chars; templated)
    message:              str          # one-paragraph description (templated)
    recommended_action:   Optional[str] # short imperative ("Apply aggressive compression"); null when not actionable

    # Provenance
    confidence:           float        # echoes contract.confidence; ∈ [0.0, 1.0]
    safety_flags:         tuple[str, ...] # echoes decision.safety_flags
    source:               str          # always "intent_policy_v0" in 2.4

    # UX gates
    requires_confirmation: bool        # always False in 2.4; reserved for 2.5
    user_visible:          bool        # honors suggestion_surface config
    expires_at:            Optional[str] # ISO-8601; null = persists until next decision
```

### Field rules

- `suggestion_id`: 29-char ms-prefix hex (mirrors `decision_id` shape; same util).
- `decision_id` and `contract_id`: required; the suggestion only exists in the context of a specific decision and its contract.
- `title`: built from a per-type template + the canonical intent label. Examples:
  - `compression_profile` → `"Could apply aggressive compression"`
  - `provider_model_recommendation` → `"Could route to {provider}"` (provider name is a TIP-allowlisted slug; never a free-form string)
  - `budget_warning` → `"Estimated cost $0.85 (near limit)"` (the dollar amount is from telemetry; never a derived metric exposing prompt structure)
- `message`: one paragraph; same template constraint. The message MUST include:
  - The suggestion's *what* (the action being recommended)
  - The suggestion's *why* (the `decision_reason` rendered in human terms)
  - An explicit dry-run / preview disclaimer (until 2.5/2.6 land)
- `recommended_action`: short imperative phrase suitable for a button label or CLI `[y/n]` prompt. `null` for `budget_warning` / `adapter_capability` (pure observations).
- `confidence`: the *contract's* classification confidence. NOT a separate "suggestion confidence." Phase 2.4 chooses to echo the upstream value rather than introduce a second number that could drift.
- `safety_flags`: empty for the five engine-derived types; populated only for the two `warn_only`-derived constructive types.
- `requires_confirmation`: always `false` in 2.4. The field exists for forward-compat with 2.5; `validate_policy_suggestion()` (2.4.1 helper) MUST raise if a suggestion is constructed with `True` while the host is on 2.4.
- `user_visible`: read from `intent_policy.suggestion_surface`. The 2.4.1 renderer reads this field and gates output accordingly.
- `expires_at`: when the suggestion becomes stale. Default `null` (persists until the next decision for the same contract). A future per-type override may set short TTLs (e.g. `budget_warning` expires at end of session); not in 2.4.
- `source`: pinned to `"intent_policy_v0"` for the entire 2.4.x line. Bumps when the engine's `intent_source` bumps (post-baseline).

### Wire shape

JSON serialization of `PolicySuggestion` is the **stable API contract**; consumers may rely on every field's presence (with explicit `null` when unset). Field order is alphabetical for round-trip stability.

```json
{
  "confidence": 0.92,
  "contract_id": "01h0...abc",
  "decision_id": "01h0...def",
  "expires_at": null,
  "message": "Could apply aggressive compression for this summarize request because confidence is high (0.92) and the canonical heuristic table recommends it. Dry-run preview only — no compression applied.",
  "recommended_action": "Apply aggressive compression",
  "requires_confirmation": false,
  "safety_flags": [],
  "source": "intent_policy_v0",
  "suggestion_id": "01h0...xyz",
  "suggestion_type": "compression_profile",
  "title": "Could apply aggressive compression",
  "user_visible": true
}
```

---

## 7. Surfaces

Five surfaces, all sharing one render function (`render_suggestion`) so wording rules from §8 are enforced in one place.

### 7.1 `tokenpak doctor --explain-last`

When suggestion(s) exist for the latest contract, append a `Suggestions:` block under the existing `Linked policy decision:` section. Format mirrors the existing field-list shape:

```
  Linked policy decision (Phase 2.2 dry-run / preview only):
    decision_id:               01h0...def
    mode:                      dry_run
    action:                    suggest_compression_profile
    decision_reason:           dry_run_suggest
    safety_flags:              (none)
    compression_profile:       aggressive
    requires_user_confirmation: False

    DRY-RUN / PREVIEW ONLY — no routing decision was made.

  Suggestions (Phase 2.4 — recommendations only):
    [compression_profile] Could apply aggressive compression
      Confidence: 0.92  |  decision_id: 01h0...def
      Recommended action: Apply aggressive compression
      Why: high-confidence summarize intent matches the
        canonical heuristic table.
      DRY-RUN / PREVIEW ONLY — no compression has been applied.
```

### 7.2 `tokenpak intent report`

Add a "Suggestions snapshot" subsection under the existing "Policy summary" section. Counts only — no per-row content. The full per-row list lives in `policy-preview` (§7.3).

```
  ── Suggestions snapshot (Phase 2.4 — recommendations only) ──

    Total eligible suggestions:  73
    By type:
      compression_profile         42
      provider_model_recommendation 12
      delivery_strategy            10
      budget_warning                5
      missing_slot_improvement      4

    Top recommended actions:
      Apply aggressive compression  42
      Apply non_streaming delivery  10

    DRY-RUN / PREVIEW ONLY. Run `tokenpak intent policy-preview`
    to inspect individual suggestions.
```

### 7.3 `tokenpak intent policy-preview`

Extend the existing latest-decision view with the matching suggestion(s). Decision row first; suggestion(s) below; both labeled clearly.

### 7.4 `GET /api/intent/policy-report`

New top-level `suggestions` key. Schema bump: `intent-policy-dashboard-v1` → `intent-policy-dashboard-v2`. v1 consumers receive a payload without the key; the dashboard JS reads-or-defaults so the absence of the key is graceful. The metadata stays `dry_run_preview_only: true`.

```json
{
  "metadata": { "schema_version": "intent-policy-dashboard-v2", "dry_run_preview_only": true, ... },
  "cards": { ... },
  "operator_panel": { ... },
  "suggestions": {
    "items": [ { /* PolicySuggestion */ }, ... ],
    "count": 73,
    "by_type": {
      "compression_profile": 42,
      "provider_model_recommendation": 12,
      "...": "..."
    }
  }
}
```

### 7.5 Dashboard "Intent Policy" panel

New "Suggestions" subsection at the top of the panel (above the existing tables) so the operator sees recommendations before metrics. Each suggestion card has:

- Type badge (color-coded per the seven types)
- Title (bold)
- Message (one paragraph)
- "Recommended action: …" line (when applicable)
- Confidence bar
- "DRY-RUN / PREVIEW ONLY" footer

The dashboard polls `/api/intent/policy-report?window=14d` (existing); the suggestion list is read from the new `suggestions.items` field. The polling cadence (5 s) is unchanged.

---

## 8. UX rules

These rules are normative. Every render path MUST honor them; the §11 wording tests assert each.

### 8.1 Allowed wording

- "Could ..." (subjunctive — clearly hypothetical)
- "Recommended ..." (clearly advisory)
- "Consider ..." (clearly optional)
- "Suggested ..." (clearly non-binding)
- "Would improve ..." / "Could improve ..." (predictive, not declarative)
- "Eligible for ..." (conditional)

### 8.2 Forbidden wording

The following MUST NOT appear in any suggestion `title`, `message`, or `recommended_action`:

- "Applied ..." (implies the change happened)
- "Changed ..." (same)
- "Routed to ..." (implies routing already occurred)
- "Switched to ..." (same)
- "Now using ..." (same)
- "Updated ..." (implies state change)
- "Will route ..." / "Will switch ..." (implies imminent action without confirmation — wrong even in 2.5/2.6)

The forbidden list is enforced with a regex test in §11. New forbidden phrases require ratification via this spec.

### 8.3 Reason requirement

Every suggestion MUST include a *why* clause in its `message`. The clause format:

```
  ... because <decision_reason rendered in plain English>.
```

The mapping from `decision_reason` → plain English phrase is centralized in `tokenpak/proxy/intent_suggestion_wording.py` (Phase 2.4.1 deliverable):

| `decision_reason` | Plain English |
|---|---|
| `dry_run_suggest` | "the canonical heuristic table recommends it" |
| `class_rule_matched` | "the policy config has a rule for this intent class" |
| `default_observe_only` | (no suggestion would surface; not mapped) |
| `low_confidence_blocked_routing` | (no suggestion would surface; not mapped) |
| (other) | (no suggestion would surface) |

A suggestion with no mappable reason is **not generated** (eligibility rule (g) above).

### 8.4 Dry-run disclaimer

Every render path MUST include the phrase `DRY-RUN / PREVIEW ONLY` in plain text adjacent to the suggestion. The wording test in §11 asserts this string is present in every render output that contains a suggestion. The disclaimer becomes optional only when 2.5/2.6 ratify a different label.

### 8.5 Per-surface tone calibration

Each surface has a slightly different verbosity profile, but all honor §8.1–§8.4:

- CLI explain (§7.1): full verbose render — title + recommended_action + why + disclaimer.
- CLI report (§7.2): aggregate counts only — no per-row text.
- CLI policy-preview (§7.3): full verbose render (mirror of explain).
- API (§7.4): JSON; UX wording rules apply to the field values, not the formatting.
- Dashboard (§7.5): card layout; same wording rules; type badge replaces the `[type]` prefix.

---

## 9. Safety and privacy

Phase 0's privacy contract flows through unchanged:

- **No raw prompt text** anywhere in the suggestion pipeline. The classifier output, the contract, the decision, and the suggestion all read structured fields only. The §11 sentinel-substring test asserts this end-to-end.
- **No secrets** in `title` / `message` / `recommended_action`. The render layer's input is the engine's output; the engine never sees secrets.
- **No full credential fingerprints**. Provider slugs (e.g. `tokenpak-mistral`) appear in `recommended_action`; provider API keys, OAuth tokens, OAuth refresh tokens, account ids never do. The existing `tokenpak/scaffold/_guardrails.py` regex set is reused at suggestion-render time as a **belt-and-suspenders** check; a suggestion that fails the guardrail is dropped (eligibility rule (e) bumps to also catch this).
- **Aggregate / reporting views remain privacy-safe**. The §7.2 "Suggestions snapshot" surfaces counts only; the §7.4 API includes per-row `PolicySuggestion` objects (with the privacy contract above on each row); the §7.5 dashboard renders one card per suggestion (same).
- **Suggestions link back via `decision_id` / `contract_id` only**. No `request_id` echoed beyond what's already in `intent_events` / `intent_policy_decisions`. No token-level identifiers.

### Cross-references

- Architecture §5.1 — byte-fidelity rule (request body unchanged through the suggestion path).
- Architecture §7.1 — prompt-locality rule.
- Standard #23 §4.3 — capability-gated wire emission (still load-bearing for `X-TokenPak-Suggestion-*` headers).
- Standard #23 §6.4 — `live_verified` semantics (driver of eligibility rule (d)).

---

## 10. Rollout plan

Each sub-sub-phase is its own PR with its own acceptance criteria. **Phase 2.4 (this spec) is no-code.** Subsequent sub-sub-phases land sequentially.

| Sub-phase | Deliverable | New runtime behavior | Default off? |
|---|---|---|---|
| **2.4** | This spec; ratification | None | n/a |
| **2.4.1** | `tokenpak/proxy/intent_suggestion.py`: `PolicySuggestion` dataclass + `build_suggestion()` pure function + `intent_suggestion_wording.py` template tables. Engine emits suggestions but writes them only to a new `intent_suggestions` table. **No render path changes.** | One new SQLite table; nothing visible. | yes |
| **2.4.2** | Surfaces wired (CLI explain, CLI report, CLI policy-preview, API, dashboard panel). All five surfaces read the new table; renderers honor §8 wording rules. With `intent_policy.mode = observe_only` (default), surfaces show empty suggestion sections — confirms the hidden-by-default invariant. | Surfaces show empty suggestion sections; nothing populated. | yes |
| **2.4.3** | Config loader for `~/.tokenpak/policy.yaml`. Validates `mode = suggest` with the rest of the schema. Adds `tokenpak doctor --policy-config` validator (extends the Phase 2.2 doctor). With `mode = suggest` + `show_suggestions = true`, eligible suggestions populate the surfaces. | Suggestions appear ONLY when host explicitly opts in. | yes |
| **2.5** | Confirmation mode spec | n/a (spec only) | n/a |
| **2.6** | Limited enforcement spec — budget caps only | n/a (spec only) | n/a |

### Out of scope for the entire 2.4.x line

- Wire-side `X-TokenPak-Suggestion-*` headers default OFF; can be opt-in per host via `suggestion_surface.response_headers = true` AND adapter declares `tip.intent.contract-headers-v1`. The rendered headers are NOT a routing signal — they are observability headers consumed by external tools.
- Provider-side caching of suggestions (cross-request memoization). Each decision produces at most one suggestion; the same prompt classified twice produces two distinct suggestions.

---

## 11. Test strategy

Each Phase 2.4.x PR ships with a regression suite covering the directive's nine test categories. The categories are spec-pinned here so no sub-sub-phase can ship without all nine.

| # | Category | Asserts |
|---|---|---|
| 1 | **Eligible suggestion generated** | High-confidence + safe + heuristic-matched decision → `PolicySuggestion` of the matching type; all required fields populated; `confidence` echoes contract; `requires_confirmation` is False. (2.4.1+) |
| 2 | **Low-confidence no suggestion** | Decision with `confidence < threshold` → no suggestion generated; the underlying `intent_policy_decisions` row is still written (per Phase 2.1). (2.4.1+) |
| 3 | **Catch-all no suggestion** | Decision with `catch_all_reason is not None` → no suggestion generated. (2.4.1+) |
| 4 | **Missing slots no suggestion** | Decision with required slot in `slots_missing` and `safety_flags = ['missing_slots']` → ELIGIBLE for `missing_slot_improvement` constructive type only; no `provider_model_recommendation` / `compression_profile` / etc. suggestions. The constructive variant carries the safety flag. (2.4.1+) |
| 5 | **`live_verified=False` no suggestion by default** | `recommended_provider` carries `live_verified=False` AND `allow_unverified_providers=false` → no suggestion generated. Symmetry check: same input with the flag flipped on emits the suggestion. (2.4.1+) |
| 6 | **Privacy tests** | Sentinel-substring + per-row hash absence asserted across `intent_suggestions` table + every render path (CLI / API / dashboard). The `message` field is built from a template; the test plants a sentinel in the prompt and confirms it appears nowhere in any suggestion field. (2.4.1+) |
| 7 | **Wording tests** | Regex test scans every emitted `title` / `message` / `recommended_action` for forbidden phrases (`Applied`, `Changed`, `Routed to`, `Switched to`, `Now using`, `Updated`, `Will route`, `Will switch`). Also asserts `DRY-RUN / PREVIEW ONLY` is present in every CLI / dashboard render. (2.4.2+) |
| 8 | **No routing mutation** | Structural test: with `mode = suggest` + `show_suggestions = true`, send 100 requests; assert response bytes / fwd_headers are byte-identical to a 100-request baseline run with `mode = observe_only`. The suggestion path is purely additive. (2.4.3+) |
| 9 | **No classifier mutation** | `tokenpak/proxy/intent_classifier.py` files-changed list MUST be empty across every Phase 2.4.x PR. CI gate: `git diff --stat origin/main -- tokenpak/proxy/intent_classifier.py` returns no output. (every 2.4.x PR) |

The cross-phase invariants from `tests/test_intent_layer_phase01_invariant.py` continue to apply; each sub-sub-phase MAY add tests there or in a new file, but the §6 safety rules from the parent spec MUST be enforced by *some* test in *every* sub-sub-phase.

---

## 12. Acceptance

### What this spec accepting means

- The 11-section design above is ratified as the working contract for Phase 2.4.0–2.4.3.
- The §4 eligibility rules and §8 UX rules are normative and become part of the implementer's reference. Standards uplift (a possible `25-intent-suggest-mode.md` standards entry in the vault) is a separate ratification step.
- The §10 sub-sub-phase plan governs sequencing. No 2.4.x PR may ship without an updated sub-sub-phase entry checking off the deliverable.
- Phase 2.4.1 (the first code-shipping sub-sub-phase) MAY proceed once this spec is ratified.

### What this spec accepting does NOT mean

- **No runtime behavior changes.** This PR adds one markdown file. No Python, no JSON, no dashboard, no test, no proxy code, no CLI command.
- **No classifier behavior changes.** `tokenpak/proxy/intent_classifier.py` remains untouched.
- **No routing behavior changes.** Phase 2.4 (this PR) has zero effect on dispatch, forward headers, or response bodies.
- **No provider backlog work.** Bedrock generic, Anthropic-on-Vertex, IBM watsonx, SigV4 / OAuth scaffolding, `--llm-assist`, and additional scaffold-renderer expansion remain held per the standing directive.
- **No ratification of confirm or enforce mode.** `mode = confirm` and `mode = enforce` remain reserved values that the validator rejects with "not yet implemented" through 2.4.3. Their specs are 2.5 and 2.6 deliverables.
- **No ratification of Phase 2.4.1+ designs.** Each sub-sub-phase comes back for explicit approval.

### Clear rollout path

```
[2.4 spec — ratify]                       ←  this PR
    ↓
[2.4.1 PolicySuggestion + builder]        ←  needs Kevin go-ahead
    ↓
[2.4.2 surfaces wired (no-op default)]
    ↓
[2.4.3 config loader + opt-in]            ←  first PR where surfaces show data
    ↓
[2.5 confirmation mode spec]
    ↓
[2.6 limited enforcement (budget caps) spec]
```

Each step gated on the previous step's acceptance. Each step's default state is **off**. Each step's tests cover §11 categories applicable to that step. The path can stop or pause at any point without leaving the system in an unsafe state — every sub-sub-phase's exit criterion is "still observation-only at runtime if config wasn't flipped on".

---

## 13. References

### Phase 0–2.2 (already merged)

- PR #44 (Phase 0): `feat(proxy): Phase I0-1 — Intent Layer Phase 0 (telemetry-only)` — merge `57af1f18`
- PR #45 (Phase 0.1): `feat(cli): Phase 0.1 — Intent Layer doctor view + explain output + docs` — merge `3c88f2b6`
- PR #46 (Phase 1): `feat(cli): Phase 1 — tokenpak intent report (observation-only)` — merge `d6537f02`
- PR #47 (Phase 1.1): `feat(proxy,dashboard): Phase 1.1 — intent dashboard / read-model` — merge `e7d7d2e1`
- PR #48 (Phase 2 spec): `docs(specs): Phase 2 — intent policy engine design / spec only` — merge `38daf997`
- PR #49 (Phase 2.1): `feat(proxy,cli): Phase 2.1 — dry-run intent policy engine` — merge `0da0eed6`
- PR #50 (Phase 2.2): `feat(proxy,cli,dashboard): Phase 2.2 — policy explain/report/dashboard preview` — merge `353db9ec`

### Documents

- `~/vault/02_COMMAND_CENTER/proposals/2026-04-24-tokenpak-intent-layer-phase-0.md` — origin proposal (Adj-1 + Adj-2 banner block).
- `~/vault/02_COMMAND_CENTER/tokenpak-standards-internal/23-provider-adapter-standard.md` — capability gate (§4.3), `live_verified` semantics (§6.4), grandfather clause (§1.5).
- `docs/internal/specs/phase2-intent-policy-engine-spec-2026-04-25.md` — parent spec (this document elaborates §8 sub-phase 2.4).
- `docs/reference/intent-layer-phase-0.md` — Phase 0 fundamentals (operator-facing).
- `docs/reference/intent-reporting.md` — Phase 1 CLI report (operator-facing).
- `docs/reference/intent-dashboard.md` — Phase 1.1 dashboard / API (operator-facing).
- `docs/reference/intent-policy-preview.md` — Phase 2.1 / 2.2 policy preview (operator-facing).
- This document — `docs/internal/specs/phase2.4-suggest-mode-spec-2026-04-26.md` — Phase 2.4 design (internal).

### Standards (vault, internal)

- `00-product-constitution.md §13` — TokenPak as TIP-1.0 reference implementation.
- `01-architecture-standard.md §5.1` — byte-fidelity rule.
- `01-architecture-standard.md §7.1` — prompt-locality rule.
- `10` (telemetry-store schema compatibility) — `§E1` migration rule, applicable to Phase 2.4.1's new `intent_suggestions` table.
- `21 §9.8` — process-enforced gating (CI).
- `23-provider-adapter-standard.md §4.3` — the canonical capability-gated middleware-activation pattern.

---

## Appendix A — open design questions deferred to sub-sub-phases

| Question | Sub-sub-phase to resolve |
|---|---|
| Per-type expiration TTLs (e.g. `budget_warning` expires at session end vs. `provider_model_recommendation` persists until next decision) | 2.4.1 |
| Should suggestions be deduplicated when the same `(intent_class, action, recommended_action)` triple repeats within N minutes? | 2.4.1 |
| Should the dashboard surface a "dismiss" action that hides a suggestion locally without affecting telemetry? | 2.4.2 (likely yes, but the dismiss is local-UI-only and never crosses the API boundary) |
| Per-class wording overrides (e.g. `summarize` vs `debug` get different verbose forms in the message)? | 2.4.2 (template table extension, not a schema change) |
| Should `suggestion_id` be exposed on the wire (response header), or telemetry-only? | 2.4.3 (likely wire-side when the gate is declared, telemetry otherwise — parallels `Contract-Id` from Phase 0) |

---

## Appendix B — what changes if Phase 0 baseline data invalidates an assumption

The assumptions baked into this spec inherit from Phase 0's baseline-report deliverable. If the eventual baseline shows:

- **Catch-all dominance > 50 %** → most decisions never become suggestions because of eligibility rule (b). The spec stands; the operator-facing impact is "you opted into suggest mode and aren't seeing many suggestions" — which is the correct, safe behavior.
- **Confidence histogram concentrated below 0.65** → most eligible decisions get blocked by rule (a). Same outcome as the catch-all case. The threshold is configurable (parent spec §7).
- **Per-class slot-fill rates very low** → the `missing_slot_improvement` constructive type becomes the dominant suggestion. Operator-facing impact: "your suggestions are mostly 'add a target slot to debug requests'" — which is exactly the right pattern, since it directs the operator's attention to the calibration gap rather than to spurious routing recommendations.

Phase 2.4 explicitly does NOT depend on the baseline being in. The spec stands as a design contract; sub-sub-phase tuning happens against real data from 2.4.1+ runs.
