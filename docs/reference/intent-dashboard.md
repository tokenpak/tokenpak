# Intent Layer — Dashboard / Read-Model (Phase 1.1)

> Phase 1.1 is **observation only**. It surfaces the Phase 1 reporting layer through a stable read-model + JSON API + dashboard panel. No classifier behavior changes. No routing changes. No raw prompt content leaves the local store.

## Surface

Three layers, all backed by the same query function (`build_report` from Phase 1):

| Layer | Where | Audience |
|---|---|---|
| Python read-model | `tokenpak.proxy.intent_dashboard.collect_dashboard` | programmatic callers, embeds |
| HTTP API | `GET /api/intent/report?window=Nd` | dashboard UI, third-party tools, scripts |
| Dashboard panel | `tokenpak/dashboard/index.html` (Intent Layer section) | operator browsing the local UI |

The wire shape is the same across all three. Schema version stamp: `intent-dashboard-v1` (carried as `metadata.schema_version`).

## API contract

```http
GET /api/intent/report
GET /api/intent/report?window=14d
GET /api/intent/report?window=7d
GET /api/intent/report?window=0d            # 0d / "" / missing → defaults differ; see below
```

Response: `200 OK` with `Content-Type: application/json` carrying the payload shape below. Errors:

- `400 bad_request` — malformed `window` (anything other than `Nd`, including the literal `"all"` or `"forever"`).
- `500 intent_dashboard_failed` — internal failure during aggregation. Best-effort path; should be rare.

Read-only. The endpoint never writes to the telemetry store, never invokes a provider, never alters adapter state.

### Window semantics

The dashboard surface and the CLI surface intentionally differ on the **default** window:

| Surface | Missing / empty | `0d` |
|---|---|---|
| `tokenpak intent report` (CLI) | `None` (read every row) | `None` (read every row) |
| `GET /api/intent/report` | **`14d` (default observation window)** | `None` (read every row) |
| `collect_dashboard()` Python | caller-specified | `None` (caller passed `window_days=None`) |

The dashboard default is 14d so a naive `curl /api/intent/report` doesn't accidentally surface multi-year history. Pass `?window=0d` explicitly when you want all rows.

### Payload shape

```jsonc
{
  "metadata": {
    "schema_version": "intent-dashboard-v1",
    "phase": "intent-layer-phase-1.1",
    "observation_only": true,
    "window_days": 14,
    "window_cutoff_iso": "2026-04-12T17:31:39",
    "telemetry_store_path": "/home/.../telemetry.db",
    "low_confidence_threshold": 0.4
  },
  "cards": {
    "total_classified":             { "value": <int> },
    "intent_class_distribution":    {
      "items": [
        { "intent_class": "summarize", "count": 12, "pct": 60.0, "avg_confidence": 0.93 }
      ]
    },
    "average_confidence":           { "value": <float, weighted by class volume> },
    "low_confidence_count":         { "value": <int>, "pct_of_total": <float>, "threshold": 0.4 },
    "catch_all_reason_distribution": {
      "items": [{ "catch_all_reason": "empty_prompt", "count": 3, "pct": 15.0 }]
    },
    "top_missing_slots":            {
      "items": [{ "slot": "target", "count": 8, "pct": 40.0 }]
    },
    "tip_headers_emitted_vs_telemetry_only": {
      "tip_headers_emitted": <int>,
      "telemetry_only": <int>,
      "tip_headers_stripped": <int>,           // mirror of telemetry_only
      "emitted_pct": <float>,
      "telemetry_only_pct": <float>
    },
    "adapters_eligible":            { "items": [...], "count": <int> },
    "adapters_blocking":            { "items": [...], "count": <int> }
  },
  "operator_panel": {
    "most_common_missing_slots":                  [...],
    "most_common_catch_all_reasons":              [...],
    "adapters_eligible_for_tip_headers":          [...],
    "adapters_requiring_capability_declaration":  [...],
    "recommended_review_areas":                   [<string>, ...]
  }
}
```

Pre-computed percentages are floats rounded to 1 decimal place (cards) or 4 decimal places (`average_confidence` weighted mean). Underlying counts remain available for clients that want to compute their own ratios.

## How dashboard metrics map to `tokenpak intent report`

Every card and operator-panel item in the dashboard comes from the same `build_report` query as the CLI's `tokenpak intent report --json`. The CLI is the ground truth; the dashboard reshapes for UI rendering and adds pre-computed percentages. If a number ever differs between the two for the same window, the report is authoritative.

