---
---

Release-gate: public-API snapshot update for v1.30.0.

Additive change only. New public symbols: `tokenpak.proxy.execution_ledger`
(`begin_plan`, `complete_plan`, `fail_plan`, `lookup_plan`,
`recover_orphaned_plans`, `check_restart_recovered_failure`, `hash_request`,
`RESTART_FAILURE_REASON`) backing the proxy-restart recovery signal;
`tokenpak.agent.license` / `tokenpak.agent.license.validator`
(`LicenseTier`, `TIER_FEATURES`, `required_tier_for`) and
`tokenpak.agent.cli.commands.{metrics,serve}` re-export shims, now tracked
as canonical modules following the backbone forensic audit's subsystem
promotion; `tokenpak.services.optimization.cache_key.extract_model_features`
and `tokenpak.telemetry.query_dsl.{detect_provider,get_rates}`. No symbol
is removed or changed.
