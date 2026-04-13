# CLI Reference

_Verified against live `tokenpak --help` output. Last audit: 2026-04-10._
_To update: run `python3 -m tokenpak <cmd> --help` for each command and cross-check this file._

**Stability labels:**
- `[stable]` — production-ready, no breaking changes expected
- `[experimental]` — shipped but subject to change
- `[planned]` — not yet available in the current release
- `[advanced]` — intentionally omitted from `tokenpak --help`; accessible via `tokenpak help` or direct invocation

> **Discoverability note:** `tokenpak --help` shows a curated subset for new users. Run `tokenpak help` to see all commands grouped by category, including advanced ones.

---

## Group: Getting Started

### `tokenpak setup` [stable]

Configure your LLM client to use tokenpak (wizard)

**Flags:**

- `--yes`, `-y` — Skip confirmation prompts (non-interactive / CI mode)

### `tokenpak start` [stable]

Start the proxy (localhost:8766)

**Flags:**

- `--port PORT` — Port to listen on (default: 8766)
- `--workers WORKERS` — Number of worker processes (default: 2)
- `--log-level {debug,info,warning,error}` — Logging level (default: info)

### `tokenpak stop` [stable]

Stop the running proxy

### `tokenpak restart` [stable]

Restart the proxy

### `tokenpak demo` [stable]

See compression in action

**Flags:**

- `--list` — List all 50 baked-in recipes
- `--category` — Filter by category (general, python, javascript, markdown, config, common_patterns)
- `--recipe` — Show details for a specific recipe by name
- `--file` — Show which recipes match a given file path
- `--seed` — Populate dashboard with 500 realistic demo events (24h window)
- `--seed-count N` — Number of demo events to generate (default: 500)
- `--seed-hours H` — Time window in hours (default: 24)
- `--clear` — Remove all demo data from telemetry storage

### `tokenpak cost` [stable]

View your API spend

**Flags:**

- `--week` — Show weekly totals
- `--month` — Show monthly totals
- `--by-model` — Break down by model
- `--export-csv` — Export as CSV

**Subcommands:**

- `show-budget` — Show budget status and alerts

### `tokenpak status` [stable]

Check proxy health

**Flags:**

- `--limit LIMIT` — Max retry events to show (default: 20)
- `--full` — Show full technical output (legacy)
- `--minimal` — One-line savings summary
- `--json` — Full JSON data dump
- `--no-meme` — Suppress tagline

### `tokenpak logs` [stable]

Show recent proxy logs

**Flags:**

- `--lines LINES`, `-n LINES` — Number of log lines to show (default: 50)

---

## Group: Indexing

### `tokenpak index` [stable]

Index a directory for context retrieval

**Flags:**

- `DIRECTORY` — Directory to index
- `--status` — Show indexed file count by type
- `--budget BUDGET` — Token budget (default: 8000)
- `--workers WORKERS`, `-w WORKERS` — Parallel workers (default: 4)
- `--auto-workers` — Use hybrid calibration (static baseline + dynamic adjustment)
- `--recalibrate` — Run static calibration before indexing
- `--calibration-rounds CALIBRATION_ROUNDS` — Calibration rounds per candidate worker count (default: 2)
- `--max-workers MAX_WORKERS` — Upper worker cap for auto/recalibration (default: 8)
- `--watch` — Watch directory and auto-reindex on file changes
- `--debounce DEBOUNCE` — Debounce delay in ms for watch mode (default: 500)
- `--no-treesitter` — Force regex-based code processing (skip tree-sitter)

### `tokenpak search` [stable] [advanced]

Search indexed content. Not shown in `tokenpak --help`; run `tokenpak help` or invoke directly.

**Flags:**

- `QUERY` — Search query
- `--budget BUDGET` — Token budget (default: 8000)
- `--top-k TOP_K` — Number of results to return (default: 10)
- `--gaps GAPS` — Path to gaps.json for miss-based retrieval expansion
- `--inject-refs` — Enable compile-time reference injection (GitHub, URLs)

---

## Group: Configuration

### `tokenpak route` [stable] [advanced]

