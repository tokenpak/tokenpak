---
---

Release-gate: reconcile the public-API snapshot with the v1.12.0 mainline.

The v1.12.0 release cut bumped `package_version` but did not regenerate the
snapshot symbol list, so the release run failed closed at the API-snapshot
check on seventeen additive symbols introduced by the receipt-only launch
work and the user-recipe overlay loader: the new
`tokenpak.companion.codex.accounting` module (`SCHEMA`, `build_receipt`,
`empty_usage`, `merge_usage`, `model_from_args`, `redact_argv`,
`usage_from_event`, `usage_from_json_line`, `utc_now`, `write_receipt`),
their `tokenpak.companion.codex.launcher` re-exports, and
`tokenpak.compression.recipes.user_recipes_dir`.

Regenerated in a clean CI-parity environment
(`pip install -e ".[dev,full,serve,telemetry,tokens,otel]"` + `python-multipart`);
`gen_api_snapshot.py --check` passes against the regenerated file. Additions
only — no public symbols removed; no runtime code changes.
