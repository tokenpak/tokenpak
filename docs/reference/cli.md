# TokenPak CLI Reference

> **Generated** by `scripts/generate-cli-docs.py` from the current
> `_COMMAND_GROUPS` + argparse registrations in `tokenpak/cli/_impl.py`.
> Do not edit by hand — the `cli-docs-in-sync` CI gate fails on any
> diff between the generator output and this file.
>
> Run `python3 scripts/generate-cli-docs.py` after CLI changes to refresh.

This document lists every `tokenpak <subcommand>` registered at build
time, grouped the way `tokenpak help` groups them.

## Groups

- [Getting Started](#getting-started)
- [Indexing](#indexing)
- [Configuration](#configuration)
- [Versioning](#versioning)
- [Operations](#operations)
- [Advanced](#advanced)

## Getting Started

### `tokenpak setup`

One-time interactive configuration wizard (detects keys, picks profile, starts proxy)

```
usage: tokenpak setup [-h]

Interactive wizard: detects API keys from your environment, lets you pick a
compression profile, writes ~/.tokenpak/config.yaml, and starts the proxy. Run
once on install. After this, set your LLM client's BASE_URL to the proxy.

options:
  -h, --help  show this help message and exit
```

### `tokenpak start`

Start the proxy (localhost:8766)

```
usage: tokenpak start [-h]

options:
  -h, --help  show this help message and exit
```

### `tokenpak stop`

Stop the running proxy

```
usage: tokenpak stop [-h]

options:
  -h, --help  show this help message and exit
```

### `tokenpak restart`

Restart the proxy

```
usage: tokenpak restart [-h]

options:
  -h, --help  show this help message and exit
```

### `tokenpak claude`

Launch Claude Code with tokenpak companion active

```
usage: tokenpak claude ...

positional arguments:
  extra_args
```

### `tokenpak demo`

See compression in action

```
usage: tokenpak demo [-h] [--list] [--category CATEGORY] [--recipe RECIPE]
                     [--file FILE] [--seed] [--seed-count N] [--seed-hours H]
                     [--clear]

options:
  -h, --help           show this help message and exit
  --list               List all 50 baked-in recipes
  --category CATEGORY  Filter by category (general, python, javascript,
                       markdown, config, common_patterns)
  --recipe RECIPE      Show details for a specific recipe by name
  --file FILE          Show which recipes match a given file path
  --seed               Populate dashboard with 500 realistic demo events (24h
                       window)
  --seed-count N       Number of demo events to generate (default: 500)
  --seed-hours H       Time window in hours (default: 24)
  --clear              Remove all demo data from telemetry storage
```

### `tokenpak cost`

View your API spend

```
usage: tokenpak cost [-h] [--week] [--month] [--by-model] [--export-csv]

options:
  -h, --help    show this help message and exit
  --week        Show weekly totals
  --month       Show monthly totals
  --by-model    Break down by model
  --export-csv  Export as CSV
```

### `tokenpak status`

Check proxy health

```
usage: tokenpak status [-h] [--limit LIMIT]

options:
  -h, --help     show this help message and exit
  --limit LIMIT  Max retry events to show
```

### `tokenpak logs`

Show recent proxy logs

```
usage: tokenpak logs [-h] [--lines LINES]

options:
  -h, --help            show this help message and exit
  --lines LINES, -n LINES
                        Number of log lines to show (default: 50)
```

### `tokenpak upgrade`

Open the TokenPak Pro upgrade page in your browser

```
usage: tokenpak upgrade [-h] [--print-url]

Open the canonical TokenPak Pro upgrade page in your default browser. Target
URL is https://app.tokenpak.ai/upgrade (override with TOKENPAK_UPGRADE_URL).

options:
  -h, --help   show this help message and exit
  --print-url  Print the upgrade URL to stdout instead of opening a browser
```

## Indexing

### `tokenpak index`

Index a directory for context retrieval

```
usage: tokenpak index [-h] [--status] [--budget BUDGET] [--workers WORKERS]
                      [--auto-workers] [--recalibrate]
                      [--calibration-rounds CALIBRATION_ROUNDS]
                      [--max-workers MAX_WORKERS] [--watch]
                      [--debounce DEBOUNCE] [--no-treesitter]
                      [directory]

positional arguments:
  directory             Directory to index

options:
  -h, --help            show this help message and exit
  --status              Show indexed file count by type
  --budget BUDGET
  --workers WORKERS, -w WORKERS
                        Parallel workers (default: 4)
  --auto-workers        Use hybrid calibration (static baseline + dynamic
                        adjustment)
  --recalibrate         Run static calibration before indexing
  --calibration-rounds CALIBRATION_ROUNDS
                        Calibration rounds per candidate worker count
  --max-workers MAX_WORKERS
                        Upper worker cap for auto/recalibration
  --watch               Watch directory and auto-reindex on file changes
  --debounce DEBOUNCE   Debounce delay in ms for watch mode (default: 500)
  --no-treesitter       Force regex-based code processing (skip tree-sitter)
```

### `tokenpak search`

Search indexed content

```
usage: tokenpak search [-h] [--budget BUDGET] [--top-k TOP_K] [--gaps GAPS]
                       [--inject-refs]
                       query

positional arguments:
  query            Search query

options:
  -h, --help       show this help message and exit
  --budget BUDGET
  --top-k TOP_K
  --gaps GAPS      Path to gaps.json for miss-based retrieval expansion
  --inject-refs    Enable compile-time reference injection (GitHub, URLs)
```

## Configuration

### `tokenpak route`

Manage model routing rules

```
usage: tokenpak route [-h] {list,add,remove,test,enable,disable} ...

positional arguments:
  {list,add,remove,test,enable,disable}
    list                Show all routing rules
    add                 Add a routing rule
    remove              Remove a routing rule by id
    test                Show which rule matches a prompt
    enable              Enable a routing rule
    disable             Disable a routing rule

options:
  -h, --help            show this help message and exit
```

### `tokenpak recipe`

Manage compression recipes

```
usage: tokenpak recipe [-h] {create,validate,test,benchmark} ...

positional arguments:
  {create,validate,test,benchmark}
    create              Scaffold a new custom recipe YAML file
    validate            Validate a recipe YAML against the schema
    test                Test a recipe against sample input
    benchmark           Benchmark compression ratio and speed for a recipe

options:
  -h, --help            show this help message and exit
```

### `tokenpak template`

Manage prompt templates

```
usage: tokenpak template [-h] {list,add,show,remove,use} ...

positional arguments:
  {list,add,show,remove,use}
    list                List all saved templates
    add                 Add or update a template
    show                Display a template
    remove              Delete a template
    use                 Expand a template with variables

options:
  -h, --help            show this help message and exit
```

### `tokenpak budget`

Set API budget limits

```
usage: tokenpak budget [-h] {set,status,show,history} ...

positional arguments:
  {set,status,show,history}
    set                 Configure budget limits
    status              Show current budget status
    show                Alias for status — show current budget status
    history             Show recent spend records

options:
  -h, --help            show this help message and exit
```

### `tokenpak goals`

Manage savings goals and track progress

```
usage: tokenpak goals [-h]
                      {list,detail,add,edit,delete,update,export,history,compare}
                      ...

positional arguments:
  {list,detail,add,edit,delete,update,export,history,compare}
    list                List all goals
    detail              Show details for a specific goal
    add                 Create a new goal
    edit                Edit an existing goal
    delete              Delete a goal
    update              Update goal progress
    export              Export goals to JSON
    history             Show milestone history
    compare             Compare goal progress

options:
  -h, --help            show this help message and exit
```

### `tokenpak config`

Config sync, pull, validate (version control)

```
usage: tokenpak config [-h] {sync,pull,validate,show,init,path} ...

positional arguments:
  {sync,pull,validate,show,init,path}
    sync                Sync config from canonical source
    pull                Pull config from git or URL
    validate            Validate config against schema
    show                Show merged config (file + env overrides)
    init                Create default config.yaml
    path                Print config file path

options:
  -h, --help            show this help message and exit
```

## Versioning

### `tokenpak version`

Show current versions (proxy, config, cli)

```
usage: tokenpak version [-h]

options:
  -h, --help  show this help message and exit
```

### `tokenpak update`

Update TokenPak to latest from git/pypi

```
usage: tokenpak update [-h] [--check] [--force] [--core-only] [--dry-run]

options:
  -h, --help   show this help message and exit
  --check      Check for updates without installing
  --force      Force update even if already up to date
  --core-only  Update core only, skip config merge
  --dry-run    Show what would change without applying
```

## Operations

### `tokenpak benchmark`

Run compression benchmarks

```
usage: tokenpak benchmark [-h] [--file PATH] [--samples] [--json] [--latency]
                          [--iterations ITERATIONS] [--compare]
                          [directory]

positional arguments:
  directory             Directory to benchmark (used with --latency mode)

options:
  -h, --help            show this help message and exit
  --file PATH           Benchmark a specific file
  --samples             Use built-in sample data (default when no
                        file/directory given)
  --json                Output results as JSON
  --latency             Run latency/indexing benchmark instead of compression
                        benchmark
  --iterations ITERATIONS
                        Iterations for latency benchmark (default: 3)
  --compare             Compare baseline vs optimized (latency mode only)
```

### `tokenpak calibrate`

Calibrate worker count for this host

```
usage: tokenpak calibrate [-h] [--max-workers MAX_WORKERS] [--rounds ROUNDS]
                          directory

positional arguments:
  directory             Directory to sample for calibration

options:
  -h, --help            show this help message and exit
  --max-workers MAX_WORKERS
  --rounds ROUNDS
```

### `tokenpak doctor`

Run diagnostics

```
usage: tokenpak doctor [-h] [--fix] [--fleet] [--deploy] [--claude-code]
                       [--privacy] [--conformance] [--intent] [--explain-last]
                       [--json]

options:
  -h, --help      show this help message and exit
  --fix           Auto-fix issues where possible
  --fleet         Check all agents in ~/.tokenpak/fleet.yaml
  --deploy        Push latest doctor to all agents (use with --fleet)
  --claude-code   Run Claude Code-specific checks (companion settings, drift,
                  base-url routing)
  --privacy       Summarize TokenPak's privacy posture (what stays local, what
                  leaves, where compliance docs live)
  --conformance   Run TIP-1.0 self-conformance checks (capability set,
                  profiles, manifests, live emissions)
  --intent        (Phase 0.1) Show the Intent Layer Phase 0 diagnostic view:
                  classifier activation, proxy self-capability publication,
                  per-adapter §4.3 gate declaration, and whether wire emission
                  is currently enabled on this host.
  --explain-last  (Phase 0.1) Render the most recent intent_events row
                  (contract_id, intent_class, confidence, slots,
                  catch_all_reason, tip_headers_emitted/stripped). Read-only.
  --json          Emit machine-readable JSON instead of the human table
                  (applies to --conformance, --intent, --explain-last)
```

### `tokenpak dashboard`

Real-time health dashboard (TUI)

```
usage: tokenpak dashboard [-h] [--fleet] [--json] [--public] [--show-token]
                          [--new-token]

options:
  -h, --help    show this help message and exit
  --fleet       Show fleet-wide summary (TUI)
  --json        Export dashboard as JSON (non-interactive)
  --public      Show public URL with token (accessible from any machine)
  --show-token  Display current dashboard token
  --new-token   Regenerate dashboard token
```

### `tokenpak timeline`

View savings trend over 7/30 days

```
usage: tokenpak timeline [-h] [--days DAYS] [--chart] [--json]

options:
  -h, --help   show this help message and exit
  --days DAYS  Number of days (default 7)
  --chart      Show ASCII sparkline chart
  --json       JSON output
```

### `tokenpak attribution`

View savings by agent/skill/model

```
usage: tokenpak attribution [-h] [--days DAYS] [--agent AGENT] [--model MODEL]
                            [--json]

options:
  -h, --help     show this help message and exit
  --days DAYS    Number of days (default 7)
  --agent AGENT  Filter by agent name
  --model MODEL  Filter by model
  --json         JSON output
```

### `tokenpak models`

Show per-model usage and efficiency breakdown

```
usage: tokenpak models [-h] [--raw] [model]

positional arguments:
  model       Show details for a specific model (partial match, e.g. 'sonnet',
              'gpt-4')

options:
  -h, --help  show this help message and exit
  --raw       Output as JSON
```

### `tokenpak forecast`

Cost burn rate & projections

```
usage: tokenpak forecast [-h] [--period {7d,30d,90d}] [--alert USD]

options:
  -h, --help            show this help message and exit
  --period {7d,30d,90d}
                        Analysis window (default: 7d)
  --alert USD           Alert if monthly projection exceeds this USD amount
```

### `tokenpak debug`

Toggle verbose debug logging

```
usage: tokenpak debug [-h] {on,off,status} ...

positional arguments:
  {on,off,status}
    on             Enable debug mode
    off            Disable debug mode
    status         Show debug mode state

options:
  -h, --help       show this help message and exit
```

### `tokenpak learn`

View/reset learned patterns

```
usage: tokenpak learn [-h] {status,reset} ...

positional arguments:
  {status,reset}
    status        Show learned patterns summary
    reset         Clear all learned data

options:
  -h, --help      show this help message and exit
```

### `tokenpak vault-health`

Vault index health diagnostic and repair

```
usage: tokenpak vault-health [-h] {repair} ...

positional arguments:
  {repair}
    repair    Check and rebuild stale vault index

options:
  -h, --help  show this help message and exit
```

### `tokenpak fleet`

Multi-machine proxy fleet status

```
usage: tokenpak fleet [-h] [--json] [--compact] {init} ...

positional arguments:
  {init}
    init      Interactively configure fleet

options:
  -h, --help  show this help message and exit
  --json      Output as JSON
  --compact   Compact one-line output
```

### `tokenpak aggregate`

Aggregate request ledger across machines

```
usage: tokenpak aggregate [-h] [--since SINCE] [--json]

options:
  -h, --help     show this help message and exit
  --since SINCE  Time window, e.g. 7d, 24h, 30m, or ISO date
  --json         JSON output
```

### `tokenpak requests`

Live request explorer

```
usage: tokenpak requests [-h] [--limit LIMIT] [--once] [action] [request_id]

positional arguments:
  action                tail | show | <request_id>
  request_id            Request id (for show)

options:
  -h, --help            show this help message and exit
  --limit LIMIT, -n LIMIT
                        Number of rows to show
  --once                Print once and exit
```

### `tokenpak intent`

Intent Layer observation + reporting (read-only)

```
usage: tokenpak intent [-h]
                       {report,policy-preview,suggestions,config,patches} ...

positional arguments:
  {report,policy-preview,suggestions,config,patches}
    report              Summarize the intent_events telemetry over a window.
                        Read-only; never reads or emits raw prompt text.
    policy-preview      Show the latest intent policy decision (Phase 2.1 dry-
                        run; read-only).
    suggestions         Show the latest dry-run policy suggestion (Phase
                        2.4.1; internal/dev inspector; read-only).
    config              Show or validate the intent_policy config (Phase
                        2.4.3; read-only).
    patches             Show the latest dry-run prompt-patch (PI-1;
                        internal/dev inspector; read-only; not applied).

options:
  -h, --help            show this help message and exit
```

## Advanced

### `tokenpak trigger`

Manage event triggers

```
usage: tokenpak trigger [-h]
                        {list,add,remove,test,log,daemon,fire,hook,watch} ...

positional arguments:
  {list,add,remove,test,log,daemon,fire,hook,watch}
    list                List all triggers
    add                 Register a new trigger
    remove              Remove a trigger by id
    test                Dry-run: show which triggers match an event
    log                 Show recent trigger fire log
    daemon              Start background trigger daemon
    fire                Fire an event string and execute matching triggers
    hook                Install/uninstall git hooks for trigger events
    watch               Start file watcher for file:changed events

options:
  -h, --help            show this help message and exit
```

### `tokenpak macro`

Manage and run macros

```
usage: tokenpak macro [-h] {list,create,run,show,delete,install,hooks} ...

positional arguments:
  {list,create,run,show,delete,install,hooks}
    list                List all macros (premade + user-defined)
    create              Create a user-defined YAML macro
    run                 Run a macro (YAML or premade)
    show                Show a macro definition
    delete              Delete a user-defined YAML macro
    install             Install a premade macro as a local file
    hooks               Manage proxy lifecycle script hooks

options:
  -h, --help            show this help message and exit
```

### `tokenpak fingerprint`

Fingerprint sync and cache management

```
usage: tokenpak fingerprint [-h] {sync,cache,clear-cache} ...

positional arguments:
  {sync,cache,clear-cache}
    sync                Generate and sync a fingerprint, receive directives
    cache               Show local directive cache status
    clear-cache         Clear cached directives

options:
  -h, --help            show this help message and exit
```

### `tokenpak agent`

Agent coordination (locks, registry)

```
usage: tokenpak agent [-h]
                      {lock,unlock,locks,list,register,deregister,heartbeat,match,prune,handoff}
                      ...

positional arguments:
  {lock,unlock,locks,list,register,deregister,heartbeat,match,prune,handoff}
    lock                Claim a file lock
    unlock              Release a file lock
    locks               List all active locks
    list                List registered agents
    register            Register this agent
    deregister          Remove an agent from registry
    heartbeat           Send heartbeat for an agent
    match               Find agents matching requirements
    prune               Remove stale agents
    handoff             Context handoff between agents

options:
  -h, --help            show this help message and exit
```

### `tokenpak lock`

File lock management

```
usage: tokenpak lock [-h] {claim,release,query,list,renew} ...

positional arguments:
  {claim,release,query,list,renew}
    claim               Claim a lock on a file or directory
    release             Release a held lock
    query               Query who holds a lock on a path
    list                List all active locks
    renew               Renew (heartbeat) a held lock to extend its TTL

options:
  -h, --help            show this help message and exit
```

### `tokenpak run`

Schedule and manage macro runs

```
usage: tokenpak run [-h] {cron,at,list,cancel} ...

positional arguments:
  {cron,at,list,cancel}
    cron                Schedule a macro on a cron expression
    at                  Schedule a one-shot macro run at a specific time
    list                List all scheduled macro runs
    cancel              Cancel a scheduled macro run

options:
  -h, --help            show this help message and exit
```

### `tokenpak replay`

Inspect and re-run captured sessions

```
usage: tokenpak replay [-h] {list,show,run,clear} ...

positional arguments:
  {list,show,run,clear}
    list                List recent captured sessions
    show                Show full details of a captured session
    run                 Re-run a session with different settings (zero API
                        cost)
    clear               Remove all entries from the replay store

options:
  -h, --help            show this help message and exit
```

### `tokenpak audit`

Enterprise audit log management

```
usage: tokenpak audit [-h] {list,export,verify,prune,summary} ...

positional arguments:
  {list,export,verify,prune,summary}
    list                List audit log entries
    export              Export audit log to file
    verify              Verify hash chain integrity
    prune               Remove entries older than retention window
    summary             Show audit log summary stats

options:
  -h, --help            show this help message and exit
```

### `tokenpak compliance`

Generate compliance reports

```
usage: tokenpak compliance [-h] {report} ...

positional arguments:
  {report}
    report    Generate a compliance report

options:
  -h, --help  show this help message and exit
```

### `tokenpak validate`

Validate a TokenPak JSON file

```
usage: tokenpak validate [-h] [--verbose] [--json] file

positional arguments:
  file           Path to the .json TokenPak file

options:
  -h, --help     show this help message and exit
  --verbose, -v  Show quality hints in addition to errors/warnings
  --json         Output validation result as JSON
```

### `tokenpak config-check`

Validate proxy config file

```
usage: tokenpak config-check [-h] file

positional arguments:
  file        Path to config file (JSON)

options:
  -h, --help  show this help message and exit
```

### `tokenpak diff`

Show context changes (Pro)

```
usage: tokenpak diff [-h] [--verbose] [--json] [--since TIMESTAMP]

options:
  -h, --help         show this help message and exit
  --verbose, -v      Show token counts per block
  --json             Output as JSON
  --since TIMESTAMP  Diff from specific time
```

### `tokenpak stats`

Show registry stats

```
usage: tokenpak stats [-h]

options:
  -h, --help  show this help message and exit
```

### `tokenpak serve`

Start proxy/telemetry server (low-level)

```
usage: tokenpak serve [-h] [--port PORT] [--telemetry] [--ingest]
                      [--workers WORKERS] [--shutdown-timeout SECONDS]

options:
  -h, --help            show this help message and exit
  --port PORT
  --telemetry           Start telemetry ingest server
  --ingest              Start Phase 5A ingest API server
  --workers WORKERS     Number of uvicorn workers
  --shutdown-timeout SECONDS
                        Seconds to wait for in-flight requests to complete
                        before forcing shutdown (default: 30, or
                        TOKENPAK_SHUTDOWN_TIMEOUT env var)
```
