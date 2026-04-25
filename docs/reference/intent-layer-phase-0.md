# Intent Layer — Phase 0

> Phase 0 is **telemetry-only**. The classifier observes; nothing about your request body, routing, caching, or response changes. This document explains what happens, when wire headers are emitted, what stays local, and how to read the 2-week observation window.

## What Phase 0 does

On every request that flows through the proxy, the rule-based classifier runs upstream of dispatch and produces an `IntentContract`:

| Field | Phase 0 source |
|---|---|
| `contract_id` | ULID-shaped opaque id (ms timestamp + random tail), 1:1 with `request_id` |
| `intent_class` | one of the 10 canonical intents from `tokenpak.proxy.intent_policy.CANONICAL_INTENTS` |
| `confidence` | `[0.0, 1.0]` — max matched-keyword weight for the winning intent |
| `subtype` | reserved (always `None` in Phase 0; Intent-1 lifts the typed-class taxonomy) |
| `risk` | `low \| medium \| high` — heuristic, see `derive_risk` |
| `slots_present` / `slots_missing` | from `SlotFiller` over `slot_definitions.yaml` |
| `intent_source` | `rule_based_v0` (see below) |
| `catch_all_reason` | populated when classification falls through to `query` |
| `raw_prompt_hash` | sha256 hex of the concatenated user-message text — dedup key only |

Every contract becomes one row in `~/.tokenpak/telemetry.db` `intent_events`.

## When TIP intent headers are emitted on the wire

The proxy attaches the five wire headers — `X-TokenPak-Intent-Class`, `X-TokenPak-Intent-Confidence`, `X-TokenPak-Intent-Subtype`, `X-TokenPak-Contract-Risk`, `X-TokenPak-Contract-Id` — **only when the resolved request adapter declares the capability**:

```python
# tokenpak/proxy/server.py — Standard #23 §4.3 capability gate (verbatim)
if request_adapter is not None and "tip.intent.contract-headers-v1" in request_adapter.capabilities:
    attach_intent_headers(request, contract)
# else: skip; headers stay in local telemetry only
```

Adapters declare capabilities as a class attribute:

```python
class MyAdapter(FormatAdapter):
    capabilities = frozenset({
        "tip.intent.contract-headers-v1",
        # …
    })
```

In Phase 0, **no first-party adapter declares this label by default**. First-party opt-in is gated on the Phase 0 baseline report. The `tests/test_intent_layer_phase0.py::TestCapabilityGate::test_no_first_party_adapter_declares_label_in_phase_0` regression test enforces this contract.

### Why headers stay local unless the adapter declares the capability

The default-off posture preserves two architecture invariants:

1. **Byte-fidelity for non-TIP providers** (Architecture §5.1). Some upstreams care about request-body bytes for billing routing or signature verification; injecting unsolicited TIP headers risks observable side-effects.
2. **Explicit opt-in over implicit support** (Standard #23 §4.2). An adapter that doesn't declare a label MUST NOT have that label's behavior applied. Telemetry stays local until the adapter author confirms the upstream tolerates the new headers.

The contract is symmetric: declaring without publishing is also a violation. The proxy publishes `tip.intent.contract-headers-v1` in `tokenpak.core.contracts.capabilities.SELF_CAPABILITIES_PROXY`, mirrored in `tokenpak/manifests/tokenpak-proxy.json` and `tokenpak/registry`'s `capability-catalog.json`.

## What `rule_based_v0` means

The Phase 0 classifier is **deterministic, regex/keyword-driven, no LLM** (Option A in the proposal). Every classification is reproducible from the raw prompt + the keyword table at `tokenpak/proxy/intent_classifier.py::_KEYWORD_PATTERNS`.

The `intent_source = "rule_based_v0"` field on every telemetry row is a deliberate marker:

- It lets a future Intent-1 LLM-assisted classifier (Option B) backfill rows under a different `intent_source` value without invalidating the Phase 0 baseline.
- It makes A/B comparisons trivial: filter `intent_events` by `intent_source` to compare distributions.
- It makes upgrades auditable: if classifier behavior changes in v1, the source value increments to `rule_based_v1` (or whatever) so old rows aren't conflated.

**Do not treat `rule_based_v0` confidence values as probabilistic.** They are normalized keyword weights, not posteriors. A confidence of `0.8` means "the canonical keyword for `summarize` matched"; it does not mean "an 80 % chance the intent is `summarize`". Phase 0's measurement plan (proposal §6) treats the confidence histogram as a tuning input, not as a probability.

## How to read the 2-week observation window

The proposal sets the default observation window at **2 weeks of live traffic** on Kevin's fleet. The window is a **measurement budget**, not a quality gate. Three things govern when to extend or cut short:

1. **Does the confidence histogram stabilize?** If the shape drifts late in week 2, extend to 4 weeks. If it's stable by day 7, the data is enough.
2. **Is `intent_class = "query"` (the catch-all) > 50 %?** If so, the canonical-intent set or the keyword table needs revision before Intent-1 lifts the subsystem. Cut the window short, fix the keyword set, restart.
3. **Is volume < 500 classifications?** Below that threshold, distribution statistics aren't meaningful. Extend until ≥ 500 rows accumulate.

The window has no effect on user-facing behavior. Nothing toggles based on the window's elapsed time — Phase 0 is observation-only. The window simply scopes when to run the **baseline report** (proposal §4 deliverable 5) that informs the Intent-1 go/no-go decision.

## Operator commands

```bash
# Diagnostic snapshot — classifier active? proxy publishing the label?
# which adapters declare the capability? would headers emit on the wire?
tokenpak doctor --intent
tokenpak doctor --intent --json    # machine-readable

# Full row dump for the most recent classification
tokenpak doctor --explain-last
tokenpak doctor --explain-last --json
```

## Privacy

Raw prompt text never enters the telemetry DB. Only the `raw_prompt_hash` (sha256 hex digest) is stored. Per-request prompts stay in the local request log under `~/.tokenpak/` (subject to `TOKENPAK_LOG_ENABLED` and `telemetry.store_prompts` per Architecture §7.1). No prompt data leaves the machine.

## Files

| Path | Purpose |
|---|---|
| `tokenpak/proxy/intent_classifier.py` | rule-based classifier (Option A) |
| `tokenpak/proxy/intent_contract.py` | `IntentContract`, telemetry store, `attach_intent_headers` |
| `tokenpak/proxy/intent_doctor.py` | doctor / explain renderers (Phase 0.1) |
| `tokenpak/proxy/intent_policy.py` | existing policy table + 10 canonical intents |
| `tokenpak/agent/compression/slot_filler.py` | slot extractor |
| `tokenpak/agent/compression/slot_definitions.yaml` | per-intent slot schemas |
| `tokenpak/manifests/tokenpak-proxy.json` | published TIP capability set |

## Standards

- `23-provider-adapter-standard.md §4.3` — capability-gated middleware activation, the canonical pattern this layer implements.
- `01-architecture-standard.md §5.1` — byte-fidelity rule for non-TIP providers.
- `01-architecture-standard.md §7.1` — prompt-locality rule (raw prompts never cross the machine boundary).
- `00-product-constitution.md §13` — TokenPak as TIP-1.0 reference implementation.

## Proposal

Full Intent-0 proposal (Phase 0 scope, decisions, measurement plan, risk + mitigation): `~/vault/02_COMMAND_CENTER/proposals/2026-04-24-tokenpak-intent-layer-phase-0.md` (Adj-1 + Adj-2 banner block at top documents the 2026-04-25 cross-reference adjustment to align with Standard #23).
