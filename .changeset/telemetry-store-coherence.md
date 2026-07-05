---
---

fix(telemetry): single DB resolver, uniqueness keys, remove phantom-schema modules.

Telemetry store coherence:

- **Single telemetry.db resolver.** Every open of `telemetry.db` now routes
  through `tokenpak.core.paths.get_db_path("telemetry.db")`. Removes six
  hardcoded `~/.tokenpak/telemetry.db` paths and the repo-root
  `Path(__file__)/…/telemetry.db` defaults in `telemetry.api`,
  `telemetry.query_dsl` and `telemetry.milestones`. `TOKENPAK_TELEMETRY_DB`
  is the canonical env override; `TOKENPAK_DB_PATH` is honored as a
  deprecated alias (a one-time warning is logged when only the alias is set).

- **Phantom-schema modules removed.** Deletes non-functional modules that
  queried `events` / `rollups` tables no code ever creates and had zero
  callers: `telemetry.integrity.reconciliation`,
  `telemetry.integrity.anomalies`, `telemetry.operational.pruning`,
  `telemetry.operational.health`, and the dead duplicate
  `telemetry.monitoring.monitor`. The operational API keeps only its
  functional RBAC surface; its always-failing `/metrics`, `/v1/health` and
  `/v1/admin/*` endpoints (broken imports + phantom tables) are removed.
  A schema-conformance test now walks SQL strings in the telemetry package
  and fails if any queried table has no CREATE statement in the codebase.

- **Uniqueness keys (additive migrations).** `tp_spend` gains
  `UNIQUE(request_id)` with an upsert (`INSERT OR REPLACE`) so re-recording
  a request cannot double-count spend; `record_spend` now rejects empty
  request ids instead of fabricating collision-prone timestamp ids.
  `tp_pricing` gains `UNIQUE(version, provider, model)` with
  `INSERT OR IGNORE` seeding so the cross-process COUNT-then-seed race
  cannot duplicate pricing rows. Both migrations dedupe pre-existing
  duplicate rows (newest wins) inside one transaction before creating the
  index.

- **TelemetryDB thread safety.** `TelemetryDB` / `TelemetryDBBase` replace
  the single shared `check_same_thread=False` connection with per-thread
  connections (thread-local factory), so concurrent writers no longer
  interleave commits. `insert_trace` now writes one trace's
  event/usage/cost/segment rows in ONE transaction, and the ingest pipeline
  no longer swallows usage/cost write failures while reporting success.

- **CWD-independent DB paths.** `BlockRegistry` and `RoutingLedger` default
  paths are anchored to the home config dir (with a warned legacy
  CWD-relative fallback read), so proxy and CLI agree on one file
  regardless of working directory. Routing-ledger writes catch
  `sqlite3.OperationalError`, count and log it, and return a sentinel
  instead of crashing the caller.

Public API: the snapshot is updated for the removed modules/symbols plus
one addition (`tokenpak.core.registry.LEGACY_CWD_DB_PATH`).

removes-public-symbol: tokenpak.telemetry.integrity.reconciliation.*
removes-public-symbol: tokenpak.telemetry.integrity.anomalies.*
removes-public-symbol: tokenpak.telemetry.operational.health.*
removes-public-symbol: tokenpak.telemetry.operational.pruning.*
removes-public-symbol: tokenpak.telemetry.monitoring.monitor.*
removes-public-symbol: tokenpak.telemetry.monitoring.Monitor
removes-public-symbol: tokenpak.telemetry.operational.api.{CONFIG_PATH,DB_PATH,HealthChecker,METRICS,Permission,PruneJob,admin_config,admin_prune,admin_stats,admin_vacuum,health,health_checker,load_retention_config,metrics,prune_job,require_auth,require_permission,retention_config}
