# Intent Layer — Policy Preview (Phase 2.1)

> Phase 2.1 ships a **dry-run** intent policy engine. It evaluates every classified request against a pure-function policy and writes the decision to a separate SQLite table (`intent_policy_decisions`). **Nothing about the request changes.** No routing, no model swap, no body mutation, no header injection. The decisions are observational only — what a future opt-in suggest mode (Phase 2.4 per the spec) might recommend.

## Engine semantics

The engine is a pure function:

```python
from tokenpak.proxy.intent_policy_engine import (
    PolicyInput, evaluate_policy, load_default_config,
)

cfg = load_default_config()    # observe_only / dry_run=true / no auto-routing
inp = PolicyInput(...)         # built from IntentContract + request context
decision = evaluate_policy(inp, cfg)
```

It reads:

- The 10 canonical intent class + classifier confidence (Phase 0).
- Slot present/missing tuples (Phase 0).
- Catch-all reason (Phase 0).
- Resolved provider + model (request resolution).
- Adapter capabilities (Phase 0 §4.3 gate label, plus any cache/delivery labels).
- The provider's `live_verified` status (Standard #23 §6.4).

It returns a `PolicyDecision` with the 15 fields the directive enumerates: `decision_id`, `mode = "dry_run"`, `intent_class`, `confidence`, `action`, `recommended_provider`, `recommended_model`, `budget_action`, `compression_profile`, `cache_strategy`, `delivery_strategy`, `warning_message`, `requires_user_confirmation`, `decision_reason`, `safety_flags`.

## The seven actions

| Action | When emitted in 2.1 | Phase 2.x roadmap |
|---|---|---|
| `observe_only` | default; no heuristic matched | always available |
| `warn_only` | any safety flag tripped | always available |
| `suggest_route` | `allow_auto_routing=true` AND no safety flags | wired in 2.4+ |
| `suggest_compression_profile` | intent has a heuristic in the table AND no safety flags | wired in 2.4+ |
| `suggest_cache_policy` | adapter declares any `tip.cache.*` capability AND no safety flags | wired in 2.4+ |
| `suggest_delivery_policy` | intent has a delivery heuristic AND no safety flags | wired in 2.4+ |
| `flag_budget_risk` | reserved (not emitted in 2.1) | wired in 2.6 (limited enforce) |

Multiple suggestions may apply to one request; Phase 2.1 picks the **single** highest-priority action per the spec §3 ordering and populates only that field. Phase 2.2's explain extension surfaces lower-priority suggestions in the JSON / dashboard view.

## Safety rules (always on)

Four safety rules cannot be disabled by config:

1. **Low confidence** (`confidence < 0.65` by default) → `warn_only`, no routing-affecting action emitted.
2. **Catch-all** (`catch_all_reason is not None`) → `warn_only`.
3. **Missing required slots** (any `required_slots[i] in slots_missing`) → `warn_only`. Required-slot derivation from `slot_definitions.yaml` is plumbed in by Phase 2.2; Phase 2.1's engine accepts the set as a `PolicyInput.required_slots` tuple.
4. **`live_verified=False` provider** with `allow_unverified_providers=false` (default) → `warn_only`.

The `warning_message` field is built from a templated string + the safety-flag identifiers. **No caller-supplied substring ever appears in the message body.** The privacy contract is asserted end-to-end with the sentinel-substring pattern from Phase 1.

## Default config

Phase 2.1 ships without a config-file loader; the default is hard-wired:

```python
PolicyEngineConfig(
    mode="observe_only",
    dry_run=True,
    allow_auto_routing=False,
    allow_unverified_providers=False,
    low_confidence_threshold=0.65,
)
```

A future Phase 2.2 will read `~/.tokenpak/policy.yaml` per the spec §7 schema. Until then, every host runs the default — meaning every decision in 2.1 is either `observe_only` (when no heuristic matched) or a `suggest_*` (when a heuristic did, with no safety flag tripped) or `warn_only` (when a safety flag tripped). **No host can make a routing decision.**

