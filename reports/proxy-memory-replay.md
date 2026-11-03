# Proxy memory and descriptor bounds on the main line: replay report

**Result: retention and descriptor growth are fixed; the RSS bound is met in
one of three runs at default allocator settings.** On the replay harness, the
base's RSS grows by 245.9 MB after a 200-request warm-up; the head's grows by
14.8, 21.5 and 34.4 MB in three runs, against a bound of 20 MB. The head's
remaining growth is allocator high-water, not retained memory: traced Python
memory stays flat, and `malloc_trim(0)` brings RSS below the warm-up level in
every head run. With the allocator settings proposed below (not applied), the
same run grows by 5.2 MB. `monitor.db` descriptors stay at or below 2 in every
sample of every head run (base: 4-5). The touched tests raise no
`ResourceWarning` on Python 3.12 or 3.13, and the full offline suite shows no
regression against the base. Nothing was pushed, published or installed, no
service or host setting was changed, and the replay never left loopback.

- Base: `8fda623703dd248dd84c8eee40594ac2083def9f`, the staging main line
  (`ff8ecd0a95`) plus the two Spend Guard approval commits it depends on.
- Head: the last commit on this branch. Code and tests were measured at
  `9aa9845372`; the commits after it add only a changeset and this report.

## What changed

The lifecycle fixes made on the 1.31.0 hotfix line are carried onto the main
line unchanged (cherry-picked with `-x`, so each commit names its original):

| Commit | Original | Change |
| --- | --- | --- |
| `7cabaf225a` | `22147af7bd` | Every SQLite connection outside the CLI is closed on success and error paths; per-thread caches close their connection when the thread ends; a static audit test fails on any unclosed site. |
| `343d1e316a` | `0afacad6fd` | Session-economics evaluations are shared between identical requests and capped (two at once, 503 with `Retry-After` when busy); a request whose client has gone is not evaluated. |
| `d933fa2bcf` | `b7ea25a8c0` | The cache-invalidator session cache keeps a compact signature, not the raw body, under a 32 MiB cap (`TOKENPAK_CACHE_INVALIDATOR_MAX_BYTES`); each body is parsed once and the previous body is never parsed again. The calibration corpus is maintained incrementally instead of re-reading the ledger on every call. |
| `60658869a2` | `2360b0757c` | Upstream clients share one verified TLS context. |
| `7057489b46` | `cbd937d885` | Opt-in soak test (16 concurrent clients). |
| `6dd1e9d599` | `d794c15f3f` | Changesets and the public-API snapshot (+7 additive symbols). |

All six applied without conflicts: the 1.31.0 tag is an ancestor of the base,
four commits behind it. The hotfix line's release notes and report
(`8cb69f5597`) describe the 1.31.0 hotfix build and are not carried.

Added on top, for what the replay and the review still found:

| Commit | Change |
| --- | --- |
| `45a193d457` | `telemetry.tokens.count_tokens` was an `lru_cache` keyed by the text itself (8,192 entries). The proxy's file token-count endpoint counts 100,000-character chunks and `truncate_to_tokens` tries many long prefixes, so the cache could keep hundreds of megabytes of text alive. Texts longer than 256 characters are now keyed by length and a 128-bit digest. Counts, eviction, `cache_info()` and `clear_cache()` behave as before. |
| `9a7964a9aa` | The invalidator cap summed character counts; a non-ASCII prompt takes up to four bytes per character. Entry sizes are now object sizes, so the 32 MiB cap is a byte cap. |
| `c7c6bcc661` | The session and time forecast calibration readers opened the ledger `mode=ro` while every other reader opens it read-write. They now open `mode=rw` with `PRAGMA query_only`: still unable to write, still unable to create a missing file, and no longer pinning an extra descriptor (see Descriptors). |
| `4a8010690e` | The proxy tests' `stub_upstream` fixture shut its server down without closing it; the leaked listening socket surfaced as a `ResourceWarning` in the next test. |
| `9aa9845372` | The replay harness and its opt-in test (below). |

## Harness

`tests/proxy/_memory_replay.py` drives a real `ProxyServer` subprocess
(`_memory_replay_launcher.py`) behind a local fake upstream. An audit hook in
the proxy process refuses any connection or name lookup that is not loopback;
it recorded zero attempts in every run.

- 1,500 `POST /v1/messages` requests, Claude Code-shaped (system, 24 tools,
  thinking, tool results), 2.28 GB in total. Each of 120 sessions sends 12 or
  13 turns whose body grows from 0.5 MB to 2.5 MB by appending messages, as a
  real conversation does; about 16 sessions are open at once. A third of the
  sessions drop a tool half-way (a cache invalidator event). Half stream.
- One session-economics call every 10 requests and a sweep of six dashboard
  endpoints every 100, against a ledger seeded with 5,000 rows, so the
  SQLite read paths of those endpoints run too.
