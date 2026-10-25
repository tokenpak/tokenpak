# Status writer CPU and lifecycle

Base: `a3c2b8ea682422457e1b44b670fc7cb5ef432585` (the requested fallback after
the status-label candidate failed independent review). Source and tests only;
no installation, publication, host configuration or existing-process changes.

Disposition: **implementation retained; acceptance incomplete**. The focused
checks pass, but both full-suite attempts ended early. The required complete
no-regression comparison remains unmet; independent review and authorization
to land or install are still required.

## Refresh work

The base writer reads a session marker (65 characters maximum), requests a
loopback snapshot (65,537 bytes maximum, 0.5 second socket timeout), renders
eight widths and writes two files. It does not parse transcripts or read the
economics corpus itself. The endpoint computes economics in the proxy process.
Pending-version detection already has a 60 second cache.

The unbounded writer operation was enumerating every status-cache entry on
every refresh. The writer now removes only its own previous session's two
files after a session change. Unrelated files and expired files left by an
earlier writer are retained for launch-directory disposal. Working state is
one directory/session pair, independent of session history.

Engineering microbenchmark, five refreshes per size, Python 3.12.3 on Linux,
synthetic isolated data, stubbed snapshot and update lookup; no live endpoint.
Transcript and economics fixtures are sparse files and never read by this path.
CPU milliseconds are medians; wall time is diagnostic on a shared host.

| Extra cache entries | Transcript / economics bytes each | Base CPU ms | Changed CPU ms | Base wall ms | Changed wall ms |
| ---: | ---: | ---: | ---: | ---: | ---: |
| 0 | 0 | 3.22 | 6.19 | 5.44 | 6.73 |
| 1,000 | 10,000,000 | 22.23 | 5.51 | 31.24 | 5.66 |
| 10,000 | 100,000,000 | 325.03 | 7.19 | 325.04 | 7.82 |

This demonstrates directory-size scaling and its removal. It does not prove
the historical incident's cause: the stopped processes and their runtime data
were not inspected, and the incident's installed source was not measured.
No transcript-reparse claim is supported by this selected base.

The related ledger-performance investigation separately measured expensive
corpus evaluation in the proxy and qualified an incremental-corpus repair on a
different candidate. That is server-process work behind the HTTP endpoint,
not a transcript read in this writer. This branch neither imports that repair
nor claims to bound the proxy's backend computation; the writer's existing
response-size and socket-timeout bounds remain in force. Deployment evidence
would need to attribute CPU to the actual process and installed source.

A paired cProfile run on the same 10,000-entry directory confirms attribution:
base 200,929 calls, with directory iteration and child `Path` construction
dominating (0.744 seconds cumulative in iteration out of 0.992 profiled seconds);
candidate 859 calls and zero directory enumeration (0.0086 profiled seconds).
Profiler overhead is included in these diagnostic figures, so the table above
remains the unprofiled timing comparison. The candidate's remaining work is two
atomic writes, one bounded marker read and eight renders.

`test_status_worker_cost.py`: all three tests fail on the base and pass after
the change. The deterministic bound is zero directory enumerations per
refresh, even with 1,000 unrelated entries, and at most two known-path removals
on a session transition. The existing display tests pass; the loopback server
test initially failed sandbox socket creation (PermissionError), then passed
in an approved isolated rerun with its own temporary server and writer.

## Idle scheduling

Unchanged inputs trigger a full refresh every 30 seconds. Every 2 seconds the
worker checks bounded session-marker content and metadata for a fixed set of
paths: the launch directory, marker, settings/title, local monitor database and
WAL, user configuration, pending-update marker and exact native transcript.
Changed metadata resumes refresh at the next 2 second poll. There is no history
search, database query or transcript parse in this poll. Proxy-only changes
without a local signal are discovered on the next 30 second refresh.

Cache expiry remains 10 seconds after the observation; idle snapshots can show
stale rather than misrepresenting an older observation as current. Creating the
status subdirectory may cause one extra initial refresh. Changes arriving during
a refresh remain visible at the next poll.

