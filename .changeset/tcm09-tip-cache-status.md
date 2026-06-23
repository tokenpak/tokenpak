---
---

CLI: add `tokenpak status --tip-cache` for compact TIP cache attribution.

The status command now exposes the TCM-09 four-lane attribution surface:
platform/client cache, TokenPak compression, TokenPak-managed cache, and
companion enrichment. JSON status output also includes the same `tip_cache`
payload for machine-readable audit and QA.
