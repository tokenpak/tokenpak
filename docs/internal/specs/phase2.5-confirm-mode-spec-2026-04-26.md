# Phase 2.5 — Confirmation Mode (design / spec only)

**Date**: 2026-04-26
**Status**: design draft, awaiting Kevin ratification
**Authors**: Sue (design) / Kevin (review)
**Parent spec**: `docs/internal/specs/phase2-intent-policy-engine-spec-2026-04-25.md` (the unified Phase 2 design). This sub-spec elaborates §8 sub-phase 2.5 of the parent — and supersedes the high-level "confirmation mode" reference there.
**Also references**: `docs/internal/specs/phase2.4-suggest-mode-spec-2026-04-26.md` — the Phase 2.4 sub-spec; this 2.5 spec extends 2.4's eligibility gates and UX rules with confirmation-specific additions.
**Builds on (already merged)**:
  - PR #44 (Phase 0): intent classifier + contract telemetry (`57af1f18`)
  - PR #45 (Phase 0.1): doctor `--intent` / `--explain-last` (`3c88f2b6`)
  - PR #46 (Phase 1): `tokenpak intent report` (`d6537f02`)
  - PR #47 (Phase 1.1): dashboard / read-model (`e7d7d2e1`)
  - PR #48 (Phase 2 spec): unified policy engine design (`38daf997`)
  - PR #49 (Phase 2.1): dry-run policy engine (`0da0eed6`)
  - PR #50 (Phase 2.2): policy explain / report / dashboard preview (`353db9ec`)
  - PR #51 (Phase 2.4 spec): opt-in suggest mode design (`fdaac3a1`)
  - PR #52 (Phase 2.4.1): PolicySuggestion builder + telemetry (`22719e6c`)
  - PR #53 (Phase 2.4.2): suggestion display surfaces (`1d7c313b`)
  - PR #54 (Phase 2.4.3): opt-in suggest mode config (`2eca2ed2`)
**Implements** (when ratified, in 2.5.1+ sub-sub-phases): nothing yet — Phase 2.5 (this PR) is **spec only**.

---

## 0. Reading guide

Structured 1:1 against the Phase 2.5 directive's ten required sections.

| § | Question |
|---|---|
| 1 | What gap in 2.4 does confirmation mode close? |
| 2 | What is explicitly NOT being designed? |
| 3 | How is confirmation mode configured? |
| 4 | What classes of action can a confirmation request cover? |
| 5 | What does a `PolicyConfirmationRequest` look like on the wire? |
| 6 | What must be true for an approval to be valid? |
| 7 | What language rules govern operator-facing wording? |
| 8 | What stays private even in confirm mode? |
| 9 | How does this roll out from spec → low-risk one-time execution? |
| 10 | How is each property tested? |

Sections 1, 2, 6, 7, 8, and the acceptance block in §11 are normative; the remainder is design.

---

## 1. Problem statement

