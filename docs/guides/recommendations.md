# `tokenpak recommendations`

Telemetry-driven recommendations: a ranked, evidence-backed view of what TokenPak found in
your local telemetry store and the next concrete action to take.

## Quickstart

```bash
tokenpak recommendations
```

By default this looks at the last 24 hours of telemetry from `~/.tokenpak/telemetry.db`
(or `$TOKENPAK_TELEMETRY_DB`) and emits a grouped report. If your store is empty or
brand new, the command prints a "no recommendations" line and exits cleanly — there is
no required setup beyond running real traffic through the proxy.

## Flags

| Flag | Description |
|---|---|
| `--window 24h` | Rolling window. Accepts `Nh` (hours, default) or `Nd` (days). |
| `--model <name>` | Restrict recommendations to a single model. |
| `--platform <name>` | Restrict to a single platform. Matches against `agent_id` and the event payload. |
| `--json` | Emit machine-readable JSON instead of the human report. |
| `--db-path <path>` | Override the telemetry DB path. |

Examples:

```bash
tokenpak recommendations --window 7d
tokenpak recommendations --window 24h --model gpt-5.5
tokenpak recommendations --json | jq '.recommendations[].id'
```

## What rules ship today

| Rule id | Severity | Triggers when |
|---|---|---|
| `cache.zero-lookups` | high | At least 5 requests in the window have **0 total cache-read tokens** — TokenPak proxy is taking traffic but the semantic-cache stage isn't producing hits. |
| `attribution.high-unattributed` | high (≥30%) / medium (≥10%) | A meaningful fraction of traffic can't be attributed to a savings source. Reads `tp_savings_attribution` (TIP-06) when present, falls back to `tp_usage.usage_source`. |
| `errors.high-rate` | high (≥10%) / medium (≥3%) | A noticeable fraction of recent requests left the proxy in a non-`ok` state. |
| `cache.schema-instability` | medium | At least 5 cache misses in the window were attributed to `tool_schema_digest_mismatch` — likely tool-schema normalization drift. |
| `pricing.missing:<model>` | tracking | A model was seen in traffic but no pricing entry exists in `tp_pricing_catalog` or the bundled pricing helper. |

Rules that depend on optional tables (`tp_savings_attribution`, `tp_cache_miss_reasons` —
both populated by the TIP-06 attribution-v2 pipeline) silently skip when those tables
aren't present, so this command always works even on telemetry stores that predate the
attribution-v2 migration.

## JSON shape

```json
{
  "window_hours": 24,
  "generated_at_utc": "2026-04-29T12:34:56Z",
  "filters": {"model": null, "platform": null},
  "count": 2,
  "recommendations": [
    {
      "id": "cache.zero-lookups",
      "severity": "high",
      "title": "0 cache reads recorded across 12 requests in last 24h",
      "evidence": {"n_traces": 12, "total_cache_read_tokens": 0, "window_hours": 24},
      "action": "Enable proxy semantic-cache stage for safe routes (status_check, configuration_inspection, summarization). See `tokenpak/services/optimization/`.",
      "expected": "TokenPak-managed cache reads on repeated safe-route traffic."
    }
  ]
}
```

The schema is additive within a major version: fields are added, never removed or
renamed. Treat unknown fields as forward-compat metadata and ignore them.

## Privacy

The engine reads only aggregate columns (`tp_events.{provider,model,status,ts}`,
`tp_usage.{cache_read,usage_source,...}`, `tp_pricing_catalog`, and the optional
TIP-06 tables). It does **not** read raw prompts, responses, or capsule contents,
and it does not require raw-prompt storage to produce useful output.

## Where this lives

- Engine: `tokenpak/telemetry/recommendations.py`
- CLI command: `tokenpak/cli/commands/recommendations.py`
- Architecture layer: telemetry (Level 2 per Standard 01 §2). The engine is read-only;
  it is invoked by an entrypoint (CLI) and never by `services/` or `proxy/`.