Manage model routing rules. Not shown in `tokenpak --help`; run `tokenpak help` or invoke directly.

**Subcommands:**

- `list`
  - `--routes` — Path to routes.yaml
- `add`
  - `--model` — Model glob pattern (e.g. 'gpt-4*', 'openai/*')
  - `--prefix` — Prompt prefix match (case-insensitive)
  - `--min-tokens` — Minimum token count (inclusive)
  - `--max-tokens` — Maximum token count (inclusive)
  - `--target` — Target model/provider (e.g. 'anthropic/claude-3-haiku-20240307')
  - `--priority` — Rule priority (lower = higher priority, default 100)
  - `--description` — Optional description
  - `--routes` — Path to routes.yaml
- `remove`
  - `ID` — Rule ID to remove
  - `--routes` — Path to routes.yaml
- `test`
  - `PROMPT` — Prompt text to test
  - `--model` — Model name to test against
  - `--tokens` — Token count override (default: auto-estimated)
  - `--verbose`, `-v` — Show all rules and their match status
  - `--routes` — Path to routes.yaml
- `enable`
  - `ID` — Rule ID
  - `--routes` — Path to routes.yaml
- `disable`
  - `ID` — Rule ID
  - `--routes` — Path to routes.yaml

### `tokenpak recipe` [stable]

Manage compression recipes

**Subcommands:**

- `create`
  - `NAME` — Recipe name (e.g. my-legal-cleanup)
  - `--output-dir` — Directory to write the recipe file (default: current dir)
  - `--category` — Recipe category: python, markdown, legal, medical, etc. (default: general)
  - `--description` — Short description
  - `--match-mode` — Pattern match mode: any|extension|filename|content|path_pattern (default: extension)
  - `--ext` — File extension hint (for extension match mode, default: txt)
  - `--domain-example` — Use a domain-specific template: legal | medical
- `validate`
  - `FILE` — Path to recipe YAML file
- `test`
  - `FILE` — Path to recipe YAML file
  - `--input-text` — Raw text to test against
  - `--input-file` — Path to a file to use as test input
  - `--filename-hint` — Filename to check pattern matching against (e.g. script.py)
- `benchmark`
  - `FILE` — Path to recipe YAML file
  - `--samples-file` — JSON file with list of sample strings (default: auto-generated)
  - `--runs` — Repetitions per sample for timing (default: 5)

### `tokenpak template` [stable]

Manage prompt templates

**Subcommands:**

- `list`
- `add`
  - `NAME` — Template name
  - `--content` — Template content (use {{var}} for variables)
- `show`
  - `NAME` — Template name
- `remove`
  - `NAME` — Template name
- `use`
  - `NAME` — Template name
  - `--var` — Variable substitution (repeatable)

### `tokenpak budget` [stable]

Set API budget limits

**Subcommands:**

- `set`
  - `--daily` — Daily spend limit in USD
  - `--monthly` — Monthly spend limit in USD
  - `--alert-at` — Alert threshold % (default 80)
  - `--hard-stop` — Block requests when limit exceeded
- `status` — Show current budget status
- `show` — Alias for status
- `history`
  - `--limit` — Number of records to show (default: 20)
  - `--month` — Show this month

### `tokenpak goals` [stable]

Manage savings goals and track progress

**Subcommands:**

- `list`
- `detail`
  - `GOAL_ID` — Goal ID
- `add`
  - `--name` — Goal name
  - `--type` — Goal type — choices: `savings`, `compression`, `cache`, `metric`
  - `--target` — Target value
  - `--start` — Start date (YYYY-MM-DD, default: today)
  - `--end` — End date (YYYY-MM-DD, default: 30 days from start)
  - `--description` — Goal description
  - `--metric` — Custom metric name (for metric type)
  - `--rolling-window` — Enable weekly pace tracking
- `edit`
  - `GOAL_ID` — Goal ID to edit
  - `--name` — New goal name
  - `--target` — New target value
  - `--description` — New description
  - `--end` — New end date (YYYY-MM-DD)
- `delete`
  - `GOAL_ID` — Goal ID to delete
- `update`
  - `GOAL_ID` — Goal ID
  - `VALUE` — New current value
