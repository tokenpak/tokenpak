# Milestone — Intent Advisory MVP

**Date**: 2026-04-26
**Status**: ✅ **complete** (closeout)
**Workstream**: TokenPak Intent Layer — Phase 0 through Phase 2.5 spec
**Owner**: Kevin (direction) / Sue (execution)
**Origin**: `~/vault/02_COMMAND_CENTER/proposals/2026-04-24-tokenpak-intent-layer-phase-0.md` (Adj-1 + Adj-2 banner block, 2026-04-25)
**Foundation specs**:
  - `docs/internal/specs/phase2-intent-policy-engine-spec-2026-04-25.md` — unified Phase 2 design
  - `docs/internal/specs/phase2.4-suggest-mode-spec-2026-04-26.md` — suggest mode sub-spec
  - `docs/internal/specs/phase2.5-confirm-mode-spec-2026-04-26.md` — confirmation mode sub-spec

This document closes out the Intent Advisory workstream at the **MVP milestone**. The milestone is reached when an operator can: (a) observe how the proxy classifies requests, (b) inspect what a future intent-aware policy engine *would* recommend, (c) opt in to advisory suggestions on every operator-facing surface, and (d) read a ratified design contract for the confirmation handshake that comes next. All four are now true.

This is a **closeout document**, not an implementation gate. The next item on the roadmap is Phase 2.5.1 (confirmation request builder); it requires explicit approval before any code lands.

---

## 1. What shipped

Twelve PRs across the workstream, all merged onto `main`:

| PR | Title | Merge SHA | Layer |
|---|---|---|---|
| #44 | `feat(proxy): Phase I0-1 — Intent Layer Phase 0 (telemetry-only)` | `57af1f18` | Phase 0 — classifier + IntentContract + intent_events table + capability-gated wire emission |
| #45 | `feat(cli): Phase 0.1 — Intent Layer doctor view + explain output + docs` | `3c88f2b6` | Phase 0.1 — `tokenpak doctor --intent / --explain-last` + privacy-contract test |
| #46 | `feat(cli): Phase 1 — tokenpak intent report (observation-only)` | `d6537f02` | Phase 1 — CLI `tokenpak intent report --window Nd [--json]` with 10 metrics + 5 narrative sections |
| #47 | `feat(proxy,dashboard): Phase 1.1 — intent dashboard / read-model` | `e7d7d2e1` | Phase 1.1 — `GET /api/intent/report` + dashboard "Intent Layer" panel |
| #48 | `docs(specs): Phase 2 — intent policy engine design / spec only` | `38daf997` | Phase 2 — unified policy engine spec (10 sections) |
| #49 | `feat(proxy,cli): Phase 2.1 — dry-run intent policy engine` | `0da0eed6` | Phase 2.1 — pure engine + 7-action enum + 4 always-on safety rules + intent_policy_decisions table + `tokenpak intent policy-preview` |
| #50 | `feat(proxy,cli,dashboard): Phase 2.2 — policy explain/report/dashboard preview` | `353db9ec` | Phase 2.2 — explain/report/dashboard surfaces over the policy decisions table; `/api/intent/policy-report` |
| #51 | `docs(specs): Phase 2.4 — opt-in suggest mode design / spec only` | `fdaac3a1` | Phase 2.4 — suggest mode sub-spec (11 sections) |
| #52 | `feat(proxy,cli): Phase 2.4.1 — PolicySuggestion builder + telemetry` | `22719e6c` | Phase 2.4.1 — pure builder + 7 suggestion types + 7 eligibility gates + forbidden-wording guardrail + intent_suggestions table |
| #53 | `feat(proxy,cli,dashboard): Phase 2.4.2 — suggestion display surfaces` | `1d7c313b` | Phase 2.4.2 — 5 surfaces wired (CLI explain/report/policy-preview, API, dashboard panel) with canonical advisory labels |
| #54 | `feat(proxy,cli,dashboard): Phase 2.4.3 — opt-in suggest mode config` | `2eca2ed2` | Phase 2.4.3 — `~/.tokenpak/policy.yaml` loader, 3 force-applied safety overrides, `tokenpak intent config` CLI |
| #55 | `docs(specs): Phase 2.5 — confirmation mode design / spec only` | `2702eb79` | Phase 2.5 — confirmation mode sub-spec (10 sections, 6 allowed action types, 6 explicitly excluded) |

Cumulative diff stat (approximate):

