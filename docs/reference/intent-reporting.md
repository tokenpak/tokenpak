# Intent Layer — Reporting (Phase 1)

> Phase 1 is **observation only**. The reporting command is read-only over the local `intent_events` SQLite table written by Phase 0. It surfaces aggregations, never raw prompts. No user-facing behavior changes; no classifier changes.

## How to read the report

```bash
# Default — last 14 days (matches the proposal's observation window)
tokenpak intent report

# Custom window
tokenpak intent report --window 7d
tokenpak intent report --window 30d

# All rows, no window
tokenpak intent report --window 0d

# Machine-readable JSON
tokenpak intent report --json
```

The human-readable layout walks operator attention through the same questions the proposal §6 measurement plan calls out, in this order:

1. **Window + telemetry-store path + total count** — sets the scope so the rest of the numbers have context.
2. **Intent class distribution** — count and average confidence for each canonical intent. Confidence here is the rule-based classifier's normalized keyword weight (see "What `rule_based_v0` means" in `intent-layer-phase-0.md`); it is **not** a probability.
3. **Low confidence count** — rows below the classifier's `CLASSIFY_THRESHOLD` (0.4 by default). Concentrated low confidence is a signal that the keyword table needs broadening, NOT that the prompts are ambiguous.
4. **Wire-emission posture** — three counters: `tip_headers_emitted`, `tip_headers_stripped`, `telemetry-only`. The latter two are the same number expressed two ways; both are present so reports translate cleanly into either framing.
5. **Top missing slots** — the slot names most often required-but-missing. Use this to decide whether a slot definition (`tokenpak/agent/compression/slot_definitions.yaml`) needs broader keywords or whether the slot is genuinely optional in real traffic.
6. **Top catch-all reasons** — when classification fell back to `query`, the reason. The five values are documented in `intent_classifier.py::CATCH_ALL_REASONS`.
7. **Adapters eligible / blocking** — split of registered adapters by whether they declare `tip.intent.contract-headers-v1` (per Standard #23 §4.3). Phase 0 default has every first-party adapter in *blocking*; opt-in is gated on this baseline.
8. **Recommended review areas** — heuristic flags drawn from the aggregations. Each line names something to look at, not something to do.

## What the 2-week observation window means

The default `--window 14d` mirrors the proposal's recommended observation duration. It is a **measurement budget**, not a quality gate. Three signals govern when to extend or cut short:

- **Confidence histogram stability** — if the shape is still drifting late in week 2, extend (`--window 28d` or `--window 0d`). If the shape stabilizes by day 7, the data is enough.
- **Catch-all dominance** — if `intent_class = "query"` dominates (> 50 %), the canonical-intent set or keyword table needs revision before Intent-1 lifts the subsystem. Cut the window short, fix, restart.
- **Volume floor** — proposal §6 sets 500 classifications as the meaningful-statistics floor. Below that, distribution shape is noise.

Nothing in TokenPak toggles based on elapsed window time. The window scopes when you run the **baseline report** that informs the Intent-1 go/no-go decision (proposal §4 deliverable 5). Until then, Phase 0 + 1 are pure observation.

## What "low confidence" and "catch-all" mean

**Low confidence** = a row with `intent_confidence < CLASSIFY_THRESHOLD` (0.4 by default). The threshold is symmetric with the existing `intent_policy.CONFIDENCE_THRESHOLD` so an upstream classification that survives this gate also survives the downstream policy gate. A high low-confidence count usually means:

- The keyword table is missing canonical phrases that real traffic uses (fix the table).
- Real traffic is genuinely ambiguous between intents (the `query` catch-all picks it up).

A high low-confidence count is **not** evidence that prompts are bad or that the user is doing something wrong.

**Catch-all** = `intent_class = "query"`. Not a positive classification; a deliberate landing pad. The catch-all reason explains *why* the classifier landed there:

- `empty_prompt` — prompt was zero or whitespace-only chars.
- `prompt_too_short` — prompt below 3 chars after strip.
- `keyword_miss` — every intent's keyword set scored zero.
- `confidence_below_threshold` — top-scoring intent fell under 0.4.
- `slot_ambiguous` — reserved (not emitted in `rule_based_v0`).

## Why telemetry-only is normal unless the adapter declares the capability

This is the load-bearing default-off invariant from PR #44 / PR #45. The proxy attaches the five wire headers (`X-TokenPak-Intent-Class`, `Confidence`, `Subtype`, `Contract-Risk`, `Contract-Id`) **only when the resolved request adapter declares `tip.intent.contract-headers-v1`** in its `FormatAdapter.capabilities` class attribute, per Standard #23 §4.3.

In Phase 0 / Phase 1, no first-party adapter declares this label by default. Every classification stays in local telemetry only — that is the intended posture, not a bug. The report's "Adapters blocking TIP intent headers" section will list every registered adapter; this is normal.

When an adapter author opts in (after the baseline report identifies where the headers add value), that adapter moves from "blocking" to "eligible" and its requests start emitting on the wire. Until then, no wire emission means no risk of breaking byte-fidelity for non-TIP providers.

## What NOT to infer yet

Phase 1 is observation. The following inferences are **out of scope** until later phases:

- **Cost or latency conclusions per intent.** The Phase 0 telemetry row carries `tokens_in / tokens_out / latency_ms` as best-effort optional fields; they are not authoritative for cost analysis. The Intent-1 schema lift will bind them to the request-event row formally.
- **Routing decisions.** Phase 1 does not change any routing. The Intent-2 clarification gate is the first user-facing change; that work has its own proposal.
- **Recipe selection.** Recipe-driven behavior lands in Intent-3.
- **Pro/OSS gating.** Intent-0 / Intent-1 are pure OSS. The split decision is queued for Intent-4 (intent memory).
- **Prompt-side cache keying.** Cache keys are not yet derived from intent or contract. That is Intent-3+ work.
- **Quality assertions about the classifier.** The rule-based classifier is deliberately simple (Option A in the proposal). The baseline report will tell us whether to lift to LLM-assisted classification (Option B) in Intent-1; do not draw conclusions until the report is in.

## Privacy

The reporting layer is read-only against `~/.tokenpak/telemetry.db`. The `intent_events` table only stores the `raw_prompt_hash` (sha256 hex digest); raw prompt text never enters the table per Architecture §7.1. The reporting paths assert this end-to-end (`tests/test_intent_report.py::TestPrivacyContract`). No prompt content, no secrets, no upstream traffic crosses the report boundary.

## Files

| Path | Purpose |
|---|---|
| `tokenpak/proxy/intent_report.py` | aggregations + renderers (this PR) |
| `tokenpak/cli/_impl.py::cmd_intent_report` | `tokenpak intent report` entry point |
| `tokenpak/proxy/intent_classifier.py` | classifier (Phase 0) |
| `tokenpak/proxy/intent_contract.py` | contract + telemetry store (Phase 0) |
| `tokenpak/proxy/intent_doctor.py` | per-request diagnostics (Phase 0.1) |
| `~/.tokenpak/telemetry.db` `intent_events` | source data, written by Phase 0 |
| `docs/reference/intent-layer-phase-0.md` | the "what / why / when" doc |
| `docs/reference/intent-reporting.md` | this document |

## Proposal

Full Intent-0 proposal: `~/vault/02_COMMAND_CENTER/proposals/2026-04-24-tokenpak-intent-layer-phase-0.md`. The Adj-1 + Adj-2 banner block at the top documents the 2026-04-25 cross-reference adjustment that aligned the header-emission gate with `23-provider-adapter-standard.md §4.3`.