Phase 2.4 (PRs #51 → #54) shipped the **opt-in suggest mode** end-to-end:

- The dry-run engine (Phase 2.1) emits `PolicyDecision` per request.
- The Phase 2.4.1 builder turns eligible decisions into `PolicySuggestion` rows in `intent_suggestions`.
- The Phase 2.4.2 surfaces (CLI explain / report / preview, dashboard panel, API endpoint) render every suggestion clearly labeled "advisory / no-op / default-off; TokenPak has not changed routing".
- The Phase 2.4.3 config loader gates per-surface badging behind explicit `mode = suggest` opt-in via `~/.tokenpak/policy.yaml`.

Today, an operator who sees a suggestion has only one path to act on it: edit code, edit config, restart things by hand. There is no in-band "I want to apply this one suggestion just for the next request" path. That gap is the Phase 2.5 problem.

Phase 2.5 designs the **confirmation contract**: a per-request, per-suggestion approval handshake that lets the operator (or end-user, in client integrations) explicitly say "yes, apply this one-time change" or "no, ignore". The handshake must be:

- **Explicit.** No silent or implicit approval. The user actively says yes.
- **Auditable.** Every approval, rejection, and expiration lands in telemetry (with the same privacy contract as Phase 0/2.1/2.4).
- **Reversible where possible.** A one-time approval applies only to a single request; permanent state changes are out of scope until Phase 2.6+.
- **Default-off.** A host running default `mode = observe_only` (or `mode = suggest`) sees no confirmation surface at all. The opt-in is `mode = confirm`.
- **Bounded.** The directive's "Confirmable action types" §4 enumerates exactly six allowed action classes; six action classes are explicitly excluded. No other actions can be confirmable in 2.5.x.
- **Reversibility-first.** Where a confirmation could trigger something not safely reversible (e.g. permanent provider switching), Phase 2.5 forbids it outright. The list of allowed actions is intentionally narrow.

The bridge from suggest to confirm is the same kind of bridge that 2.4 was for observe-to-suggest: narrow, explicit, opt-in, with hard safety rules that no config can override.

---

## 2. Non-goals

The following are explicitly NOT being designed in Phase 2.5. Each is a statement of *what this spec does not authorize*, not a permanent exclusion.

| Non-goal | Why it's out of scope for Phase 2.5 |
|---|---|
| **No automatic enforcement** | Phase 2.5 is approval-driven by definition. Even when an approval is granted, the action must be one-time and bounded (§4). Permanent / blanket enforcement is Phase 2.6's scope (budget caps only) or later. |
| **No silent provider/model switching** | A confirmation may be granted for `route_once_to_provider_model` (§4 allowed list), but the surface must show *what* it's switching to and *for one request only*. Silent or persistent switching is excluded. |
| **No classifier changes** | The Phase 0 rule-based classifier remains the substrate. `intent_classifier.py` MUST NOT be edited in any 2.5.x PR. |
| **No LLM classifier** | `intent_source` stays `"rule_based_v0"` through every 2.5.x sub-sub-phase. Option B (LLM-assisted classification) remains gated on the Phase 0 baseline report, which is independent of Phase 2.5. |
| **No hidden request mutation** | Until 2.5.4 (one-time low-risk execution) actually lands, the engine never mutates a request. Even after 2.5.4, mutations are bounded to the §4 allow-list and the per-request scope. |
| **No prompt / content logging** | The privacy contract from Phase 0 (Architecture §7.1) flows through unchanged. Confirmation requests carry only structured fields + templated text + IDs / hashes. |
| **No irreversible actions without explicit approval** | Even with explicit approval, irreversible actions are excluded entirely (§4 excluded list — credential changes, destructive config, permanent switching). The approval doesn't unlock them; the spec doesn't make them confirmable. |
| **No confirmation cascades** | A single confirmation cannot grant a chain of approvals. One confirmation = one action = one request. Multi-action approvals are out of scope. |
| **No automatic re-confirmation** | An approved action does not implicitly re-approve future identical requests. Each request needing a confirmable action gets its own confirmation request. |

These non-goals are tested invariantly under §10 via the structural, sentinel-substring, and no-route-mutation patterns established in 2.4.x.

---

## 3. Confirmation mode config

Config schema extends the Phase 2.4 spec §3 schema. New fields are at the bottom; existing fields preserve their semantics.

```yaml
intent_policy:
  # Engine mode. Phase 2.5 introduces "confirm" as a new value.
  # Older values (observe_only / suggest) stay valid; "enforce"
  # is reserved for Phase 2.6.
  mode: observe_only        # one of: observe_only | suggest | confirm | enforce

  # Always true through Phase 2.6. The dry-run flag becomes false
  # only when a future phase explicitly authorizes execution
  # outside the §4 confirmable allow-list. Forced True by the
  # config loader regardless of file value.
  dry_run: true

  # Same semantics as 2.4.3 — forced False until a future phase
  # explicitly authorizes auto-routing.
  allow_auto_routing: false

  # NEW in 2.5. Master switch for the confirmation pipeline.
  # When false, the engine still records suggestions per 2.4.1
  # but emits NO PolicyConfirmationRequest objects. Required to
  # be true for `mode = confirm` to do anything visible.
  require_confirmation: true

  # NEW in 2.5. Per-surface visibility for the confirmation UI.
  # Mirrors the Phase 2.4.3 suggestion_surface block. Each surface
  # gates whether the confirmation UI appears there at all when a
  # confirmation request is pending. response_headers stays
  # locked False — wire-side confirmation prompts are out of
  # scope through 2.5.x.
  confirmation_surface:
    cli: true
    dashboard: true
    api: true
    response_headers: false

  # NEW in 2.5. Time-to-live for a pending confirmation request,
  # in seconds. After expiration, the request transitions to
  # `expired` and cannot trigger any action. Default 300s (5 min)
  # — operators can shorten this; lengthening past a future safety
  # ceiling will be rejected by the loader.
  confirmation_ttl_seconds: 300

  # Same semantics as 2.4.3 — relax the unverified-provider
  # safety rule for explicitly-trusted dev hosts. A
  # confirmation involving an unverified provider is suppressed
  # unless this flag is set.
  allow_unverified_providers: false
```

### Mode interaction matrix

| `mode` | `require_confirmation` | Behavior |
|---|---|---|
| `observe_only` | (any) | Same as 2.4.3 — no confirmations generated. |
| `suggest` | (any) | Same as 2.4.3 — suggestions visible; no confirmations. |
| `confirm` | `false` | Engine still records suggestions; **no** confirmation requests emitted. The host has explicitly turned the confirm pipeline off without leaving suggest mode. |
| `confirm` | `true` | Engine records suggestions + emits `PolicyConfirmationRequest` for each eligible §4 action class. The user/operator is required to approve before any §4 action runs. (Until 2.5.4, "approval" is recorded but no execution occurs — see §9 rollout.) |
| `enforce` | (any) | **Reserved for 2.6.** The config loader rejects `mode = enforce` in 2.5.x with a clear "not yet implemented" error. |

### Safety invariants (force-applied by the loader)

The Phase 2.4.3 loader's three safety overrides extend to 2.5:

1. `dry_run` is forced `True`. (Same as 2.4.3.)
2. `allow_auto_routing` is forced `False`. (Same as 2.4.3.)
3. `confirmation_surface.response_headers` is forced `False`. (NEW in 2.5; same posture as suggestion_surface.response_headers.)

Plus a 2.5-specific safety:

4. `confirmation_ttl_seconds` is clamped to `[15, 3600]` (15 seconds to 1 hour). Values outside this range are clamped with a warning. The lower bound prevents accidental zero-TTL configs that would make every confirmation auto-expire; the upper bound prevents an operator from leaving a confirmation pending for so long that it becomes a stale-state hazard.

---

## 4. Confirmable action types

Six action classes are confirmable in Phase 2.5. Six action classes are **explicitly excluded** from 2.5 confirmability — they remain Phase 2.6+ scope (or never).

### Allowed (the §4 confirmable allow-list)

| Action type | Maps to suggestion type | Scope when approved |
|---|---|---|
| `route_once_to_provider_model` | `provider_model_recommendation` | One-time per-request override of the resolved provider/model |
| `apply_compression_profile_once` | `compression_profile_recommendation` | One-time application of the suggested compression profile to this request |
| `apply_cache_policy_once` | `cache_policy_recommendation` | One-time per-request cache-strategy override |
| `apply_delivery_strategy_once` | `delivery_strategy_recommendation` | One-time delivery-profile override (e.g. force `non_streaming`) |
| `set_budget_warning_threshold` | (operator-driven; no underlying suggestion) | Update the host's `intent_policy.budget_caps.per_request_usd` warning threshold (advisory only — actual enforcement is 2.6) |
| `suppress_suggestion_temporarily` | (any suggestion, marked dismissed) | Tag the suggestion as "operator-dismissed for the next N minutes" so the same recommendation doesn't surface again immediately. Local-only state; never crosses the API boundary as a write. |

### Explicitly excluded (NOT confirmable in 2.5)

| Action type | Why excluded |
|---|---|
| **Permanent provider switching** | Persistent state change. The host's resolved provider for an intent class would silently change for all future requests. Out of scope per the directive's reversibility rule. |
| **Permanent model switching** | Same reason. |
| **Destructive config changes** | Anything that edits / deletes / overwrites a config file or removes an adapter. The confirmation surface does not have authority to write to disk. |
| **Credential changes** | The dispatch path's credential flow is owned by `services/routing_service/credential_injector.py`. Confirmation is not a permitted ingress into credential state. Phase 0 / Standard #23 §6.4 already pin this. |
| **Enabling unverified providers** | Trying to confirm `allow_unverified_providers = true` via a confirmation request would let a one-time UI flow change a host-wide safety flag. Not allowed; that flag stays config-file-only. |
| **Enabling enforcement mode** | Trying to confirm `mode = enforce` from inside the confirmation surface would let one approval flip the host into Phase 2.6 territory. Not allowed; mode changes stay config-file-only. |

The exclusion list is enforced by the action-type validator at request-build time (Phase 2.5.1's `validate_confirmable_action_type`). An attempt to construct a `PolicyConfirmationRequest` with an excluded action type raises `ConfirmationActionTypeError`.

### Action-type taxonomy invariants

- **One-time by default.** Every action type in the allow-list is bounded to a single request unless explicitly otherwise (none of the six are otherwise in 2.5).
- **Reversible.** None of the six allowed action types persist beyond the request that triggered them. Even `set_budget_warning_threshold` is advisory in 2.5 — actual budget *enforcement* is Phase 2.6.
- **Bounded surface.** Each action type's effect is scoped to fields the proxy already controls (provider/model resolution, compression hook, cache strategy, delivery profile, intent_policy.budget_caps preview). No action type touches credentials, classifier behavior, or adapter capabilities.

---

## 5. Confirmation object shape

```python
@dataclass(frozen=True)
class PolicyConfirmationRequest:
    """One pending confirmation prompt. Operator-visible.

    All fields are nullable except identity, action_type, title,
    message, status, source. JSON serializer emits explicit null
    for unset fields so consumers can rely on field presence
    (matches Phase 1.1 / 2.4.1 schema-stability convention).
    """
    # Identity
    confirmation_id:        str          # ULID; sortable; per-request
    suggestion_id:          str          # links to intent_suggestions row
    decision_id:            str          # links to intent_policy_decisions row
    contract_id:            str          # links to intent_events row

    # What the user is being asked to approve
    action_type:            str          # one of the six §4 allowed types
    proposed_action:        str          # short imperative ("Apply aggressive compression for this one request")
    title:                  str          # short label ≤ 60 chars (templated)
    message:                str          # one-paragraph description (templated)

    # Provenance / risk
    risk_level:             str          # "low" | "medium" | "high" — see §5.1
    safety_flags:           tuple[str, ...]  # echoes the underlying suggestion's flags
    expires_at:             str          # ISO-8601 timestamp; computed at build time from confirmation_ttl_seconds

    # Lifecycle
    requires_user_confirmation: bool     # ALWAYS True — the field exists for forward-compat with the PolicySuggestion shape
    status:                 str          # "pending" | "approved" | "rejected" | "expired" | "canceled"

    # Provenance pinning
    source:                 str          # always "intent_policy_v0" in 2.5
```

### 5.1 Risk-level taxonomy

The directive enumerates `risk_level` as part of the operator-visible fields. The Phase 2.5 mapping:

| `risk_level` | When emitted | Confirmable in 2.5.x |
|---|---|---|
| `low` | `apply_compression_profile_once`, `apply_cache_policy_once`, `apply_delivery_strategy_once`, `suppress_suggestion_temporarily` — all reversible per-request changes | Yes — Phase 2.5.4 will execute these one-time |
| `medium` | `route_once_to_provider_model` — provider override is reversible per-request but has more side-effects (different cost, latency, capability surface) | Yes — but Phase 2.5.4 might exclude these from initial low-risk-only execution; flagged as 2.5.4 review item |
| `high` | `set_budget_warning_threshold` — host-wide setting (even if advisory in 2.5; budget *enforcement* is 2.6) | Yes for the *advisory* threshold update; the *enforcement* implication is gated on 2.6 ratification |

`risk_level` is the operator's primary scan-target on the confirmation surface; UX rules (§7) require it to be visually prominent.

### 5.2 Status state machine (overview; full lifecycle in §6)

```
                ┌──────────┐
                │ pending  │ ← initial state on builder emission
                └────┬─────┘
                     │
        approve ─────┼─────── reject ─────── expire (TTL elapsed)
                     │                       │
                     ▼                       ▼
               ┌──────────┐            ┌──────────┐
               │ approved │            │ expired  │
               └──────────┘            └──────────┘
                     │
                     │ (approval is informational in 2.5.1–2.5.3;
                     │  one-time execution lands in 2.5.4 for the
                     │  low-risk subset only)
                     ▼
               (no execution before 2.5.4)
                          ┌──────────┐
                          │ canceled │ ← operator-driven explicit cancel
                          └──────────┘
```

Transition allowed: `pending → approved | rejected | expired | canceled`. No back-edges. Once a confirmation is in any non-pending state, it is terminal.

### 5.3 Wire shape

JSON serialization of `PolicyConfirmationRequest` is the **stable API contract** for any Phase 2.5.x sub-sub-phase that surfaces it. Field order is alphabetical for round-trip stability:

```json
{
  "action_type": "apply_compression_profile_once",
  "confirmation_id": "01h0...xyz",
  "contract_id": "01h0...abc",
  "decision_id": "01h0...def",
  "expires_at": "2026-04-26T19:05:00",
  "message": "Approve applying aggressive compression for this one summarize request? This has not been applied yet — you can approve or reject below.",
  "proposed_action": "Apply aggressive compression once",
  "requires_user_confirmation": true,
  "risk_level": "low",
  "safety_flags": [],
  "source": "intent_policy_v0",
  "status": "pending",
  "suggestion_id": "01h0...ghi",
  "title": "Approve aggressive compression?"
}
```

---

## 6. Approval requirements (lifecycle invariants)

Six rules govern when a confirmation can be approved. All are always-on; no config flag can disable them.

### Rule 6.1 — Confirmation must reference a valid `suggestion_id`

The builder receives a suggestion + a host config. If the suggestion no longer exists in `intent_suggestions` (e.g. host-side cleanup, manual delete, schema migration), the confirmation request MUST NOT be constructible. Validates by row lookup at build time.

### Rule 6.2 — Suggestion must not be expired

If the underlying `PolicySuggestion.expires_at` is non-null and in the past, no confirmation request is constructed. The suggestion's TTL governs whether a confirmation can ever exist.

### Rule 6.3 — Suggestion must pass all Phase 2.4 eligibility gates

A suggestion that wouldn't be eligible per Phase 2.4 spec §4 (low confidence / catch-all / missing slots / live_verified=False / adapter misconfig / unexplainable reason) cannot become a confirmation. The Phase 2.5.1 builder calls into the existing `intent_suggestion._is_eligible` (or its public re-export) to enforce this.

### Rule 6.4 — Approval must be explicit

The confirmation surface MUST require a discrete user gesture: a CLI command (`tokenpak intent confirm <confirmation_id> --approve`), a dashboard button click, or an API POST with a body that includes the confirmation_id. Implicit approval mechanisms — auto-approve-after-N-seconds, default-yes, default-no-with-timeout-as-yes — are explicitly forbidden.

### Rule 6.5 — Approval must be logged without raw prompt text

The approval event lands in a new `intent_confirmation_events` table (Phase 2.5.3 schema). Schema columns: `confirmation_id`, `prior_status`, `new_status`, `transitioned_at`, `actor_surface` (`cli` / `dashboard` / `api`), and a `reason` field that is operator-supplied free text **with strict size + content validation** — capped at 200 chars, sanitized through the Phase 2.4.1 forbidden-phrase guardrail, and stored verbatim. NO raw prompt text. NO secrets. Asserted with the sentinel-substring pattern.

### Rule 6.6 — Approved action must be one-time by default

Approval applies to exactly the request whose contract_id matches the confirmation request's contract_id. Re-using an approved confirmation for a different request is forbidden by the executor (Phase 2.5.4 deliverable). The state machine's `approved` status terminates with execution (or failure-to-execute, when that lands in 2.5.4) — there is no "approved-and-still-active" reusable state.

### Rule 6.7 — Rejected / expired confirmations must not mutate anything

A confirmation that transitions to `rejected`, `expired`, or `canceled` MUST NOT touch the request's fwd_headers, body, target_url, or any adapter / provider / credential state. The state-machine implementation (Phase 2.5.3) tracks the transition in telemetry only.

### 6.8 Eligibility evaluation order at build time

```python
# Pseudocode — implemented in Phase 2.5.1
def is_eligible_for_confirmation(suggestion, contract, decision, config) -> bool:
    if not config.require_confirmation:
        return False
    if config.mode != "confirm":
        return False
    # Suggestion must still be live + valid.
    if suggestion is None:
        return False
    if suggestion.expires_at and suggestion.expires_at < now():
        return False
    if not _is_phase_2_4_eligible(suggestion, contract, decision, config):
        return False
    if suggestion.suggestion_type not in _CONFIRMABLE_TYPES:
        return False  # excluded action types never become confirmations
    if (
        suggestion.suggestion_type == SUGGESTION_PROVIDER_MODEL
        and not _provider_live_verified(suggestion.recommended_provider)
        and not config.allow_unverified_providers
    ):
        return False  # safety §6 rule 4 carry-through
    return True
```

---

## 7. UX rules

These rules are normative. Every render path MUST honor them; the §10 wording tests assert each.

### 7.1 Allowed wording (confirmation-specific)

The Phase 2.4 spec §8.1 allowed-wording list carries through unchanged. Phase 2.5 adds these confirmation-specific phrases:

- "Approve this recommendation?"
- "Apply once?"
- "Confirm this one-time change?"
- "This has not been applied yet."
- "Pending your approval."
- "Approve" / "Reject" / "Cancel" (button labels)

### 7.2 Forbidden wording (carried through from Phase 2.4)

The Phase 2.4 spec §8.2 forbidden-wording list MUST stay enforced — `Applied`, `Changed`, `Routed to`, `Switched to`, `Now using`, `Updated`, `Will route`, `Will switch`. **NO confirmation-specific exceptions.** Even when a confirmation is approved, the surface MUST NOT use these phrases until 2.5.4 actually executes the action — and even then, the post-execution surface must use precise tense ("Applied for this one request", scoped, not the unscoped present tense).

### 7.3 Pre-approval wording rule

Before approval, every render path MUST clearly state the action **has not happened**. Approved variants of the wording rule:

- "This has not been applied yet."
- "Awaiting your approval to apply this for one request."
- "Pending — TokenPak has not made the change."

### 7.4 Risk-level rendering

Every confirmation surface MUST display the `risk_level` field. Suggested visual treatment (Phase 2.5.2 work):

| `risk_level` | CLI rendering | Dashboard rendering |
|---|---|---|
| `low` | `[low]` prefix | green badge |
| `medium` | `[medium]` prefix | amber badge |
| `high` | `[high]` prefix | red badge |

The renderer MUST NOT hide the risk label, even for `low`. The operator scanning a confirmation surface must always see the level adjacent to the action.

### 7.5 Reason requirement

Every confirmation MUST include a *why* clause in its `message`. Format:

```
... because <decision_reason rendered in plain English>.
```

Reuses the Phase 2.4.1 reason-rendering table from `intent_suggestion.py`. New reasons specific to confirmable actions get added there in Phase 2.5.1.

### 7.6 Dry-run disclaimer (carried through)

Every render path MUST include "DRY-RUN / PREVIEW ONLY" in plain text adjacent to the confirmation through Phase 2.5.3. In Phase 2.5.4 (one-time low-risk execution), the disclaimer changes to "ONE-TIME APPLICATION — change scoped to this request" for the executed-action card; pre-approval cards still carry "DRY-RUN / PREVIEW ONLY".

---

## 8. Safety and privacy

The Phase 0 / 2.4 privacy contract carries through unchanged:

- **No raw prompt text** in any confirmation field. Templated `title` / `message` / `proposed_action`. No caller-supplied substrings.
- **No secrets** in any field. Provider slugs (e.g. `tokenpak-mistral`) are public symbols and may appear in `proposed_action`; provider API keys, OAuth tokens, account ids never do.
- **No raw credentials** of any kind. The Phase 0 / Standard #23 §6.4 prompt-locality rule carries through: `intent_confirmation_events` has no column that could carry credential material. The Phase 2.4.1 forbidden-phrase guardrail extends to scan confirmation fields.
- **No full credential values** even hashed. The schema simply does not have such a column.
- **Confirmation logs link only by IDs / hashes.** `confirmation_id`, `suggestion_id`, `decision_id`, `contract_id` are the join keys — all opaque IDs.
- **Confirmation surfaces show risk level + reason.** Per §7.4 and §7.5.
- **`live_verified=False` blocked by default.** Per §6.8 eligibility rule. The `allow_unverified_providers = true` opt-in (carried from 2.4) re-enables this path for explicitly-trusted dev hosts.

### Cross-references

- Architecture §5.1 — byte-fidelity rule (request body unchanged through every Phase 2.5.x sub-sub-phase).
- Architecture §7.1 — prompt-locality rule.
- Standard #23 §4.3 — capability gate (still load-bearing for any wire-side `X-TokenPak-Confirmation-*` headers a future phase introduces; **2.5.x does not introduce wire headers**).
- Standard #23 §6.4 — `live_verified` semantics (driver of §6.8 eligibility).

---

## 9. Rollout plan

Each sub-sub-phase is its own PR with its own acceptance criteria. **Phase 2.5 (this spec) is no-code.** Subsequent sub-sub-phases land sequentially.

| Sub-sub-phase | Deliverable | New runtime behavior | Default off? |
|---|---|---|---|
| **2.5** | This spec; ratification | None | n/a |
| **2.5.1** | `tokenpak/proxy/intent_confirmation.py` — `PolicyConfirmationRequest` dataclass + pure-function builder. Validates §4 action-type allow-list, §6 eligibility rules. Writes to a new `intent_confirmations` table. **No surface changes; no execution.** | One new SQLite table; nothing visible. | yes |
| **2.5.2** | Surfaces wired (CLI explain / report / preview, dashboard panel, API endpoint). All five surfaces read the new table; renderers honor §7 wording rules including risk-level rendering. With `intent_policy.mode = observe_only` (default), surfaces show empty confirmation sections. | Surfaces show empty confirmation sections; nothing actionable. | yes |
| **2.5.3** | Approve / reject / expire / cancel state machine. New `intent_confirmation_events` table tracking transitions. CLI verbs (`tokenpak intent confirm <id> --approve | --reject | --cancel`) + dashboard buttons + API POST endpoint. **Approval is recorded but does not execute anything.** Expiration is automatic via TTL. | Operator can record approve/reject/cancel; no request mutation. | yes |
| **2.5.4** | One-time approved-action execution for **low-risk policies only** (§5.1's `low` tier: `apply_compression_profile_once`, `apply_cache_policy_once`, `apply_delivery_strategy_once`, `suppress_suggestion_temporarily`). `medium` and `high` tiers do NOT execute in 2.5.4 — they remain "approved but observation-only". | One-time mutation per approved low-risk request. Bounded scope per §4. | yes |
| **2.6** | Limited enforcement spec — budget caps only | n/a (spec only) | n/a |

### Out of scope for the entire 2.5.x line

- Wire-side `X-TokenPak-Confirmation-*` response headers. Phase 2.5.x carries `confirmation_surface.response_headers` locked at `False` per §3.
- Cross-request approval persistence. Each confirmation maps to one `contract_id`; an approved confirmation for request A does not auto-approve request B even if the suggestion is identical.
- Bulk approval. Each confirmation requires its own explicit gesture per §6.4. Multi-select UI is not in 2.5.x.
- `medium` / `high` tier execution. Reserved for a Phase 2.5.5 or Phase 2.6+ ratification.

---

## 10. Test strategy

Each Phase 2.5.x PR ships with a regression suite covering the directive's eleven test categories. Categories are spec-pinned here so no sub-sub-phase can ship without all eleven.

| # | Category | Asserts |
|---|---|---|
| 1 | **Confirmable suggestion creates confirmation request** | Eligible (§6) suggestion + `mode = confirm` + `require_confirmation = true` produces a `PolicyConfirmationRequest` of the correct action-type; all required fields populated; `status = pending`; `expires_at` derived from `confirmation_ttl_seconds`. (2.5.1+) |
| 2 | **Low-confidence suggestion cannot become confirmation** | Underlying `IntentContract.confidence < threshold` → no confirmation. The Phase 2.4 §4 eligibility carry-through. (2.5.1+) |
| 3 | **Expired suggestion cannot become confirmation** | `suggestion.expires_at < now()` → no confirmation. §6.2 enforced. (2.5.1+) |
| 4 | **`live_verified=False` blocked by default** | `route_once_to_provider_model` whose `recommended_provider` carries `live_verified=False` AND `allow_unverified_providers=false` → no confirmation. Symmetry: same input with the flag flipped emits the confirmation. (2.5.1+) |
| 5 | **Approval is explicit** | A confirmation cannot transition `pending → approved` without a discrete user gesture. The 2.5.3 approve helper requires an explicit `confirmation_id` argument plus an explicit verb (`--approve`). Test calls the executor without those args and asserts the state stays `pending`. (2.5.3+) |
| 6 | **Rejection produces no mutation** | Approve → reject path, plus baseline-rejection path. Assert request body / fwd_headers / target_url unchanged across the test request. (2.5.3+) |
| 7 | **Expiration produces no mutation** | TTL elapses without action. Confirmation transitions to `expired`. Same byte-stability assertions as rule 6. (2.5.3+) |
| 8 | **No raw prompt / secrets emitted** | Sentinel-substring across every render path + every column of the `intent_confirmations` and `intent_confirmation_events` tables. The `reason` field accepts operator-supplied text — that text is sanitized through the forbidden-phrase guardrail before storage; sentinel-substring tests pin the contract. (2.5.1+) |
| 9 | **Wording guardrail** | Every emitted `title` / `message` / `proposed_action` MUST be free of the Phase 2.4.1 forbidden-phrase list. Pre-approval rendering MUST include the "has not been applied" wording from §7.3. Post-approval rendering (2.5.4+) uses the scoped present tense exactly as §7.2 specifies. (2.5.1+) |
| 10 | **No routing mutation in spec / build phases** | Through 2.5.3, no Phase 2.5.x code path may mutate fwd_headers / body / target_url. End-to-end test sends 100 requests with `mode = confirm` + active confirmations and asserts the response bytes are byte-identical to a 100-request baseline run with `mode = observe_only`. (2.5.1 → 2.5.3) |
| 11 | **No classifier mutation** | `tokenpak/proxy/intent_classifier.py` files-changed list MUST be empty across every Phase 2.5.x PR. Same CI gate as Phase 2.4. (every 2.5.x PR) |

The Phase 2.4 invariants from `tests/test_intent_layer_phase01_invariant.py` continue to apply.

---

## 11. Acceptance

### What this spec accepting means

- The 10-section design above is ratified as the working contract for Phase 2.5.0–2.5.4.
- The §4 confirmable / excluded action-type lists are normative; the §6 lifecycle rules are normative; the §7 UX rules are normative. Standards uplift (a possible `26-intent-confirm-mode.md` standards entry in the vault) is a separate ratification step.
- The §9 sub-sub-phase plan governs sequencing. No 2.5.x PR may ship without an updated sub-sub-phase entry checking off the deliverable.
- Phase 2.5.1 (the first code-shipping sub-sub-phase) MAY proceed once this spec is ratified.

### What this spec accepting does NOT mean

- **No runtime behavior changes.** This PR adds one markdown file. No Python, no JSON, no dashboard, no test, no proxy code, no CLI command.
- **No classifier behavior changes.** `tokenpak/proxy/intent_classifier.py` remains untouched.
- **No routing behavior changes.** Phase 2.5 (this PR) has zero effect on dispatch, forward headers, or response bodies.
- **No provider backlog work.** Bedrock generic, Anthropic-on-Vertex, IBM watsonx, SigV4 / OAuth scaffolding, `--llm-assist`, and additional scaffold-renderer expansion remain held per the standing directive.
- **No ratification of `enforce` mode.** `mode = enforce` remains a reserved value that the loader rejects with "not yet implemented" through Phase 2.5.x. Its spec is Phase 2.6.
- **No ratification of Phase 2.5.1+ designs.** Each sub-sub-phase comes back for explicit approval. This spec is the foundation, not a pre-approval.

### Clear rollout path

```
[2.5 spec — ratify]                              ←  this PR
    ↓
[2.5.1 PolicyConfirmationRequest + builder]      ←  needs Kevin go-ahead
    ↓
[2.5.2 surfaces wired (no-op default)]
    ↓
[2.5.3 approve/reject/expire/cancel state machine]
    ↓
[2.5.4 one-time low-risk execution]              ←  first PR with byte-level mutations
    ↓
[2.6 limited enforcement (budget caps only) spec]
```

Each step gated on the previous step's acceptance. Each step's default state is **off**. Each step's tests cover §10 categories applicable to that step. The path can stop or pause at any point without leaving the system in an unsafe state — every sub-sub-phase's exit criterion is "still observation-only at runtime if config wasn't flipped on, and bounded to the §4 low-risk allow-list when it is".

---

## 12. References

### Phase 0–2.4.3 (already merged)

- PR #44 (Phase 0): `feat(proxy): Phase I0-1 — Intent Layer Phase 0 (telemetry-only)` — merge `57af1f18`
- PR #45 (Phase 0.1): `feat(cli): Phase 0.1 — Intent Layer doctor view + explain output + docs` — merge `3c88f2b6`
- PR #46 (Phase 1): `feat(cli): Phase 1 — tokenpak intent report (observation-only)` — merge `d6537f02`
- PR #47 (Phase 1.1): `feat(proxy,dashboard): Phase 1.1 — intent dashboard / read-model` — merge `e7d7d2e1`
- PR #48 (Phase 2 spec): `docs(specs): Phase 2 — intent policy engine design / spec only` — merge `38daf997`
- PR #49 (Phase 2.1): `feat(proxy,cli): Phase 2.1 — dry-run intent policy engine` — merge `0da0eed6`
- PR #50 (Phase 2.2): `feat(proxy,cli,dashboard): Phase 2.2 — policy explain/report/dashboard preview` — merge `353db9ec`
- PR #51 (Phase 2.4 spec): `docs(specs): Phase 2.4 — opt-in suggest mode design` — merge `fdaac3a1`
- PR #52 (Phase 2.4.1): `feat(proxy,cli): Phase 2.4.1 — PolicySuggestion builder + telemetry` — merge `22719e6c`
- PR #53 (Phase 2.4.2): `feat(proxy,cli,dashboard): Phase 2.4.2 — suggestion display surfaces` — merge `1d7c313b`
- PR #54 (Phase 2.4.3): `feat(proxy,cli,dashboard): Phase 2.4.3 — opt-in suggest mode config` — merge `2eca2ed2`

### Documents

- `~/vault/02_COMMAND_CENTER/proposals/2026-04-24-tokenpak-intent-layer-phase-0.md` — origin proposal.
- `~/vault/02_COMMAND_CENTER/tokenpak-standards-internal/23-provider-adapter-standard.md` — capability gate (§4.3), `live_verified` semantics (§6.4).
- `docs/internal/specs/phase2-intent-policy-engine-spec-2026-04-25.md` — parent spec (this document elaborates §8 sub-phase 2.5).
- `docs/internal/specs/phase2.4-suggest-mode-spec-2026-04-26.md` — Phase 2.4 sub-spec (this 2.5 spec extends 2.4's eligibility gates and UX rules).
- `docs/reference/intent-layer-phase-0.md` — Phase 0 fundamentals (operator-facing).
- `docs/reference/intent-reporting.md` — Phase 1 CLI report (operator-facing).
- `docs/reference/intent-dashboard.md` — Phase 1.1 dashboard / API (operator-facing).
- `docs/reference/intent-policy-preview.md` — Phase 2.1 / 2.2 policy preview (operator-facing).
- `docs/reference/intent-suggest-mode.md` — Phase 2.4.3 suggest-mode config (operator-facing).
- This document — `docs/internal/specs/phase2.5-confirm-mode-spec-2026-04-26.md` — Phase 2.5 design (internal).

### Standards (vault, internal)

- `00-product-constitution.md §13` — TokenPak as TIP-1.0 reference implementation.
- `01-architecture-standard.md §5.1` — byte-fidelity rule.
- `01-architecture-standard.md §7.1` — prompt-locality rule.
- `10` (telemetry-store schema compatibility) — `§E1` migration rule, applicable to Phase 2.5.1's new `intent_confirmations` table and Phase 2.5.3's new `intent_confirmation_events` table.
- `21 §9.8` — process-enforced gating (CI).
- `23-provider-adapter-standard.md §4.3` — the canonical capability-gated middleware-activation pattern.
- `23-provider-adapter-standard.md §6.4` — `live_verified` semantics (driver of §6.8 eligibility).

---

## Appendix A — open design questions deferred to sub-sub-phases

| Question | Sub-sub-phase to resolve |
|---|---|
| When `set_budget_warning_threshold` is approved, where exactly is the new threshold persisted (config file vs runtime memory)? | 2.5.3 (likely runtime memory until 2.6's enforcement design lands) |
| Does `suppress_suggestion_temporarily` need its own duration (independent of `confirmation_ttl_seconds`)? | 2.5.1 (probably yes — suggestion suppression should out-live the confirmation TTL) |
| For `route_once_to_provider_model` (medium-risk), is the executor allowed in 2.5.4 or deferred to a Phase 2.5.5? | 2.5.4 (exclude from low-risk-only execution; defer to 2.5.5 or 2.6) |
| Should the `actor_surface` field on `intent_confirmation_events` carry the SDK / CLI version that recorded the transition? | 2.5.3 (probably yes — useful for forensics; non-leaky since version strings are public) |
| Does the dashboard surface a "Pending confirmations" badge in the header globally, or only inside the Intent Policy panel? | 2.5.2 (likely inside the panel only, mirroring 2.4.2 layout) |

---

## Appendix B — what changes if Phase 0 baseline data invalidates an assumption

The assumptions baked into this spec inherit from Phase 0's baseline-report deliverable. If the eventual baseline shows:

- **Catch-all dominance > 50 %** → most decisions never become suggestions, so most never become confirmations either. Phase 2.5 stands; the operator-facing impact is "you opted into confirm mode and aren't seeing many prompts" — the correct, safe behavior.
- **Confidence histogram concentrated below 0.65** → most eligible suggestions get blocked; same outcome.
- **Per-class slot-fill rates very low** → `missing_slot_improvement` (a Phase 2.4.1 constructive type) is excluded from confirmability per §4 — it's not a recommended action, just guidance. So this doesn't affect Phase 2.5 directly.
- **Operator complaint: confirmations interrupt workflow** → the design choices here (§3 `require_confirmation` master switch, per-surface visibility flags, TTL-driven auto-expiration) all contribute to graceful degradation. If complaints arise, the response is to tune those knobs, not to soften approval requirements.

Phase 2.5 explicitly does NOT depend on the baseline being in. The spec stands as a design contract; sub-sub-phase tuning happens against real data.