- ~6,500 lines of production Python across `tokenpak/proxy/intent_*` modules
- ~2,000 lines of new HTML/JS dashboard surfaces
- ~4,500 lines of pytest tests across 7 new test files
- ~3,000 lines of operator-facing documentation in `docs/reference/intent-*.md`
- ~2,500 lines of internal design documentation in `docs/internal/specs/phase{2, 2.4, 2.5}-*.md`

---

## 2. Current capabilities

Listed by what an operator can do **today** on a host running `tokenpak` from `main`:

### 2.1 Classification

- Every request flowing through the proxy is classified upstream into one of the 10 canonical intents (`status` / `usage` / `debug` / `summarize` / `plan` / `execute` / `explain` / `search` / `create` / `query`).
- The classifier is **rule-based, deterministic, no LLM** — Option A from the proposal. `intent_source = "rule_based_v0"` on every row.
- Five catch-all reasons surface when classification falls back: `empty_prompt` / `prompt_too_short` / `keyword_miss` / `confidence_below_threshold` / `slot_ambiguous`.

### 2.2 Telemetry

Three SQLite tables under `~/.tokenpak/telemetry.db`, all linked by `contract_id`:

- `intent_events` — one row per classified request (Phase 0).
- `intent_policy_decisions` — one row per dry-run engine decision (Phase 2.1).
- `intent_suggestions` — one row per eligible suggestion (Phase 2.4.1).

Privacy contract: every row stores hashes / IDs / templated text only. Raw prompt content never touches these tables; the sentinel-substring pattern is asserted across CLI / API / dashboard render paths.

### 2.3 Operator surfaces

| Surface | Command / Endpoint | Purpose |
|---|---|---|
| Doctor — intent | `tokenpak doctor --intent [--json]` | Classifier activation, proxy capability publication, per-adapter §4.3 gate declaration, would-emit-vs-telemetry-only summary, active config snapshot |
| Doctor — explain last | `tokenpak doctor --explain-last [--json]` | Latest classification + linked policy decision + linked policy suggestions; full field-level dump |
| Intent report (CLI) | `tokenpak intent report --window Nd [--json]` | 10 metrics + 5 narrative sections; appended Phase 2.2 "Policy summary" + Phase 2.4.2 "Advisory Suggestions" sub-sections |
| Policy preview (CLI) | `tokenpak intent policy-preview [--last] [--json]` | Latest dry-run decision + linked suggestions |
| Suggestions inspector (CLI) | `tokenpak intent suggestions [--last] [--json]` | Latest individual suggestion (dev / debug surface) |
| Config inspector (CLI) | `tokenpak intent config [--show] [--validate] [--json]` | Active `intent_policy` config + safety-override warnings |
| Read-model API | `GET /api/intent/report?window=Nd` | Phase 1.1 dashboard payload (cards + operator panel) |
| Read-model API — policy | `GET /api/intent/policy-report?window=Nd` | Phase 2.2 dashboard payload + Phase 2.4.2 `suggestions` section + Phase 2.4.3 `active_policy_config` + `suggest_mode_active` metadata |
| Dashboard UI | `http://127.0.0.1:8766/dashboard/` | "Intent Layer" panel (Phase 1.1) + "Intent Policy" panel (Phase 2.2) with Advisory Suggestions sub-section (Phase 2.4.2) |

### 2.4 Optional opt-in: suggest mode

Hosts can opt in to advisory suggest-mode badging by editing `~/.tokenpak/policy.yaml`:

```yaml
intent_policy:
  mode: suggest
```

The opt-in only adds "Suggest mode active" badging to the surfaces above — it does NOT change request handling. The Phase 2.4 spec §10 "default-off" invariant holds: a host that has not edited the config sees zero behavioral difference vs. Phase 1.1.

---

## 3. What is intentionally not active

Listed by what is **explicitly out of scope** at this milestone:

- ❌ **No automatic routing.** No request reroutes based on engine output. The `recommended_provider` / `recommended_model` fields surface in telemetry + suggestions but are never read by the dispatcher.
- ❌ **No request mutation.** Body bytes, headers, target URL — all preserved exactly as in Phase 1.1. The Phase 2.x engine is a side-channel observer.
- ❌ **No provider/model switching** (automatic, semi-automatic, or otherwise).
- ❌ **No confirmation execution.** The Phase 2.5 spec is ratified but no `PolicyConfirmationRequest` builder exists yet (Phase 2.5.1 deliverable).
- ❌ **No enforcement mode.** `mode = enforce` is a reserved value the loader rejects with a clear "not yet implemented" warning; even when hit, it falls back to `observe_only`.
- ❌ **No classifier behavior changes.** Every classification on every host runs the same `rule_based_v0` keyword table. `tokenpak/proxy/intent_classifier.py` is untouched since Phase 0 (PR #44).
- ❌ **No LLM classifier** (Option B). Reserved for a future phase gated on the Phase 0 baseline-report deliverable.
- ❌ **No wire-side `X-TokenPak-Suggestion-*` / `X-TokenPak-Confirmation-*` headers.** `suggestion_surface.response_headers` is force-locked False until a future ratification.
- ❌ **No confirm / approve / reject CLI verbs.** Phase 2.5 is spec-only; the verbs are Phase 2.5.3 deliverables.
- ❌ **No budget-cap enforcement.** `flag_budget_risk` is a reserved action in the Phase 2.1 enum; no engine path emits it yet, and even if it did, no enforcement exists. Phase 2.6 is spec scope.

---

## 4. Safety guarantees (always-on)

The following invariants hold at every host, every config, every code path through the Intent Layer. The forbidden-phrase regex, structural-import tests, and sentinel-substring tests pin them in CI.

### 4.1 Privacy

- Raw prompt text never enters any cross-request store. Only `raw_prompt_hash` (sha256 hex) appears in `intent_events`; nothing prompt-derived appears in `intent_policy_decisions` or `intent_suggestions`.
- Every render path — `tokenpak doctor --explain-last`, `tokenpak intent report`, `tokenpak intent policy-preview`, `tokenpak intent suggestions`, `tokenpak intent config`, `GET /api/intent/{report,policy-report}`, the dashboard panel — has been sentinel-substring-tested for prompt leak. **Zero leaks across all surfaces.**
- No secrets, no credential material, no full credential values appear in any telemetry row, render path, or payload field.

### 4.2 Wire-fidelity

- The Phase 0 §4.3 capability gate (`tip.intent.contract-headers-v1`) governs every wire-side TIP header. No first-party adapter declares the gate label by default; this is structurally tested in `tests/test_intent_layer_phase01_invariant.py`.
- Architecture §5.1 byte-fidelity rule preserved: Phase 2.x engine integration is a side-channel; no edits to dispatch / forward primitives.
- Per the Phase 2.4 spec §11 wording rule: forbidden phrases (`Applied`, `Changed`, `Routed to`, `Switched to`, `Now using`, `Updated`, `Will route`, `Will switch`) are blocked by `_check_wording` at suggestion-build time. A regression in the template would raise `SuggestionWordingError` before any user-facing render.

### 4.3 Default-off posture

- Default config: `mode: observe_only`, `dry_run: true`, `allow_auto_routing: false`, `allow_unverified_providers: false`, `show_suggestions: false`, `suggestion_surface.response_headers: false`.
- Every operator-facing capability that adds a behavior change (advisory badging, future confirmation prompts, future budget warnings) is **opt-in** via explicit edit to `~/.tokenpak/policy.yaml`.

### 4.4 Forced-safe overrides

The Phase 2.4.3 config loader force-applies three safety invariants regardless of what the file says:

1. `dry_run` is forced `True`. (Locked through every Phase 2.5.x sub-sub-phase.)
2. `allow_auto_routing` is forced `False`. (Locked indefinitely until a future spec authorizes auto-routing.)
3. `suggestion_surface.response_headers` is forced `False`. (Same.)

Each emits a warning at load time when an override is attempted; the warning surfaces in `tokenpak intent config --validate`.

### 4.5 Reserved-mode rejection

- `mode = confirm` and `mode = enforce` are reserved values; the Phase 2.4.3 loader rejects them with a clear "not yet implemented" warning and falls back to `observe_only`.
- A host that tries to set them in the config file does NOT crash; it falls back safely.

---

## 5. CI / test coverage summary

Suite size at the milestone (relative to the pre-Intent-Layer baseline):

| Phase | Test file | Tests | Cumulative `pytest tests/` |
|---|---|---|---|
| Pre-Phase 0 baseline | (existing) | — | 940 |
| Phase 0 | `test_intent_layer_phase0.py` | 51 | 991 |
| Phase 0.1 | `test_intent_layer_phase01_invariant.py` | 3 | 994 |
| Phase 1 | `test_intent_report.py` | 24 | 1018 |
| Phase 1.1 | `test_intent_dashboard.py` | 27 | 1045 |
| Phase 2.1 | `test_intent_policy_engine_phase21.py` | 38 | 1083 |
| Phase 2.2 | `test_intent_policy_phase22.py` | 26 | 1109 |
| Phase 2.4.1 | `test_intent_suggestion_phase24_1.py` | 36 | 1145 |
| Phase 2.4.2 | `test_intent_suggestion_phase24_2.py` | 27 | 1172 |
| Phase 2.4.3 | `test_intent_suggest_mode_phase24_3.py` | 36 | 1208 |
| Phase 2.5 spec | (no code) | 0 | 1208 |
| **Intent Advisory MVP total** | **9 test files** | **268 tests** | **1208** |

CI workflows that gate every Phase 2.x PR:

- `CI — Lint & Test` (Lint Ruff + Test 3.10 / 3.11 / 3.12 / 3.13 + Import contracts + bandit + cli-docs-in-sync + headline-benchmark)
- `TIP-1.0 Self-Conformance` (3.10 / 3.11 / 3.12)
- `Repo Hygiene Check` (×2)

Every PR in the workstream merged with **all checks green**. Two CI round-trips required across the whole workstream:

- PR #45 (Phase 0.1) — `cli-docs-in-sync` failed once because the generator wasn't re-run after adding `--intent` / `--explain-last`. Fixed by running `python3 scripts/generate-cli-docs.py` and re-pushing.
- PR #52 (Phase 2.4.1) — `Lint (Ruff)` failed on an unused `SuggestionWordingError` import after a test refactor; CI ruff stricter than local cache. Fixed and re-pushed.

Both round-trips were tooling-friction issues, not behavioral failures.

### 5.1 Cross-phase invariants pinned in tests

- Forbidden-phrase regex (`Applied` / `Changed` / `Routed to` / `Switched to` / `Now using` / `Updated` / `Will route` / `Will switch`) — pinned in `tests/test_intent_suggestion_phase24_1.py` and re-asserted across every render path in `tests/test_intent_suggestion_phase24_2.py`.
- Sentinel-substring privacy test — present in every Phase 2.x test file.
- Structural "no dispatch primitives imported" test — every new module under `tokenpak/proxy/intent_*` is structurally verified to NOT import `forward_headers` / `pool.request` / `pool.stream`.
- Phase 0 default invariant ("no first-party adapter declares the gate label") — pinned in `tests/test_intent_layer_phase0.py::TestCapabilityGate::test_no_first_party_adapter_declares_label_in_phase_0`. Re-asserted by Phase 1.1 + 2.2 + 2.4.2 dashboard / API schema tests.
- Classifier-immutability invariant — `tokenpak/proxy/intent_classifier.py` constants pinned (`CLASSIFY_THRESHOLD == 0.4`, `INTENT_SOURCE_V0 == "rule_based_v0"`). Every Phase 2.x test file asserts these.

---

## 6. CLI / API / dashboard surface inventory

Quick reference for operators. Every surface is read-only and respects the Phase 2.4.3 `suggestion_surface` flags + the suggest-mode opt-in.

### CLI

```bash
# Diagnostic snapshot of the Intent Layer subsystem
tokenpak doctor --intent
tokenpak doctor --intent --json

# Latest classified request, with linked policy decision + suggestions
tokenpak doctor --explain-last
tokenpak doctor --explain-last --json

# Window-scoped reporting (default 14d; 0d = all rows)
tokenpak intent report
tokenpak intent report --window 7d
tokenpak intent report --window 30d --json
tokenpak intent report --window 0d        # all rows

# Latest dry-run policy decision + linked suggestions
tokenpak intent policy-preview
tokenpak intent policy-preview --json

# Latest individual suggestion (dev/debug)
tokenpak intent suggestions
tokenpak intent suggestions --json

# Active policy config + safety-override warnings
tokenpak intent config --show
tokenpak intent config --validate
tokenpak intent config --json
```

### API

```http
GET /api/intent/report?window=Nd          # Phase 1.1 (default 14d on the API; 0d = all rows)
GET /api/intent/policy-report?window=Nd   # Phase 2.2 + Phase 2.4.2 suggestions section
                                          # + Phase 2.4.3 active_policy_config metadata
```

### Dashboard

```
http://127.0.0.1:8766/dashboard/
```

Two panels:

- "Intent Layer" — Phase 1.1; cards + tables + operator review areas
- "Intent Policy" — Phase 2.2; cards + tables; Phase 2.4.2 added the "Advisory Suggestions" sub-section; Phase 2.4.3 added the `suggest_mode_active` badge

---

## 7. Deferred roadmap

The following phases are designed but not implemented. **None of them may proceed without explicit Kevin approval.**

| Phase | Scope | Spec status |
|---|---|---|
| **2.5.1** | Confirmation request builder. Pure-function `build_confirmation_request(suggestion, contract, decision, config) -> Optional[PolicyConfirmationRequest]` with the §6 lifecycle invariants enforced. New `intent_confirmations` SQLite table. **No surface changes; no execution.** | Ratified (`docs/internal/specs/phase2.5-confirm-mode-spec-2026-04-26.md` §9 deliverable 1) |
| **2.5.2** | Confirmation surfaces wired (CLI explain / report / policy-preview, dashboard panel, API endpoint). All five surfaces read the new table; renderers honor §7 wording rules including risk-level rendering. With `mode = observe_only` (default), surfaces show empty confirmation sections. | Ratified (sub-spec §9 deliverable 2) |
| **2.5.3** | Approve / reject / expire / cancel state machine. New `intent_confirmation_events` table. CLI verbs (`tokenpak intent confirm <id> --approve / --reject / --cancel`) + dashboard buttons + API POST endpoint. **Approval is recorded but does not execute anything.** | Ratified (sub-spec §9 deliverable 3) |
| **2.5.4** | One-time approved-action execution for **low-risk policies only** (§5.1 `low` tier of the 2.5 spec): `apply_compression_profile_once`, `apply_cache_policy_once`, `apply_delivery_strategy_once`, `suppress_suggestion_temporarily`. `medium` (`route_once_to_provider_model`) and `high` (`set_budget_warning_threshold`) tiers do NOT execute in 2.5.4. | Ratified (sub-spec §9 deliverable 4) |
| **2.6** | Limited enforcement spec — budget caps only. `flag_budget_risk` decisions become enforceable: block, downsize, or downgrade based on host config. Auto-routing remains locked off. Provider switching remains locked off. | Spec scope only; sub-spec to be written |
| **Classifier improvement** | Option B: LLM-assisted classification or refined keyword tables based on the Phase 0 baseline-report findings. Independent of Phase 2.5 / 2.6 sequencing. | Not started |
| **Production routing policy** | The Phase 2 spec §3 "future scope" — automatic provider rerouting beyond Phase 2.6's budget-cap downgrades. Prerequisites: stable Phase 2.6 baseline + ratified routing-decision contract. | Not started |

---

## 8. Future implementation entry points

When work resumes on a deferred phase, these are the canonical entry points:

| Deferred phase | First file to edit / create |
|---|---|
| 2.5.1 | `tokenpak/proxy/intent_confirmation.py` (new) — `PolicyConfirmationRequest` dataclass + `build_confirmation_request` + action-type validator |
| 2.5.1 | `tokenpak/proxy/intent_confirmation_telemetry.py` (new) — `intent_confirmations` SQLite store |
| 2.5.1 | `tokenpak/proxy/server.py` — call site after the Phase 2.4.1 suggestion-write block |
| 2.5.2 | `tokenpak/proxy/intent_doctor.py::collect_explain_last` — extend to surface confirmations linked by suggestion_id |
| 2.5.2 | `tokenpak/proxy/intent_policy_dashboard.py::collect_policy_dashboard` — add `confirmations` section |
| 2.5.2 | `tokenpak/dashboard/intent_policy.js` — render confirmation cards |
| 2.5.3 | `tokenpak/proxy/intent_confirmation_state.py` (new) — pending → approved / rejected / expired / canceled state machine |
| 2.5.3 | `tokenpak/cli/_impl.py::cmd_intent_confirm` (new) — `tokenpak intent confirm <id> --approve / --reject / --cancel` |
| 2.5.4 | `tokenpak/proxy/server.py` — at the dispatch path where compression / cache / delivery hooks fire, gate on approved low-risk confirmations |
| 2.6 | `tokenpak/proxy/intent_policy_engine.py` — extend `_select_type` to emit `flag_budget_risk` for over-threshold requests |
| Classifier improvement | `tokenpak/proxy/intent_classifier.py` + `tokenpak/proxy/intent_classifier_v1.py` (new) — preserves Phase 0 invariants |
| Production routing policy | New `tokenpak/proxy/intent_routing.py` + new spec |

Each entry point links back to the relevant spec for context. **None of these files should be created or edited without explicit approval.**

---

## 9. Do not implement without explicit approval

The following capabilities are intentionally **NOT** part of the Intent Advisory MVP and **MUST NOT** be implemented without explicit Kevin approval per phase:

- ❌ **Production automatic routing** — re-routing requests to a different provider/model than the caller declared, based on engine output. Even with Phase 2.5.4's one-time low-risk execution, the `route_once_to_provider_model` action class is in the medium-risk tier and is NOT executed in 2.5.4. Auto-routing remains a Phase 2.6+ scope.
- ❌ **Enforce mode** — `mode = enforce` in `~/.tokenpak/policy.yaml`. The Phase 2.4.3 loader rejects this value with a "not yet implemented" warning. The Phase 2.6 sub-spec (forthcoming) will narrow what this could mean (budget caps only). Until then, attempting to implement enforce-mode actions is out of contract.
- ❌ **Auto provider/model switching** — any code path that resolves a different provider/model based on engine output without an explicit per-request user gesture. Phase 2.5's confirmation handshake is the **only** authorized interrupt point; even there, execution is gated on the §4 confirmable allow-list.
- ❌ **Request mutation** outside the Phase 2.5.4 low-risk allow-list. Specifically: do NOT add code that edits `body`, `fwd_headers`, or `target_url` based on engine output without going through the 2.5.4 confirmation gate.
- ❌ **Confirmation execution** beyond the four low-risk action types (`apply_compression_profile_once`, `apply_cache_policy_once`, `apply_delivery_strategy_once`, `suppress_suggestion_temporarily`). The medium / high tiers (`route_once_to_provider_model`, `set_budget_warning_threshold`) require additional ratification before any executor lands.

These rules are not tested directly by code (since the absence-of-feature can't be tested) but ARE pinned by:

- The Phase 2.4.3 config-loader's three force-applied safety overrides.
- The structural "no dispatch primitives imported" tests across every Phase 2.x intent module.
- The Phase 0 invariant test that asserts no first-party adapter declares the gate label.
- The Phase 2.5 spec's §9 sub-sub-phase plan, which is normative.

Any PR that attempts to add behavior in the above list MUST cite a fresh, explicit Kevin approval in the PR description. Without that approval, the PR should not be opened.

---

## 10. Standards uplift opportunities

The Intent Advisory workstream produced two operator-facing surfaces and two design specs that arguably belong as numbered standards in `~/vault/02_COMMAND_CENTER/tokenpak-standards-internal/`. Open question for a future ratification cycle:

- A `24-intent-policy-engine.md` standard codifying the Phase 2 §6 always-on safety rules, the Phase 2.4.3 config-loader force-applied invariants, and the forbidden-phrase regex.
- A `25-intent-confirmation-mode.md` standard codifying the Phase 2.5 §6 lifecycle invariants and the §4 confirmable / excluded action-type lists.

These would slot in alongside `23-provider-adapter-standard.md` (the canonical capability-gated middleware-activation pattern this whole workstream rests on). Standards uplift is a **separate** ratification step and is out of scope for this milestone closeout.

---

## 11. Acknowledgements + provenance

The workstream began on 2026-04-25 with Kevin's Intent-0 directive and ended on 2026-04-26 with the Phase 2.5 spec ratification. Twelve PRs across approximately 36 hours of execution time, ten directives (Phase 0 → Phase 2.5 spec), zero behavioral regressions, two minor CI round-trips on tooling friction.

Origin: `~/vault/02_COMMAND_CENTER/proposals/2026-04-24-tokenpak-intent-layer-phase-0.md` — Sue's Intent Layer Phase 0 proposal (pre-existing). The Adj-1 + Adj-2 banner block at the top of that proposal documents the cross-reference adjustment to align with `23-provider-adapter-standard.md §4.3`.

Foundation pattern: every behavior change in this workstream rests on Standard #23's capability-gated middleware-activation pattern (`if "tip.X" in adapter.capabilities: ...`). Without that standard, the privacy + byte-fidelity + opt-in invariants would not have a load-bearing structural anchor.

Closeout: this milestone is **complete** at 2026-04-26 20:17:23Z (PR #55 merge timestamp). The next deferred phase requires explicit approval before code lands.