## CLI

```bash
# Render the most recent decision (operator-readable plain text)
tokenpak intent policy-preview
tokenpak intent policy-preview --last      # alias for default behavior

# Same row, machine-readable JSON
tokenpak intent policy-preview --json
```

Returns a friendly "no decisions yet" message when the table is empty (fresh install).

## Telemetry

Decisions land in `~/.tokenpak/telemetry.db` `intent_policy_decisions`. Linked to the Phase 0 `intent_events` row via `contract_id`. Every column is either an id (`decision_id`, `request_id`, `contract_id`), a structured field from the decision, or a config-snapshot column. **No raw prompt text. No per-row content.**

Schema:

```sql
CREATE TABLE intent_policy_decisions (
    decision_id              TEXT PRIMARY KEY,
    request_id               TEXT,
    contract_id              TEXT,
    timestamp                TEXT NOT NULL,
    mode                     TEXT NOT NULL,
    intent_class             TEXT NOT NULL,
    intent_confidence        REAL NOT NULL,
    action                   TEXT NOT NULL,
    decision_reason          TEXT NOT NULL,
    safety_flags             TEXT NOT NULL,    -- JSON array of safety flag ids
    recommended_provider     TEXT,
    recommended_model        TEXT,
    budget_action            TEXT,
    compression_profile      TEXT,
    cache_strategy           TEXT,
    delivery_strategy        TEXT,
    warning_message          TEXT,
    requires_user_confirmation INTEGER NOT NULL,
    config_mode              TEXT,
    config_dry_run           INTEGER,
    config_allow_auto_routing INTEGER,
    config_allow_unverified_providers INTEGER,
    config_low_confidence_threshold REAL
);
```

## What NOT to infer yet

Phase 2.1 is dry-run. The decisions surface what an operator's future config might do. They are **not** authoritative for:

- **Routing decisions.** No request reroutes based on `recommended_provider`. That's Phase 2.4 with explicit opt-in.
- **Compression behavior.** The `compression_profile` field is observational; the actual compression hook still runs from the existing `tip.compression.v1` capability gate.
- **Cache policy.** Same — observational only.
- **Budget enforcement.** `flag_budget_risk` is reserved; budget caps land in Phase 2.6.
- **Required-slot derivation.** Phase 2.1's engine reads required slots from a `PolicyInput.required_slots` tuple supplied by the caller. Phase 2.2 will plumb this from `slot_definitions.yaml` automatically.

## Files

| Path | Purpose |
|---|---|
| `tokenpak/proxy/intent_policy_engine.py` | pure engine (`evaluate_policy`, action / reason / flag enums) |
| `tokenpak/proxy/intent_policy_telemetry.py` | SQLite store (`intent_policy_decisions` table) |
| `tokenpak/proxy/intent_policy_preview.py` | CLI render functions |
| `tokenpak/proxy/server.py` | call site in `_proxy_to_inner` (after Phase 0 contract write) |
| `tokenpak/cli/_impl.py::cmd_intent_policy_preview` | `tokenpak intent policy-preview` entry point |
| `~/.tokenpak/telemetry.db` `intent_policy_decisions` | source data |
| `docs/internal/specs/phase2-intent-policy-engine-spec-2026-04-25.md` | the unified Phase 2 design spec |
| `docs/reference/intent-policy-preview.md` | this document |

## Standards / cross-references

- Phase 2 spec: `docs/internal/specs/phase2-intent-policy-engine-spec-2026-04-25.md` (sub-phase 2.1).
- Standard #23 §4.3 — capability gate (still load-bearing for any wire-side header emission a later sub-phase introduces).
- Standard #23 §6.4 — `live_verified` semantics (driver of safety rule 4).
- Architecture §7.1 — prompt-locality rule (driver of the privacy contract).
- Phase 0 docs: `docs/reference/intent-layer-phase-0.md`.
- Phase 1 docs: `docs/reference/intent-reporting.md`.
- Phase 1.1 docs: `docs/reference/intent-dashboard.md`.
