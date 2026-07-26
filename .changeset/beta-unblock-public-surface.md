---
---

Release-gate: ratchet the public-API snapshot for the beta-surface and
measured-reporting work, and for the two-tier licence collapse.

**Added.** `tokenpak.services.preview` — the real preview path and its result
contract (`PreviewResult`, `PreviewBlock`, `PreviewProvenance`, `PreviewState`,
`PreviewInvariantError`, `TOKENIZER_ID`, `run_preview`), replacing a simulation
that computed output as a word count times a constant.
`tokenpak.core.runtime.lifecycle` — the single lifecycle observer
(`snapshot`, `read_pid`, `write_pid`), replacing per-command notions of what
"running" meant. Plus `tokenpak.telemetry.query_dsl.TelemetryUnavailable` and
`tokenpak.telemetry.model_analytics.default_log_path`.

**Removed — six symbols, each with a ruling.**

`tokenpak.licensing.TIER_TEAM`, `tokenpak.licensing.TIER_ENTERPRISE` — the tier
ladder collapses to `free < pro`. No licence above Pro could be issued, so every
feature gated above Pro was unreachable by any real licence holder. Both names
were introduced one minor earlier; the ladder is now read from
`LicenseTier.ladder()` and the names from `tokenpak.licensing.known_tiers()`
rather than being hardcoded.

`tokenpak.cli.commands.preview.cmd_preview`,
`tokenpak.cli.commands.preview.register_preview` — the module is deleted rather
than rewired. It imported `Compressor` from `compression.core`, which does not
exist, so every call raised `ImportError`. Nothing that worked has been removed.

`tokenpak.cli.commands.license_cmd.DEFAULT_UPGRADE_URL`,
`tokenpak.cli.commands.status.DEFAULT_UPGRADE_URL` — the default pointed at a
page returning HTTP 404, echoed from a footer on every `tokenpak status` run.
There is deliberately no replacement default: `resolve_upgrade_url()` returns
empty unless an operator sets `TOKENPAK_UPGRADE_URL`, so a destination is never
fabricated.
