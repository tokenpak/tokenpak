# Status writer CPU and lifecycle

Base: `a3c2b8ea682422457e1b44b670fc7cb5ef432585` (the requested fallback after
the status-label candidate failed independent review). Source and tests only;
no installation, publication, host configuration or existing-process changes.

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

Engineering microbenchmark, five refreshes per size, Python 3 on Linux,
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

`test_status_worker_cost.py`: all three tests fail on the base and pass after
the change. The deterministic bound is zero directory enumerations per
refresh, even with 1,000 unrelated entries, and at most two known-path removals
on a session transition. The existing display tests pass except the loopback
server test, which cannot bind a socket inside this sandbox (PermissionError).

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

## Validation in progress

Lifetime checks and full base/candidate suite parity follow in the final commit.