- `export`
  - `--output`, `-o` — Output file (default: stdout)
- `history`
- `compare`

### `tokenpak config` [stable]

Config sync, pull, validate (version control)

**Subcommands:**

- `sync`
  - `--source` — Config source: git (vault) or url (default: git) — choices: `git`, `url`
  - `--url` — URL for source=url
  - `--dry-run`
- `pull`
  - `--source` — choices: `git`, `url` (default: git)
  - `--url` — URL for source=url
  - `--dry-run`
  - `--merge` — Merge strategy (default: merge) — choices: `replace`, `merge`, `diff`
- `validate`
- `show`
  - `--json` — Output as JSON
- `init`
  - `--force` — Overwrite existing config
- `path`
- `migrate` — Migrate legacy config.json settings into config.yaml

---

## Group: Versioning

### `tokenpak version` [stable]

Show current versions (proxy, config, cli)

### `tokenpak update` [stable]

Update TokenPak to latest from git/pypi

**Flags:**

- `--check` — Check for updates without installing
- `--force` — Force update even if already up to date
- `--core-only` — Update core only, skip config merge
- `--dry-run` — Show what would change without applying

---

## Group: Operations

### `tokenpak benchmark` [stable]

Run compression benchmarks

**Flags:**

- `DIRECTORY` — Directory to benchmark (used with --latency mode)
- `--file` — Benchmark a specific file
- `--samples` — Use built-in sample data (default when no file/directory given)
- `--json` — Output results as JSON
- `--latency` — Run latency/indexing benchmark instead of compression benchmark
- `--iterations` — Iterations for latency benchmark (default: 3)
- `--compare` — Compare baseline vs optimized (latency mode only)

### `tokenpak calibrate` [stable]

Calibrate worker count for this host

**Flags:**

- `DIRECTORY` — Directory to sample for calibration
- `--max-workers MAX_WORKERS` — Upper worker count ceiling (default: 8)
- `--rounds ROUNDS` — Calibration rounds per candidate count (default: 2)

### `tokenpak doctor` [stable]

Run diagnostics

**Flags:**

- `--fix` — Auto-fix issues where possible
- `--json` — Output results as machine-readable JSON
- `--fleet` — Check all agents in ~/.tokenpak/fleet.yaml
- `--deploy` — Push latest doctor to all agents (use with --fleet)
- `--verbose`, `-v` — Show extra detail for each check
- `--claude-code` — Run Claude Code integration checks (ENABLE_TOOL_SEARCH, mode, IDE detection)

### `tokenpak dashboard` [stable]

Real-time health dashboard (TUI)

**Flags:**

- `--fleet` — Show fleet-wide summary (TUI)
- `--json` — Export dashboard as JSON (non-interactive)
- `--public` — Show public URL with token (accessible from any machine)
- `--show-token` — Display current dashboard token
- `--new-token` — Regenerate dashboard token

### `tokenpak timeline` [stable]

View savings trend over 7/30 days

**Flags:**

- `--days DAYS` — Number of days (default: 7)
- `--chart` — Show ASCII sparkline chart
- `--json` — JSON output

### `tokenpak attribution` [stable]

View savings by agent/skill/model

**Flags:**

- `--days DAYS` — Number of days (default: 7)
- `--agent AGENT` — Filter by agent name
- `--model MODEL` — Filter by model
- `--json` — JSON output

### `tokenpak models` [stable]

Show per-model usage and efficiency breakdown

**Flags:**

- `MODEL` — Show details for a specific model (partial match, e.g. 'sonnet', 'gpt-4')
- `--raw` — Output as JSON

### `tokenpak forecast` [stable]

Cost burn rate & projections

**Flags:**

- `--period {7d,30d,90d}` — Analysis window (default: 7d)
- `--alert USD` — Alert if monthly projection exceeds this USD amount

### `tokenpak debug` [stable]

Toggle verbose debug logging

**Subcommands:**

- `on` — Enable debug mode
- `off` — Disable debug mode
- `status` — Show debug mode state
- `list` — List captured debug traces
- `export` — Decrypt and print a captured trace