- One client, requests in order. The DLP scan and Spend Guard are off, as in
  the soak test: the scan costs about a millisecond of CPU per kilobyte and
  is not the memory under test, and Spend Guard would hold requests.
- Sampled on the proxy process: RSS, threads and descriptors every 25
  requests, `monitor.db` descriptors every 0.2 s, and with `trace=1`
  `tracemalloc` traced memory. After the measured phase: the top
  allocation-site growth since the warm-up, the live objects holding large
  values, and the RSS `malloc_trim(0)` returns.
- `TOKENPAK_REPLAY_SOURCE` runs the same harness against another source
  tree; the base was measured from a `git archive` export of the base commit.
  `env.NAME=value` sets a variable in the proxy process only (used for the
  allocator comparison).

`tests/proxy/test_proxy_memory_replay.py` (`needs_proxy`, `slow`) runs it and
asserts the bounds; with default allocator settings it currently fails the
RSS bound in most runs (see Results). Run by hand:
`python -m tests.proxy._memory_replay` (`trace=1`, `concurrency=4`,
`env.NAME=value` and other fields as `key=value`).

## Results

Untraced runs measure RSS; traced runs (with `tracemalloc`, which costs RSS
of its own) classify the growth. Host: 8 cores, load 9-14 throughout.

| Measure (proxy process) | Base | Head |
| --- | --- | --- |
| **RSS growth after warm-up, untraced** | **+245.9 MB** (172 to 418) | **+14.8, +34.4, +21.5 MB** in three runs (128 to 142, 120 to 155, 106 to 127) |
| Largest sample after warm-up, above the warm-up | +245.9 MB | +19.3, +46.9, +30.9 MB |
| RSS after `malloc_trim(0)` at the end | 365 MB | 90, 92, 83 MB (below the warm-up level each time) |
| `monitor.db` descriptors, peak over every 0.2 s sample | 4 (13,905 samples) | **2** in every run (13,199 to 30,728 samples each) |
| Requests answered 200 | 1,500 of 1,500 | 1,500 of 1,500 in every run |
| Session-economics latency p50 / max | 389 / 1,014 ms | 77 / 1,094 ms |
| Traced run: RSS growth after warm-up | +371.6 MB (296 to 668) | +54.4 MB (235 to 290) |
| Traced run: traced-memory growth after warm-up | +219.0 MB (85 to 304) | +10.5 MB (63 to 73; 62-68 in between) |
| Traced run: top growth site | `server.py` request-body read, +218 MB | the same read, +4.7 MB (bodies in flight at the snapshot) |
| Traced run: RSS returned by `malloc_trim(0)` | 166 MB of 713 | 76 MB of 340 |
| Traced run: `monitor.db` descriptor peak | 5 | 2 |
| **Classification** | **mixed: retention, plus allocator high-water** | **fragmentation: traced memory flat, RSS still rising** |

### Classification

On the base, traced memory grows by 219 MB and nearly all of it is one site,
the read of the request body: the invalidator cache kept the raw body of each
of its last 100 sessions, and with conversations growing to 2.5 MB that is
retention, roughly 250 MB at the cap. The other half of the RSS growth (about
150 MB) is not traced Python memory. `malloc_trim` returns 166 MB of it in
the traced run, so it is free memory the allocator has not given back to the
system: the high-water mark of parsing a 2.5 MB body (and, on the base,
parsing two of them per request). So the base is retention plus allocator
high-water.

The head keeps no body and parses each body once, and its traced memory is
flat: 62-73 MB from the warm-up to the end. Its RSS still drifts upward by
15-34 MB over 1,300 requests, and `malloc_trim` takes it below the warm-up
level every time: what is left is free memory held by glibc's allocator, not
retained objects. Each request still makes multi-megabyte transient
allocations (the body, its parse, the upstream copy), and with glibc's
default dynamic mmap threshold those land in the heap and leave it
fragmented.

### Descriptors

While the ledger writer holds `monitor.db` open, SQLite's unix VFS does not
close a closed connection's descriptor; it keeps it for reuse by the next
connection opened in the same mode (read-write or read-only). The process's
`monitor.db` descriptor count is therefore the writer plus the most
connections ever open at once in each mode, not a count of open connections.
An opt-in connection tracker in the launcher
(`TOKENPAK_REPLAY_SQLITE_TRACK=1`) shows, on the head before the calibration
change: at most one read-write reader beside the writer, and one read-only
reader (the calibration corpus), so 3. With the calibration readers on
read-write, it is 2 at every sample.