The four virtual-clock/metadata tests in `test_status_worker_idle.py` fail on
the preceding commit and pass after this change. A 95 second unchanged session
does four refreshes instead of 48; a change at second 7 refreshes by second 9;
repeated observation failures also wait 30 seconds.

## Lifetime

The writer checks its original parent and stops on parent change, SIGTERM or
SIGINT. It additionally exits on the next 2 second poll when its run directory
or previously observed transcript disappears. A removed bound-session marker
also ends it. A new session identity resets transcript tracking before the old
file is checked, preserving `/clear`; an as-yet-uncreated transcript is allowed.

One hour without session activity ends the writer even while the parent remains
alive. Activity is launch-directory, binding-marker or bound-transcript metadata
change. Status-cache writes and other sessions' shared-ledger writes do not
extend the session. Initial age comes from file mtimes; subsequent intervals use
the monotonic clock. An already-stale launch exits before producing a snapshot.

Transcript detection uses the ordinary Claude project path under
`CLAUDE_CONFIG_DIR` (or the native default) and the exact validated session ID.
There is no fallback to another session's latest transcript or directory search.
Custom transcript layouts and other clients without this path rely on the
parent/run-directory/one-hour fences. Native Windows and custom-layout transcript
discovery have not been qualified. A prompt resumed after the writer's one-hour
exit needs a new launch to start another writer; this change adds no supervisor.

Unexpected refresh exceptions are logged by exception class only and retry no
sooner than 30 seconds later, including when input files keep changing. Cached
data is never re-stamped as fresh after an error. Parent/signal/deletion checks
remain active during the retry wait.

## Ending a finished interactive worker session (report only)

The current launcher starts a detached writer and then execs the native client.
The exec preserves the parent PID; a completed task left at the client's prompt
therefore leaves the writer's parent alive. Actual client exit changes the
writer's parent and ends it. No post-exec launcher cleanup runs on that path.

A supervised-child launcher could wait for native-client exit and call
`stop_writer()` in a `finally` block, forwarding signals and preserving exit
codes and PTY behavior. That is the separate launcher-exit task's scope; its
current amendment makes session-directory deletion report-only. It does not
itself turn task completion into native-client exit. An explicit, authorized
coordinator cleanup after accepted settlement would need to request client exit
and positively verify it before reclaiming a task-created terminal.

The separate finished-tab cleanup closes only completed/cancelled shell-only
terminals after its idle period; it retains live-agent and failed terminals.
It therefore cannot close a completed task whose native client is still alive.
No launcher, coordinator protocol, live terminal, process or cleanup policy is
changed here. A follow-up should qualify the whole settlement/client-exit/writer
exit sequence with a disposable dispatch after installation is authorized.

## Validation

Focused final runs: **73 passed**: 72 in the sandbox plus one approved isolated
loopback integration run after its retained socket-permission failure. This
includes all 21 added tests and all 52 existing display tests. The final lifetime test file run
against the preceding commit has nine failures and three passing compatibility
checks; all twelve pass with this change. The initial lifetime run retained a
floating-point assertion failure, repaired by a 0.01 second test tolerance;
the production one-hour threshold did not change.

Additional deterministic checks cover 1 GiB sparse transcript and corpus files:
no body opens, at most 16 metadata operations per input poll, identical work on
unchanged repeats. An external database-creation probe confirms that a database
outside the run directory wakes the metadata watcher. Database candidates are
resolved once with the canonical path resolver; the watcher never opens SQLite.

Fail-before/pass-after receipts are retained per behavior:

| Behavior | Failing predecessor receipt | Candidate receipt |
| --- | --- | --- |
| Bounded cleanup | `item1-before.xml`: 3 failures on base | 3 added cases pass; `item1-after.xml` also retains the unrelated socket denial |
| Idle scheduling | `item2-before.xml`: 4 failures on commit 1 | 4 initial cases pass; 2 later metadata cases also pass in the final run |
| Lifetime and exception retry | `item3-final-before.xml`: 9 failures, 3 compatibility passes on commit 2 | All 12 pass in the final run |