### `tokenpak learn` [stable]

View/reset learned patterns

**Subcommands:**

- `status` — Show learned patterns summary
- `reset` — Clear all learned data

### `tokenpak vault-health` [stable]

Vault index health diagnostic and repair (legacy alias; prefer `tokenpak vault`)

**Subcommands:**

- `repair` — Check and rebuild stale vault index entries

### `tokenpak fleet` [stable]

Multi-machine proxy fleet status

**Flags:**

- `--json` — Output as JSON
- `--compact` — Compact one-line output

**Subcommands:**

- `init` — Interactively configure fleet

### `tokenpak aggregate` [stable]

Aggregate request ledger across machines

**Flags:**

- `--since SINCE` — Time window, e.g. 7d, 24h, 30m, or ISO date (default: 7d)
- `--json` — JSON output

### `tokenpak requests` [stable]

Live request explorer

**Flags:**

- `ACTION` — tail | show | <request_id> (default: tail)
- `REQUEST_ID` — Request id (for show)
- `--limit`, `-n` — Number of rows to show (default: 10)
- `--once` — Print once and exit

### `tokenpak monitor` [experimental]

Background proxy monitor. No additional options.

### `tokenpak diagnose` [experimental]

Run a quick diagnostic check (streamlined alternative to `doctor`)

**Flags:**

- `--json` — Output as JSON
- `--verbose` — Verbose output

### `tokenpak explain` [stable]

Explain compression profiles and their tradeoffs

**Flags:**

- `--profile PROFILE` — Profile name (safe|balanced|aggressive|agentic); omit to show all

### `tokenpak alerts` [stable]

Manage alert delivery

**Subcommands:**

- `test` — Send a test alert to a delivery channel

### `tokenpak validate-config` [stable]

Validate a proxy config file (YAML or JSON)

**Flags:**

- `FILE` — Path to config file (YAML or JSON)

### `tokenpak retrieval` [experimental]

Manage and test the retrieval pipeline

**Flags:**

- `--json` — Output as JSON

**Subcommands:**

- `status` — Show retrieval config and index stats
- `test` — Run a test query through all enabled retrievers

---

## Group: Advanced

### `tokenpak trigger` [stable]

Manage event triggers

**Subcommands:**

- `list`
  - `--json` — Output raw JSON
- `add`
  - `--event` — Event pattern (e.g. file:changed:*.py, git:commit, cost:daily>5)
  - `--action` — Action: tokenpak sub-command or shell script path
  - `--json` — Output raw JSON
- `remove`
  - `ID` — Trigger ID
  - `--json` — Output raw JSON
- `test`
  - `--event` — Event string to test
  - `--json` — Output raw JSON
- `log`
  - `--limit` — Number of entries to show (default: 20)
  - `--json` — Output raw JSON
- `daemon` — Start background trigger daemon
- `fire`
  - `EVENT` — Event string to fire (e.g. git:push, agent:finished:cali)
- `hook` — Install/uninstall git hooks for trigger events
- `watch`
  - `PATHS` — Paths to watch (default: .)

### `tokenpak macro` [stable]

Manage and run macros

**Subcommands:**

- `list`
- `create`
  - `--name` — Macro name (e.g., my-deploy)
  - `--description` — Short description
  - `--step` — Add a step (repeatable). Format: 'Label:command'
  - `--var` — Default variable (repeatable). Format: KEY=VALUE
  - `--continue-on-error` — Keep running if a step fails (default: fail-fast)
  - `--file` — Load macro definition from a YAML file
  - `--overwrite` — Overwrite an existing macro with the same name
- `run`
  - `NAME` — Macro name
  - `--dry-run` — Print commands without executing them
  - `--continue-on-error` — Keep running if a step fails
  - `--var` — Runtime variable override (repeatable)
  - `--json` — Output raw JSON
- `show`
  - `NAME` — Macro name
  - `--json` — Output raw JSON
- `delete`
  - `NAME` — Macro name
  - `--yes`, `-y` — Skip confirmation prompt
- `install`
  - `NAME` — Macro name (morning-standup, pre-deploy, weekly-report)