| Dashboard card | Maps to (CLI / `--json` field) |
|---|---|
| Total Classified | `total_classified` |
| Intent class distribution | `intent_class_distribution` × `avg_confidence_by_class` |
| Average confidence | volume-weighted mean of `avg_confidence_by_class` × counts |
| Low confidence | `low_confidence_count` + `low_confidence_threshold` |
| Catch-all reason distribution | `catch_all_reason_distribution` |
| Top missing slots | `top_missing_slots` |
| TIP headers emitted vs telemetry-only | `tip_headers_emitted` + `tip_headers_stripped` (= `telemetry_only`) |
| Adapters eligible | `adapters_eligible` |
| Adapters blocking | `adapters_blocking` |

## Why telemetry-only is normal

In Phase 0/1/1.1, **no first-party adapter declares `tip.intent.contract-headers-v1`**. The proxy attaches the five wire headers (`X-TokenPak-Intent-Class`, `Confidence`, `Subtype`, `Contract-Risk`, `Contract-Id`) only when the resolved request adapter declares this capability per Standard #23 §4.3. With every adapter in `adapters_blocking`, every request stays in local telemetry only. The "TIP Headers Emitted" card reading 0 is the **expected default**, not a bug.

When an adapter author opts in (after the baseline report identifies where the headers add value), that adapter moves to `adapters_eligible` and its requests start emitting. The dashboard card flips on automatically.

## What "low confidence" means

`low_confidence_count` is the count of rows where `intent_confidence < low_confidence_threshold` (0.4 by default — symmetric with the existing `intent_policy.CONFIDENCE_THRESHOLD`). The Phase 0 classifier stamps `intent_source = "rule_based_v0"` on every row; confidence is a normalized keyword weight, **not** a probability. A prompt classified at 0.9 means "the canonical keyword for that intent matched"; it does NOT mean "90 % chance the intent is X".

A high low-confidence count is usually a signal that the keyword table needs broader phrases, not that the prompts are ambiguous.

## What NOT to infer yet

Phase 1.1 is observation. The dashboard surface is **NOT** authoritative for:

- **Cost or latency conclusions per intent.** The Phase 0 row carries `tokens_in / tokens_out / latency_ms` as best-effort optional fields; they are not bound to the request-event row formally until Intent-1.
- **Routing decisions.** Phase 1.1 changes no routing.
- **Recipe selection.** Recipe-driven behavior lands in Intent-3.
- **Pro/OSS gating.** Intent-0 / Intent-1 / Intent-1.1 are pure OSS.
- **Prompt-side cache keying.** Cache keys are not derived from intent or contract yet.
- **Quality assertions about the classifier.** Phase 0 chose Option A (rule-based). The baseline report informs the Intent-1 go/no-go on Option B.

## Why Phase 1.1 is observation-only

The proposal sets a 2-week (`14d`) baseline-observation window before any user-facing behavior change can be considered. Phase 1.1 surfaces aggregations through more reach surfaces (Python, HTTP, UI) so the operator can read the same baseline from any vantage. None of those surfaces makes a routing or rewrite decision; that is reserved for Intent-2 onward.

## Privacy

The Python read-model, the HTTP endpoint, and the dashboard panel all share one privacy contract:

- Raw prompt text NEVER enters the read path.
- The `raw_prompt_hash` (sha256 hex digest) MAY appear in `intent_events` rows, but the dashboard surface does NOT include it in the payload.
- All percentages and counts are aggregations across the window — no per-request identifiers leave the response except the adapter class names (which are public symbols).

The privacy contract is asserted end-to-end via sentinel-substring tests:

- `tests/test_intent_report.py::TestPrivacyContract` — Phase 1 CLI report
- `tests/test_intent_dashboard.py::TestPrivacyContract` — Phase 1.1 dashboard

## Files

| Path | Purpose |
|---|---|
| `tokenpak/proxy/intent_dashboard.py` | read-model (`collect_dashboard`, schema version stamp) |
| `tokenpak/proxy/server.py::do_GET` | `/api/intent/report` HTTP endpoint |
| `tokenpak/dashboard/index.html` | "Intent Layer" panel — cards + tables + operator review areas |
| `tokenpak/dashboard/intent.js` | UI fetch + render |
| `tokenpak/proxy/intent_report.py` | Phase 1 query layer (reused) |
| `~/.tokenpak/telemetry.db` `intent_events` | source data, written by Phase 0 |
| `docs/reference/intent-layer-phase-0.md` | Phase 0 fundamentals |
| `docs/reference/intent-reporting.md` | Phase 1 CLI report |

## Cross-references

- Standard #23 §4.3 — the canonical capability-gated middleware-activation pattern.
- Architecture §5.1 — byte-fidelity rule for non-TIP providers.
- Architecture §7.1 — prompt-locality rule.
- Constitution §13 — TokenPak as TIP-1.0 reference implementation.
- Intent-0 proposal: `~/vault/02_COMMAND_CENTER/proposals/2026-04-24-tokenpak-intent-layer-phase-0.md` (Adj-1 + Adj-2 banner block at top).