Two things raise the count above 2 on a real host, neither of them a leak:
concurrent requests (each concurrent reader can park one more descriptor; on
the hotfix line the soak test's 21 clients peaked at 13 and did not grow), and
Spend Guard, which reads the ledger `mode=ro` in several places and so parks
one read-only descriptor once it is enabled. Spend Guard code is outside this
change.

For comparison, a long-running proxy on a workstation, on a release without
these fixes, held 332 `monitor.db` descriptors and 12 WAL descriptors after
41 hours, with 1.66 GB RSS (read-only check of `/proc/<pid>/fd` and
`/proc/<pid>/status`).

## Proposal (not applied): allocator settings for the proxy

Measured on the head with the same harness, the settings set only in the
replay's proxy process (one run each):

| Proxy environment | RSS growth after warm-up | Largest sample | RSS at the end |
| --- | --- | --- | --- |
| glibc defaults (three runs) | +14.8 / +34.4 / +21.5 MB | +19.3 / +46.9 / +30.9 MB | 142 / 155 / 127 MB |
| `MALLOC_ARENA_MAX=2` | +28.2 MB | +28.2 MB | 139 MB |
| `MALLOC_MMAP_THRESHOLD_=131072 MALLOC_TRIM_THRESHOLD_=131072` | +16.5 MB | +20.4 MB | 113 MB |
| both thresholds and `MALLOC_ARENA_MAX=2` | **+5.2 MB** | +12.9 MB | 108 MB |

A fixed mmap threshold makes every allocation of 128 KiB or more (the body,
each message's text) its own mapping, returned to the system when freed, and
the trim threshold returns heap tops sooner; the arena cap bounds how many
heaps keep a high-water mark when requests run on many threads. CPU per
request did not change measurably (2,314 ms default, 2,383 ms with the
thresholds, on a loaded host).

Options, to be decided separately:

1. Set the three variables in the proxy's service environment. This is a host
   change (or a change to the unit that `tokenpak` installs), with the effect
   above.
2. Apply the same settings from the proxy at start-up with `mallopt()` on
   glibc. A source change; it needs its own review because it changes
   process-wide allocator behaviour.
3. Enable the existing opt-in memory guard (`TOKENPAK_MEMORY_GUARD=1`,
   `TOKENPAK_MEMORY_TARGET_MB`, `TOKENPAK_MEMORY_CEILING_MB`), which runs a
   collection and `malloc_trim` above the target. A configuration change; it
   trims after the fact rather than avoiding the high-water mark.

Then lower the per-request transient allocation itself (a follow-up): the
invalidator still parses each whole body to read five top-level fields.

Re-measure on a real host with more than one client before choosing, for
example `python -m tests.proxy._memory_replay concurrency=4
env.MALLOC_ARENA_MAX=2 env.MALLOC_MMAP_THRESHOLD_=131072
env.MALLOC_TRIM_THRESHOLD_=131072`. Nothing in this change sets any of these.

## Tests

- Focused: every carried and added test passes on the head (149 nodes across
  the touched test files; the new tests fail on the base code where they
  guard a fix: descriptors 3 instead of 2, 8 MB of counted text retained).
- `ResourceWarning`: the touched test files run with
  `-W error::ResourceWarning -W error::pytest.PytestUnraisableExceptionWarning`
  pass with no `ResourceWarning` on Python 3.12 and on Python 3.13 (where
  `sqlite3` reports unclosed connections): 149 passed on each.
- Full offline suite, base and head exports run at the same time:
  `pytest tests/ -m 'not needs_proxy and not needs_webhook and not
  needs_internal_alerts and not needs_cali_env and not needs_fast_host'
  --continue-on-collection-errors -n 3 --timeout=300`, `PYTHONHASHSEED=0`,
  clean environment and temporary home, an audit-hook network guard
  (loopback only, and no loopback port that was already listening), the
  tokenizer read from a local cache.

  | | Base | Head |
  | --- | --- | --- |
  | Passed / failed / errors / skipped | 9,617 / 50 / 0 / 377 | 9,664 / 50 / 0 / 377 |
  | Pass on base, fail on head | | 1: `test_cached_response_faster_than_api` (timing) |
  | Fail on base, pass on head | 1: `test_real_local_endpoint_writer_cli_and_shutdown` | |
  | Serial reruns of both, 3 times on each side | 6 of 6 passed | 6 of 6 passed |
  | New tests | | 47, all passed |

  **No deterministic regression.** The 49 failures common to both are
  pre-existing. A first pair of runs without the tokenizer cache had 45
  nodes differ (36 one way, 9 the other); all were test-order effects of the
  refused tokenizer download, and all 45 pass on both sides with the cache.
- Release gate: `gen_api_snapshot.py --check` and
  `gen_telemetry_schema.py --check` pass; `tests/release` and `tests/lint`
  pass; `ruff check` and `ruff format --check` on every changed Python file.

## Notes

- One unguarded snapshot check early in the work downloaded the public
  `cl100k_base` tokenizer file into the system temporary directory. No
  provider endpoint was contacted and nothing was spent; every later run used
  the network guard.
- Overlap: `tokenpak/routing/routing_ledger.py` is changed both by the carried
  SQLite lifecycle commit and by the open routing branches; merging either
  after the other needs a small conflict resolution in that file.
  `tokenpak/proxy/server.py` merges cleanly with them. Spend Guard code is not
  touched.