- `hooks` — Manage proxy lifecycle script hooks

### `tokenpak fingerprint` [stable]

Fingerprint sync and cache management

**Subcommands:**

- `sync`
  - `TEXT` — Prompt text (or omit to read from stdin)
  - `--file`, `-f` — Read prompt from file
  - `--messages` — OpenAI messages JSON file
  - `--dry-run` — Show what would be sent without transmitting
  - `--privacy {minimal,standard,full}` — Privacy level (default: standard)
  - `--ttl TTL` — Cache TTL in seconds (default: 3600)
  - `--skip-cache`
  - `--json`
- `cache`
  - `--json`
- `clear-cache`
  - `--id` — Clear only this fingerprint ID (default: all)
  - `--yes`, `-y` — Skip confirmation prompt

### `tokenpak agent` [stable]

Agent coordination (locks, registry)

**Subcommands:**

- `lock`
  - `PATH` — File path to lock
  - `--timeout` — Lock TTL in seconds (default: 600)
  - `--agent` — Agent id override
- `unlock`
  - `PATH` — File path to unlock
  - `--agent` — Agent id override
- `locks`
  - `--agent` — Filter by agent id
- `list`
  - `--all` — Include stale agents
  - `--json` — JSON output
- `register`
  - `NAME` — Agent name (e.g., trix, sue, cali)
  - `--hostname` — Hostname (default: auto-detect)
  - `--gpu` — Has GPU
  - `--memory` — Memory in GB (default: 4.0)
  - `--specialties` — Specialties (e.g., code research)
  - `--providers` — Provider access (default: anthropic)
  - `--json` — JSON output
- `deregister`
  - `AGENT_ID` — Agent ID to remove
- `heartbeat`
  - `AGENT_ID` — Agent ID
  - `--status` — Update status — choices: `active`, `busy`, `draining`
  - `--task` — Current task name
- `match`
  - `--gpu` — Require GPU
  - `--memory` — Minimum memory GB
  - `--specialty` — Required specialties
  - `--provider` — Required providers
  - `--json` — JSON output
- `prune` — Remove stale agents
- `handoff` — Context handoff between agents

### `tokenpak lock` [stable]

File lock management

**Subcommands:**

- `claim`
  - `PATH` — File or directory path to lock
  - `--timeout` — Lock TTL in seconds (default: 1800 = 30 min)
  - `--agent` — Agent id override
- `release`
  - `PATH` — File or directory path to release
  - `--agent` — Agent id override
- `query`
  - `PATH` — File or directory path to query
  - `--agent` — Agent id override
- `list`
  - `--agent` — Filter by agent id
- `renew`
  - `PATH` — File or directory path to renew
  - `--timeout` — New TTL in seconds (default: 1800 = 30 min)
  - `--agent` — Agent id override

### `tokenpak run` [stable]

Schedule and manage macro runs

**Subcommands:**

- `cron`
  - `NAME` — Macro name
  - `--cron` — Cron expression e.g. "0 9 * * 1-5"
  - `--description` — Optional description
- `at`
  - `NAME` — Macro name
  - `--at` — Time string e.g. "2026-03-06 09:00" or "now + 1 hour"
  - `--description` — Optional description
- `list`
- `cancel`
  - `ID` — Schedule ID to cancel

### `tokenpak replay` [stable] [advanced]

Inspect and re-run captured sessions. Not shown in `tokenpak --help`; run `tokenpak help` or invoke directly.

**Subcommands:**

- `list`
  - `--limit` — Max entries to show (default: 20)
  - `--provider` — Filter by provider
- `show`
  - `ID` — Replay entry ID
  - `--messages` — Print captured message content
- `run`
  - `ID` — Replay entry ID
  - `--model` — Label as a different model
  - `--no-compress` — Simulate sending uncompressed
  - `--aggressive` — Apply aggressive compression mode
  - `--diff` — Show unified diff of original vs compressed messages
- `clear`

---

## Additional Commands

### `tokenpak check-alerts` [stable]

Evaluate alert rules and return exit code 1 if any fired.

### `tokenpak compare` [stable]

Show before/after cost comparison for last N requests.

