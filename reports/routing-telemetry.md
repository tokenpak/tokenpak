# Routing telemetry implementation report

**Overall: complete under the clarified offline-parity acceptance criteria.**
The five source changes are implemented, all 59 added regression cases pass,
and no matched base test regresses. The required offline suite stops on the
same 11 network-dependent collection errors on base and candidate; this is
not a full-suite passing receipt. Remaining failures are preserved below.
Independent QA, native-client qualification, release and installation remain
separate work. No push, publish, install, host configuration or service change
was performed.

Branch: `lane-T1-routing-telemetry`.
Base: `ff8ecd0a950b0f916f745a15abc1d7b256606a56`.
Source candidate: `1bc57de426` (the subsequent commit updates only this report).
Report path: `reports/routing-telemetry.md` (the repository's validation-artifact directory).

| ID | Status | Commit | Regression tests |
| --- | --- | --- | --- |
| T1-1 | done; historical empty Codex reasoning counts not reproduced | `29596fec14` | `test_request_effort_labels`, `test_provider_and_legacy_effort_labels`, `test_codex_responses_reasoning_counts_are_already_observed` |
| T1-2 | done under updated generic tag contract | `842d4abe73` | `test_codex_tag_reuses_existing_base_url_override`, `test_claude_work_tag_is_launch_local`, `test_prefix_is_removed_before_routing_and_identity_resets`, `test_existing_rows_receive_empty_tag_without_data_loss` |
| T1-3 | done | `1bc57de426` | `test_usage_groups_tags_sessions_and_preserves_unknowns`, `test_usage_endpoint_runs_behind_existing_auth_gate`, `test_served_model_observation_and_monitor_persistence`, `test_report_cli_reads_usage_locally` |
| T1-4 | done under updated routing scope | `33c47fe3bc` | `test_feedback_persists_outcome_and_updates_rating` |
| T1-5 | done at the wire contract | `27c3f26f76` | `test_unattended_soft_block_is_session_scoped_and_never_auto_approved`, `test_unknown_context_fallback_bands_are_unchanged` |

## Reproduction and behavior

**T1-1.** Base ingestion admitted only low, medium and high; existing xhigh tests
explicitly expected an unrecognized raw value. New regressions failed **20**
cases and passed **10** before the fix. Shared normalization now recognizes none,
minimal, low, medium, high, xhigh, max and ultra. Historical explicit raw labels
are recovered without rewriting rows; conflicting and unknown values stay unknown.
The existing parser's unknown-value fixture was changed from ultra (now supported)
to future-effort, retaining the unknown-value assertion.

The coordinator also requested verification of Codex reasoning-token capture.
That historical failure is **not reproduced** on this base: JSON and
response.completed SSE usage fixtures through the existing openai-codex parser
capture 30 reasoning / 10 visible / 40 total output tokens and 50 cached input
tokens. A monitor round trip preserves reasoning and visible counts. The counting
path required no source change; missing provider usage stays unknown. This is
offline source evidence, not a live provider receipt.

**T1-2.** The coordinator's October 16 clarification replaced the original
Hub-specific job_id/attempt grammar with an opaque 1–128 character tag drawn
from ASCII letters, digits, dot, underscore, colon and hyphen. Example:
`orca:job_example:1`. The additive column is `work_tag`; old rows default to empty.

Both launchers use TOKENPAK_JOB_TAG in a launch-local `/_tokenpak/t/TAG` base-URL
prefix. Codex changes only the value of its existing base_url override, without
adding provider overrides. Claude uses ANTHROPIC_BASE_URL. The proxy also accepts
X-Tokenpak-Tag, strips the prefix before routing and session extraction, and
excludes the internal header from upstream forwarding. Invalid values are served
untagged with one warning that does not echo the input. Tags do not become agent
identities or change guard policy. Tests cover invalid values, 128-character tags,
dot segments, session-handler reuse and retained legacy rows. Base Monitor.log
rejected the new tag argument in **4** regression cases before implementation.

**T1-3.** New read-only interfaces:

```text
tokenpak report --usage --work-tag orca:job_example:1 --session-id SESSION --json
GET /stats/usage?work_tag=orca:job_example:1&session_id=SESSION
```

Both filters are optional. The CLI can use `--db path` and reads locally without
contacting the proxy. The endpoint follows the existing authentication gate and
scopes bearer-authenticated callers to their own user_id. It rejects duplicate
or unknown filters, returns a structured 503 for an unreadable ledger, and marks
responses no-store.

Results contain tag totals and totals per (tag, session): request counts, observed
served models, effort labels, input/cached/output/reasoning counters and first/last
timestamps. The new `served_model` field comes from response metadata; requested
aliases are not presented as served identities. Historical missing served-model
or effort identities have explicit unknown counts. Nullable token totals and
observed-request counts expose incomplete measurement. Cache and reasoning
counters can be subsets of input/output and must not be summed to infer billing.
Efforts reflect the existing provider/request observation provenance.

Only counter and identity columns are selected: no prompt or response text.
Missing databases are not created, old databases are not migrated by the reader,
and tests check byte-identical ledger content. Before implementation, **3**
regressions failed for the missing reader and missing CLI usage result.

**T1-4.** The feedback regression failed with
`No module named 'tokenpak.proxy.elo'` before the fix and passes with
`tokenpak.telemetry.elo`, checking both the persisted outcome and the rating
change in temporary storage. The model-tier data is marked stale.

The coordinator clarified that TokenPak will own a provider-agnostic routing
scorer, with Orca as an adapter, in a follow-up lane. Routing modules are retained
without deprecation or activation. The blanket no-production-callers finding
is not reproduced: request_pipeline and the route CLI use rules; ShadowHook
imports the routing ledger.

**T1-5.** Both Claude messages and Codex Responses shapes can reach soft block
through the actual estimator/policy/orchestrator without operator approval. With
an unknown context window, defaults remain 100,000 warn / 500,000 block /
1,000,000 hard stop. Before the fix, **2** tests failed on missing session metadata
and **1** threshold test passed. Block, pending and reprompt responses now add:

```json
{"session_id":"SESSION","automatic_retry_allowed":false,"headless_action":"stop_and_report"}
```

HTTP 402, existing error types, retry-after-approval semantics, pending storage,
thresholds and hard stops are unchanged. The outcome is machine-readable; this
change does not force an unmodified native client to obey the contract. Native
client and Hub reaction to a 402 remains separate qualification.

## Validation

Final focused run: **136 passed**, one existing deprecation warning:

```sh
python3 -m pytest \
  tests/proxy/test_routing_efforts.py tests/proxy/test_xhigh_effort_identity.py \
  tests/proxy/test_monitor_reasoning_columns.py tests/proxy/test_job_attribution.py \
  tests/proxy/test_job_usage.py tests/proxy/test_shadow_feedback.py \
  tests/companion/test_job_tag_launcher.py tests/services/test_reasoning_usage_parsers.py \
  tests/proxy/spend_guard/test_headless_block_contract.py \
  tests/proxy/spend_guard/test_codex_responses_approval.py \
  tests/proxy/test_session_forecast_calibration.py -q --tb=short
```

Storage/schema cohort: **53 passed** (overlaps the focused cohort):
`test_job_attribution.py`, `test_job_usage.py`,
`tests/release_gate/test_telemetry_schema_snapshot.py` and
`tests/release_gate/test_migration_multihop.py`. The later nullable-history
regression also passes in the final focused run. The telemetry schema snapshot
was regenerated from this checkout with `PYTHONPATH=.`.

Actual published-schema migration matrix:
`PYTHONPATH=. python3 scripts/release_gate/migration_multihop.py` — **18/18 PASS**,
covering three stores across six published baselines v1.23.0–v1.28.0, with
schema, integrity and seeded-row preservation checks. All stores were temporary.
Ruff lint and format checks pass on all **22** changed Python files; diff check
passes. Source hashes are checked after commit consolidation to ensure it does
not alter the tested files.

## Offline parity acceptance — October 16 verification

The clarified acceptance permits failures that reproduce on the base and
collection errors requiring unavailable network access. It also requires every
added test to pass and expected Orca instruction files to remain uncommitted.
Those criteria are met. This verification changed no product source or tests;
all 24 source/test/data file hashes match the previously tested candidate.

The base was a `git archive` export of the exact base SHA into `.t1-base-check`
inside this worktree. Both revisions used Python 3.12.3, existing dependencies,
temporary homes/databases and the same offline harness. The export was removed
after verifying that the task's test processes had exited. No dependency was
installed and no live service was contacted or changed.

| Check | Base | Candidate | Comparison |
| --- | --- | --- | --- |
| Required documented hermetic selection | 9,748 selected; 0 executed; 139 collection skips; 397 deselected; 11 collection errors; exit 2 | 9,807 selected; 0 executed; 139 collection skips; 397 deselected; 11 collection errors; exit 2 | Same error nodes and missing tokenizer dependency; +59 selected cases |
| Focused telemetry/launcher/guard/forecast cohort above | 77 passed | 136 passed | All 77 common node IDs pass; all 59 added cases pass |
| Broker and guard failure comparison | 14 passed, 12 failed | 14 passed, 12 failed | Identical failure signatures after normalizing memory addresses |
| CLI diagnostic failure comparison | 13 passed, 10 failed | 13 passed, 10 failed | Same failed node IDs and assertions; missing subprocess dependency |
| Total of the three executed matched cohorts | **104 passed, 22 failed** | **163 passed, 22 failed** | **0 new failures; 59 additional passes** |

The three executed cohorts are disjoint. Collection skips are not successful
executions. Tests outside these executable cohorts remain unverified because
the broad command cannot collect successfully in this environment.

The required command is the repository's offline selection from
`tests/TEST-ENV-MATRIX.md` and `tests/conftest.py`, applied to the `tests/` tree
used by `make test`:

```sh
python3 -m pytest tests/ -m 'not needs_proxy and not needs_webhook and not needs_internal_alerts and not needs_cali_env and not needs_fast_host' -q --tb=short
```

Both invocations also loaded a test-only receipt plugin and `sitecustomize`
through `PYTHONPATH`. The latter raises `PermissionError` for DNS and IPv4/IPv6
connect/bind/sendto audit events; the native sandbox also denies network sockets.
It does not mock product logic, tokenizers, database writes or assertions.
This prevents collection-time cache downloads and catches unmarked network tests.
The standard broad invocations retain the default 30-second per-test timeout.
The sequential broker/guard and CLI diagnostic comparisons use `--timeout=180`
to distinguish temporary SQLite/host delays from assertion failures; no product
budget, guard threshold or test assertion was changed.

### Identical network-only collection errors

All 11 collection errors, on both revisions, originate in loading the absent
`cl100k_base.tiktoken` data from `openaipublic.blob.core.windows.net`. Each records
`offline parity guard: DNS disabled`. They are:

- `tests/benchmarks`
- `tests/chaos/test_vault_index_corruption.py`
- `tests/proxy/test_header_allowlist_canonicality.py`
- `tests/proxy/test_vault_bridge_memory_bound.py`
- `tests/proxy/test_vault_bridge_reload_serialization.py`
- `tests/proxy/test_vault_bridge_rss_return.py`
- `tests/proxy/test_vault_retrieval_timeout.py`
- `tests/test_health.py`
- `tests/test_lifecycle.py`
- `tests/test_security.py`
- `tests/test_swap_monitor.py`

A provisioned offline tokenizer cache is needed for broad-suite execution.
Network access was not enabled to obtain it.

### Pre-existing broker and guard failures

The five broker failures expect their historical model catalog despite the
registry-first loader. The seven guard failures use old request sizes that no
longer cross the known model's context-window threshold. Both revisions fail
at the same assertions:

- `tokenpak/tests/test_broker.py::TestLoadTiers::test_loads_valid_json`
- `tokenpak/tests/test_broker.py::TestLoadTiers::test_returns_empty_on_missing_file`
- `tokenpak/tests/test_broker.py::TestLoadTiers::test_returns_empty_on_malformed_json`
- `tokenpak/tests/test_broker.py::TestUpgrade::test_upgrade_high_complexity_low_acceptance`
- `tokenpak/tests/test_broker.py::TestUpgrade::test_no_upgrade_already_most_capable`
- `tokenpak/tests/test_spend_guard_e2e.py::TestRunawayBlocks::test_runaway_blocks_then_replay`
- `tokenpak/tests/test_spend_guard_e2e.py::TestRunawayBlocks::test_runaway_blocks_then_cancel`
- `tokenpak/tests/test_spend_guard_e2e.py::TestRunawayBlocks::test_ambiguous_re_prompts`
- `tokenpak/tests/test_spend_guard_e2e.py::TestTIPDirectivesE2E::test_tip_cancel_discards_pending`
- `tokenpak/tests/test_spend_guard_e2e.py::TestAntiLoop::test_repeated_blocked_request_anti_loops`
- `tokenpak/tests/test_spend_guard_e2e.py::TestAuditTrail::test_block_writes_audit_row`
- `tokenpak/tests/test_spend_guard_e2e.py::TestAuditTrail::test_replay_writes_audit_row`

### Identical CLI environment failures

These ten cases fail on both revisions. Their subprocess helpers replace the
environment and omit `PYTHONUSERBASE`, so the installed user-site `watchdog`
dependency is absent from the child interpreter. Several assertions therefore
see empty CLI output instead of the expected config diagnosis or JSON. This
fixture/environment problem was not repaired as part of telemetry work:

- `tests/cli/test_corrupt_config_is_not_missing_config.py::test_start_reports_the_parse_error_not_a_missing_api_key`
- `tests/cli/test_corrupt_config_is_not_missing_config.py::test_status_reports_the_config_as_unreadable`
- `tests/cli/test_corrupt_config_is_not_missing_config.py::test_doctor_marks_the_corrupt_config_as_an_error`
- `tests/cli/test_corrupt_config_is_not_missing_config.py::test_preview_exit_code_matches_the_state_it_reports`
- `tests/cli/test_every_parser_verb_is_dispatchable.py::test_upgrade_is_a_working_shim_that_states_enrollment_is_unavailable`
- `tests/cli/test_every_parser_verb_is_dispatchable.py::test_upgrade_stays_out_of_default_discovery`
- `tests/cli/test_path_resolution_conformance.py::TestHomeOverrideIsolatesWrites::test_setup_writes_inside_the_override_only`
- `tests/cli/test_path_resolution_conformance.py::TestPreviewJsonContract::test_input_guard_still_emits_json`
- `tests/cli/test_path_resolution_conformance.py::TestCorruptIsNotMissing::test_unparseable_meta_config_is_corrupt_not_absent`
- `tests/cli/test_path_resolution_conformance.py::TestOverrideResistsSymlinkEscape::test_symlinked_write_target_is_refused`

### Retained adverse and interrupted checks

The earlier quick-suite receipt remains **11 collection errors, 139 skipped,
10,155 deselected**. Its missing-tokenizer cause is now reproduced by the exact
base export, rather than inferred from a representative module comparison.
The implementation-round expanded cohorts recorded **66 passed, 5 failed,
1 skipped** for routing and **74 passed, 7 failed** for the guard; their twelve
assertion failures are covered by the exact-base comparison above.

An exploratory `--continue-on-collection-errors` run went beyond the documented
command. The candidate was interrupted after **1,052 passed, 23 failed,
159 skipped, 397 deselected, 1 xfailed, 14 errors**; the base attempt reached its
1,200-second process timeout without a complete receipt. These are incomplete
runs, not full-suite evidence or part of the matched totals above. Every adverse
candidate test outcome was classified:

- Eleven runtime cases need sockets prohibited by the no-network constraint:
  five `TestProxyLifecycle` cases in `tests/chaos/test_chaos.py`, three cases in
  `tests/cli/test_stats_reports_this_installs_proxy.py`, two runtime-frame cases
  in `tests/cli/test_update_pending.py`, and
  `test_status_unreachable_path_prints_no_cta` in `tests/cli/test_upgrade_cmd.py`.
  Three are fixture errors; eight are test failures. None reached a live service.
- Ten CLI failures reproduce on the base as listed above.
- Five CLI timeout cases pass on both revisions in the sequential comparison:
  help consistency, home-migration retry, permission-file scanning, and the two
  print-only integration cases. Both parameterized integration functions were
  checked across all five targets, avoiding dependence on unordered set iteration.
- The other eleven errors are the collection errors listed above.

An expanded legacy-suite attempt reached its 300-second process timeout and is
not counted as a completed suite. A subsequent 53-case broker/guard diagnostic
completed with **35 passed, 18 failed**, including six 30-second SQLite timeout
failures. All six timeout cases pass on both revisions in the sequential matched
cohort; the remaining twelve assertion failures are identical on base and
candidate. Failed and interrupted receipts remain available; they were not
relabeled as successful full-suite runs.

Detailed local receipts are in `/tmp/t1-parity-20261016/`: `base-required` and
`branch-required` logs/JSON, `base-focused` and `focused`, `base-matched` and
`branch-matched`, `base-diagnostic` and `branch-diagnostic`, and
`final-summary.json`. The same directory retains the interrupted diagnostic logs
and the offline harness. This committed report is the durable acceptance record.

Independent QA, provisioned broad-suite execution, native-client handling of
HTTP 402, integration, release and installation remain separate tasks. No new
source defect or unmet criterion under the clarified parity contract remains.

All task edits are committed. Expected untracked Orca-generated instruction
files/directories remain untouched: `.cursor/`, `.github/copilot-instructions.md`,
`AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `ORCA-WORKFLOW.md`. They are not task edits
and were not committed. The task's tracked source is clean.