The named regressions include
`test_refresh_does_not_enumerate_run_or_cache`,
`test_refresh_removes_only_its_previous_session_pair`,
`test_unchanged_inputs_refresh_at_most_every_thirty_seconds`,
`test_changed_inputs_resume_within_two_seconds`,
`test_deleted_run_directory_ends_writer`,
`test_deleted_bound_transcript_ends_writer`,
`test_idle_session_expires_even_if_parent_is_alive`, and
`test_unexpected_refresh_error_waits_before_retry`.

`focused-final2.xml` contains the 72 sandbox passes; `loopback-approved.xml`
contains the additional existing integration pass. Ruff check, Ruff format
check and whitespace validation pass. The source/test hashes remained unchanged
through the final report update.

The initial serial runs are **incomplete**, retained as `base-suite.log` and
`candidate-suite.log`. The base stopped producing output for over nine minutes
despite the configured 30 second test timeout; both owned test sessions were
then interrupted through their execution handles (exit 130, no complete XML).
Those attempts do not establish parity and have not been relabeled as passing.

Both suite runs use Python 3.12.3, isolated homes, cleared inherited launch and
provider variables, `PYTHONHASHSEED=0`, and the repository's offline selection:

```sh
python3 -m pytest -q --tb=no -p no:cacheprovider --continue-on-collection-errors \
  -n 3 --dist=loadfile --timeout=120 --timeout-method=thread \
  --max-worker-restart=30 --junitxml=receipt.xml \
  -m 'oss and not needs_proxy and not needs_webhook and not needs_internal_alerts and not needs_cali_env and not needs_fast_host'
```

The replacement run used fresh `git archive` exports on both sides, base then
candidate, with source/test hashes checked before collection. The identical
120 second allowance and three test workers changed no assertion or selection.
Both runs then lost two test workers and ended with an internal xdist scheduler
`KeyError` for `WorkerController gw4` during replacement-worker collection.

| Contained run | Named passes | Collection skips | Crash errors | Scheduler errors | Unfinished XML entries | Exit | Elapsed seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Base | 993 | 141 | 2 | 1 | 1 | 3 | 736.32 |
| Candidate | 1,231 | 141 | 2 | 1 | 1 | 3 | 585.62 |

The console calls the two worker crashes failures; final XML records them as
errors. The unnamed unfinished XML element on each side is not a passing test.
All 993 named base passes also pass on the candidate, with no observed adverse,
missing or skipped transition among those cases. The candidate's 238 additional
observed passes reflect scheduling before the abort, not 238 newly added tests.
Most selected tests were never reached (progress stopped near 9% and 12%), so
this partial overlap **does not establish full-suite parity**.

Both crashes occurred in `test_health_returns_200` in
`tests/test_telemetry_server.py` and `test_503_when_semaphore_exhausted` in
`tests/test_proxy_server_async.py`. An isolated base run of the first test with
`--timeout=20 --timeout-method=thread` also timed out while Starlette/AnyIO waited
on a cross-thread future. This identifies a base stall site, not its root cause.
The status-writer source is not changed to work around these unrelated tests.

A subsequent approved diagnostic was intended to select those two cases. Its
helper matched only XML failure tags rather than the finalized error tags and
therefore selected zero nodes, accidentally starting default collection. Its
100 second outer watchdog killed and reaped that owned test child; no complete
XML was produced, and the candidate diagnostic never started. This invalid
attempt is retained separately and contributes no passing evidence. Any future
diagnostic selector must assert a nonempty exact node list before execution.

Raw logs, XML, timings, source hashes, `contained_run.py` and corrected per-node
comparison are retained outside the worktree in `/tmp/writer-cpu-evidence`.
The original comparison that counted an unnamed entry is also retained.
`receipts.tar.gz` contains 56 receipt/helper files, including a SHA-256 manifest;
its SHA-256 is
`4e56d1418f51a64d101387dbbb6619f6bbdef817e400657727262f54a0dd5955`.
These are local artifacts, not installation or deployment evidence.

The tested source commit is `6eb37aa085fa0bb5c3a543a0c7f05faa1783b110`;
the final commit amends only this report. The final identity and the three
behavior commits are recorded with the dispatch result. The next acceptance
step is a complete matched base/candidate run in a qualified test environment,
retaining these incomplete and adverse receipts, followed by independent review.