**Flags:**

- `--last` — Show last N requests (default: 1)

### `tokenpak compress` [stable]

Compress a piece of text, JSON, or code.

**Flags:**

- `--file FILE`, `-f FILE` — Input file path (reads from stdin if omitted)
- `--verbose`, `-v` — Show compression blocks
- `--json` — Output as machine-readable JSON

### `tokenpak diff` [stable]

Show context changes (Pro feature)

**Flags:**

- `--verbose`, `-v` — Show token counts per block
- `--json` — Output as JSON
- `--since TIMESTAMP` — Diff from specific time

### `tokenpak help` [stable]

Show tier-aware help. Pass a command name for details, or --minimal for compact list.

**Flags:**

- `CMD_NAME` — Command name for detailed help
- `--more` — Show essential + intermediate commands
- `--all` — Show all commands
- `--minimal` — Show compact one-line command list

### `tokenpak init` [experimental]

Initialize tokenpak in the current directory. No additional options.

### `tokenpak last` [stable]

Show details of last compressed request(s).

**Flags:**

- `--limit LIMIT` — Show last N requests (default: 1)
- `--json` — Output as JSON
- `--verbose`, `-v` — Show full request/response bodies

### `tokenpak leaderboard` [stable]

Show per-model efficiency ranking.

**Flags:**

- `--days` — Rolling window in days (default: today)

### `tokenpak optimize` [stable]

Analyze and optimize a prompt for better compression efficiency.

**Flags:**

- `--file FILE`, `-f FILE` — Input file path (reads from stdin if omitted)
- `--strategy {conservative,balanced,aggressive}` — Optimization aggressiveness (default: balanced)
- `--show-diff` — Show before/after token counts

### `tokenpak preview` [stable]

Preview compression result for input text (dry-run).

**Flags:**

- `INPUT` — Input text to preview (or reads from stdin)
- `--file FILE` — Read input from file instead of command line
- `--raw` — Show raw compression output (no formatting)
- `--verbose` — Show detailed block breakdown
- `--json` — Output as JSON (machine-readable)

### `tokenpak report` [stable]

Generate and display daily savings report.

**Flags:**

- `--markdown` — Output markdown format (for messaging)
- `--json` — Output JSON format

### `tokenpak savings` [stable]

Show compression savings summary.

**Flags:**

- `--days DAYS` — Rolling window in days (default: 30)

### `tokenpak serve` [stable]

Start proxy/telemetry server (low-level alias for `start`)

**Flags:**

- `--port PORT` — Port to listen on (default: 8766)
- `--telemetry` — Start telemetry ingest server
- `--ingest` — Start Phase 5A ingest API server
- `--workers WORKERS` — Number of uvicorn workers
- `--shutdown-timeout SECONDS` — Seconds to wait for in-flight requests to complete before forcing shutdown (default: 30, or TOKENPAK_SHUTDOWN_TIMEOUT env var)

### `tokenpak stats` [stable]

Show registry stats. No additional options.

### `tokenpak usage` [stable]

Show model token usage summary.

**Flags:**

- `--days DAYS` — Rolling window in days (default: 30)

### `tokenpak validate` [stable]

Validate a TokenPak JSON file

**Flags:**

- `FILE` — Path to the .json TokenPak file
- `--verbose`, `-v` — Show quality hints in addition to errors/warnings
- `--json` — Output validation result as JSON

### `tokenpak vault` [stable]

Vault index health diagnostic and repair

**Subcommands:**

- `repair` — Check and rebuild stale vault index entries

### `tokenpak config-check` [stable]

Validate proxy config file (JSON). See also `validate-config` for YAML support.

**Flags:**

- `FILE` — Path to config file (JSON)

---

## Planned Commands

The following commands are documented but **not yet available** in the current release:

### `tokenpak audit` [planned]

Enterprise audit log management (coming in a future release)

**Planned subcommands:** `list`, `export`, `verify`, `prune`, `summary`

### `tokenpak compliance` [planned]

Generate compliance reports against SOC2/GDPR/CCPA (coming in a future release)

**Planned subcommands:** `report`

---
