# CLI Reference

_Auto-generated from `tokenpak/cli.py` — do not edit by hand._
_To update: edit `tokenpak/cli.py` then run `python scripts/generate-cli-docs.py`._

---

## Group: Getting Started

### `tokenpak start`

Start the proxy (localhost:8766)

### `tokenpak stop`

Stop the running proxy

### `tokenpak restart`

Restart the proxy

### `tokenpak demo`

See compression in action

**Flags:**

- `--list` — List all 50 baked-in recipes
- `--category` — Filter by category (general, python, javascript, markdown, config, common_patterns)
- `--recipe` — Show details for a specific recipe by name
- `--file` — Show which recipes match a given file path
- `--seed` — Populate dashboard with 500 realistic demo events (24h window)
- `--seed-count` — Number of demo events to generate (default: 500) (default: 500)
- `--seed-hours` — Time window in hours (default: 24) (default: 24)
- `--clear` — Remove all demo data from telemetry storage

### `tokenpak cost`

View your API spend

**Flags:**

- `--week` — Show weekly totals
- `--month` — Show monthly totals
- `--by-model` — Break down by model
- `--export-csv` — Export as CSV

### `tokenpak status`

Check proxy health

**Flags:**

- `--limit` — Max retry events to show (default: 20)

### `tokenpak logs`

Show recent proxy logs

**Flags:**

- `--lines`, `-n` — Number of log lines to show (default: 50) (default: 50)

---

## Group: Indexing

### `tokenpak index`

Index a directory for context retrieval

**Flags:**

- `DIRECTORY` — Directory to index
- `--status` — Show indexed file count by type
- `--budget` — default: 8000
- `--workers`, `-w` — Parallel workers (default: 4) (default: 4)
- `--auto-workers` — Use hybrid calibration (static baseline + dynamic adjustment)
- `--recalibrate` — Run static calibration before indexing
- `--calibration-rounds` — Calibration rounds per candidate worker count (default: 2)
- `--max-workers` — Upper worker cap for auto/recalibration (default: 8)
- `--watch` — Watch directory and auto-reindex on file changes
- `--debounce` — Debounce delay in ms for watch mode (default: 500) (default: 500)
- `--no-treesitter` — Force regex-based code processing (skip tree-sitter)

### `tokenpak search`

Search indexed content

**Flags:**

- `QUERY` — Search query
- `--budget` — default: 8000
- `--top-k` — default: 10
- `--gaps` — Path to gaps.json for miss-based retrieval expansion (default: /home/sue/.tokenpak/gaps.jsonl)
- `--inject-refs` — Enable compile-time reference injection (GitHub, URLs)

---

## Group: Configuration

### `tokenpak route`

Manage model routing rules

**Subcommands:**

- `list`
  - `--routes` — Path to routes.yaml
- `add`
  - `--model` — Model glob pattern (e.g. 'gpt-4*', 'openai/*')
  - `--prefix` — Prompt prefix match (case-insensitive)
  - `--min-tokens` — Minimum token count (inclusive)
  - `--max-tokens` — Maximum token count (inclusive)
  - `--target` — Target model/provider (e.g. 'anthropic/claude-3-haiku-20240307')
  - `--priority` — Rule priority (lower = higher priority, default 100) (default: 100)
  - `--description` — Optional description (default: )
  - `--routes` — Path to routes.yaml
- `remove`
  - `ID` — Rule ID to remove
  - `--routes` — Path to routes.yaml
- `test`
  - `PROMPT` — Prompt text to test (default: )
  - `--model` — Model name to test against (default: )
  - `--tokens` — Token count override (default: auto-estimated)
  - `--verbose`, `-v` — Show all rules and their match status
  - `--routes` — Path to routes.yaml
- `enable`
  - `ID` — Rule ID
  - `--routes` — Path to routes.yaml
- `disable`
  - `ID` — Rule ID
  - `--routes` — Path to routes.yaml

### `tokenpak recipe`

Manage compression recipes

**Subcommands:**

- `create`
  - `NAME` — Recipe name (e.g. my-legal-cleanup)
  - `--output-dir` — Directory to write the recipe file (default: current dir) (default: .)
  - `--category` — Recipe category: python, markdown, legal, medical, etc. (default: general)
  - `--description` — Short description (default: )
  - `--match-mode` — Pattern match mode: any|extension|filename|content|path_pattern (default: extension)
  - `--ext` — File extension hint (for extension match mode) (default: txt)
  - `--domain-example` — Use a domain-specific template: legal | medical
- `validate`
  - `FILE` — Path to recipe YAML file
- `test`
  - `FILE` — Path to recipe YAML file
  - `--input-text` — Raw text to test against
  - `--input-file` — Path to a file to use as test input
  - `--filename-hint` — Filename to check pattern matching against (e.g. script.py) (default: )
- `benchmark`
  - `FILE` — Path to recipe YAML file
  - `--samples-file` — JSON file with list of sample strings (default: auto-generated)
  - `--runs` — Repetitions per sample for timing (default: 5) (default: 5)

### `tokenpak template`

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
  - `--var` — Variable substitution (repeatable) (default: [])

### `tokenpak budget`

Set API budget limits

**Subcommands:**

- `set`
  - `--daily` — Daily spend limit in USD
  - `--monthly` — Monthly spend limit in USD
  - `--alert-at` — Alert threshold %% (default 80)
  - `--hard-stop` — Block requests when limit exceeded
- `status`
- `show`
- `history`
  - `--limit` — default: 20
  - `--month` — Show this month

### `tokenpak goals`

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

### `tokenpak config`

Config sync, pull, validate (version control)

**Subcommands:**

- `sync`
  - `--source` — Config source: git (vault) or url (default: git) — choices: `git`, `url`
  - `--url` — URL for source=url
  - `--dry-run`
- `pull`
  - `--source` — default: git — choices: `git`, `url`
  - `--url` — URL for source=url
  - `--dry-run`
  - `--merge` — Merge strategy (default: merge) — choices: `replace`, `merge`, `diff`
- `validate`
- `show`
  - `--json` — Output as JSON
- `init`
  - `--force` — Overwrite existing config
- `path`

---

## Group: Versioning

### `tokenpak version`

Show current versions (proxy, config, cli)

### `tokenpak update`

Update TokenPak to latest from git/pypi

**Flags:**

- `--check` — Check for updates without installing
- `--force` — Force update even if already up to date
- `--core-only` — Update core only, skip config merge
- `--dry-run` — Show what would change without applying

---

## Group: Operations

### `tokenpak benchmark`

Run compression benchmarks

**Flags:**

- `DIRECTORY` — Directory to benchmark (used with --latency mode)
- `--file` — Benchmark a specific file
- `--samples` — Use built-in sample data (default when no file/directory given)
- `--json` — Output results as JSON
- `--latency` — Run latency/indexing benchmark instead of compression benchmark
- `--iterations` — Iterations for latency benchmark (default: 3) (default: 3)
- `--compare` — Compare baseline vs optimized (latency mode only)

### `tokenpak calibrate`

Calibrate worker count for this host

**Flags:**

- `DIRECTORY` — Directory to sample for calibration
- `--max-workers` — default: 8
- `--rounds` — default: 2

### `tokenpak doctor`

Run diagnostics

**Flags:**

- `--fix` — Auto-fix issues where possible
- `--fleet` — Check all agents in ~/.tokenpak/fleet.yaml
- `--deploy` — Push latest doctor to all agents (use with --fleet)

### `tokenpak dashboard`

Real-time health dashboard (TUI)

**Flags:**

- `--fleet` — Show fleet-wide summary (TUI)
- `--json` — Export dashboard as JSON (non-interactive)
- `--public` — Show public URL with token (accessible from any machine)
- `--show-token` — Display current dashboard token
- `--new-token` — Regenerate dashboard token

### `tokenpak timeline`

View savings trend over 7/30 days

**Flags:**

- `--days` — Number of days (default 7) (default: 7)
- `--chart` — Show ASCII sparkline chart
- `--json` — JSON output

### `tokenpak attribution`

View savings by agent/skill/model

**Flags:**

- `--days` — Number of days (default 7) (default: 7)
- `--agent` — Filter by agent name
- `--model` — Filter by model
- `--json` — JSON output

### `tokenpak models`

Show per-model usage and efficiency breakdown

**Flags:**

- `MODEL` — Show details for a specific model (partial match, e.g. 'sonnet', 'gpt-4')
- `--raw` — Output as JSON

### `tokenpak forecast`

Cost burn rate & projections

**Flags:**

- `--period` — Analysis window (default: 7d) (default: 7d) — choices: `7d`, `30d`, `90d`
- `--alert` — Alert if monthly projection exceeds this USD amount

### `tokenpak debug`

Toggle verbose debug logging

**Subcommands:**

- `on`
- `off`
- `status`

### `tokenpak learn`

View/reset learned patterns

**Subcommands:**

- `status`
- `reset`

### `tokenpak vault-health`

Vault index health diagnostic and repair

**Subcommands:**

- `repair`

### `tokenpak fleet`

Multi-machine proxy fleet status

**Flags:**

- `--json` — Output as JSON
- `--compact` — Compact one-line output

**Subcommands:**

- `init`

### `tokenpak aggregate`

Aggregate request ledger across machines

**Flags:**

- `--since` — Time window, e.g. 7d, 24h, 30m, or ISO date (default: 7d)
- `--json` — JSON output

### `tokenpak requests`

Live request explorer

**Flags:**

- `ACTION` — tail | show | <request_id> (default: tail)
- `REQUEST_ID` — Request id (for show)
- `--limit`, `-n` — Number of rows to show (default: 10)
- `--once` — Print once and exit

---

## Group: Advanced

### `tokenpak trigger`

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
  - `--limit` — default: 20
  - `--json` — Output raw JSON
- `daemon`
- `fire`
  - `EVENT` — Event string to fire (e.g. git:push, agent:finished:cali)
- `hook`
- `watch`
  - `PATHS` — Paths to watch (default: .)

### `tokenpak macro`

Manage and run macros

**Subcommands:**

- `list`
- `create`
  - `--name` — Macro name (e.g., my-deploy)
  - `--description` — Short description (default: )
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
- `hooks`

### `tokenpak fingerprint`

Fingerprint sync and cache management

**Subcommands:**

- `sync`
  - `TEXT` — Prompt text (or omit to read from stdin)
  - `--file`, `-f` — Read prompt from file
  - `--messages` — OpenAI messages JSON file
  - `--dry-run` — Show what would be sent without transmitting
  - `--privacy` — default: standard — choices: `minimal`, `standard`, `full`
  - `--ttl` — Cache TTL in seconds (default 3600) (default: 3600)
  - `--skip-cache`
  - `--json`
- `cache`
  - `--json`
- `clear-cache`
  - `--id` — Clear only this fingerprint ID (default: all)
  - `--yes`, `-y` — Skip confirmation prompt

### `tokenpak agent`

Agent coordination (locks, registry)

**Subcommands:**

- `lock`
  - `PATH` — File path to lock
  - `--timeout` — Lock TTL in seconds (default 600) (default: 600)
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
  - `--specialties` — Specialties (e.g., code research) (default: [])
  - `--providers` — Provider access (default: ['anthropic'])
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
  - `--specialty` — Required specialties (default: [])
  - `--provider` — Required providers (default: [])
  - `--json` — JSON output
- `prune`
- `handoff`

### `tokenpak lock`

File lock management

**Subcommands:**

- `claim`
  - `PATH` — File or directory path to lock
  - `--timeout` — Lock TTL in seconds (default 1800 = 30 min) (default: 1800)
  - `--agent` — Agent id override
- `release`
  - `PATH` — File or directory path to release
  - `--agent` — Agent id override
- `query`
  - `PATH` — File or directory path to query
  - `--agent` — Agent id override (for manager context)
- `list`
  - `--agent` — Filter by agent id (display context only)
- `renew`
  - `PATH` — File or directory path to renew
  - `--timeout` — New TTL in seconds (default 1800 = 30 min) (default: 1800)
  - `--agent` — Agent id override

### `tokenpak run`

Schedule and manage macro runs

**Subcommands:**

- `cron`
  - `NAME` — Macro name
  - `--cron` — Cron expression e.g. "0 9 * * 1-5"
  - `--description` — Optional description (default: )
- `at`
  - `NAME` — Macro name
  - `--at` — Time string e.g. "2026-03-06 09:00" or "now + 1 hour"
  - `--description` — Optional description (default: )
- `list`
- `cancel`
  - `ID` — Schedule ID to cancel

### `tokenpak replay`

Inspect and re-run captured sessions

**Subcommands:**

- `list`
  - `--limit` — Max entries to show (default 20) (default: 20)
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

### `tokenpak audit`

Enterprise audit log management

**Subcommands:**

- `list`
  - `--since` — Filter entries since date (ISO format, e.g. 2026-01-01)
  - `--until` — Filter entries until date
  - `--user` — Filter by user ID
  - `--action` — Filter by action type
  - `--model` — Filter by model name
  - `--outcome` — Filter by outcome (ok/auth_failure/...)
  - `--limit` — Max results (default: 50) (default: 50)
  - `--json` — Output as JSON
  - `--db` — Audit DB path
- `export`
  - `OUTPUT` — Output file path
  - `--format` — Export format (default: json) (default: json) — choices: `json`, `csv`
  - `--since`
  - `--until`
  - `--user`
  - `--db` — Audit DB path
- `verify`
  - `--db` — Audit DB path
- `prune`
  - `--days` — Retention window in days (default: 90) (default: 90)
  - `--db` — Audit DB path
- `summary`
  - `--since`
  - `--db`

### `tokenpak compliance`

Generate compliance reports

**Subcommands:**

- `report`
  - `--standard` — Compliance standard to report against — choices: `soc2`, `gdpr`, `ccpa`
  - `--since` — Report period start date (ISO)
  - `--until` — Report period end date (ISO)
  - `--org` — Organization name for the report
  - `--output` — Save report to file (.json or .txt)
  - `--format` — default: text — choices: `json`, `text`
  - `--db` — Audit DB path

### `tokenpak validate`

Validate a TokenPak JSON file

**Flags:**

- `FILE` — Path to the .json TokenPak file
- `--verbose`, `-v` — Show quality hints in addition to errors/warnings
- `--json` — Output validation result as JSON

### `tokenpak config-check`

Validate proxy config file

**Flags:**

- `FILE` — Path to config file (JSON)

### `tokenpak diff`

Show context changes (Pro)

**Flags:**

- `--verbose`, `-v` — Show token counts per block
- `--json` — Output as JSON
- `--since` — Diff from specific time

### `tokenpak stats`

Show registry stats

### `tokenpak serve`

Start proxy/telemetry server (low-level)

**Flags:**

- `--port` — default: 8766
- `--telemetry` — Start telemetry ingest server
- `--ingest` — Start Phase 5A ingest API server
- `--workers` — Number of uvicorn workers
- `--shutdown-timeout` — Seconds to wait for in-flight requests to complete before forcing shutdown (default: 30, or TOKENPAK_SHUTDOWN_TIMEOUT env var)

---

## Additional Commands

### `tokenpak check-alerts`

Evaluate alert rules and return exit code 1 if any fired.

### `tokenpak compare`

Show before/after cost comparison for last N requests.

**Flags:**

- `--last` — Show last N requests (default: 1)

### `tokenpak help`

Show tier-aware help. Pass a command name for details, or --minimal for compact list.

**Flags:**

- `CMD_NAME` — Command name for detailed help
- `--more` — Show essential + intermediate commands
- `--all` — Show all commands
- `--minimal` — Show compact one-line command list

### `tokenpak leaderboard`

Show per-model efficiency ranking.

**Flags:**

- `--days` — Rolling window in days (default: today)

### `tokenpak preview`

Preview compression result for input text (dry-run).

**Flags:**

- `INPUT` — Input text to preview (or reads from stdin)
- `--file` — Read input from file instead of command line
- `--raw` — Show raw compression output (no formatting)
- `--verbose` — Show detailed block breakdown
- `--json` — Output as JSON (machine-readable)

### `tokenpak report`

Generate and display daily savings report.

**Flags:**

- `--markdown` — Output markdown format (for messaging)
- `--json` — Output JSON format

### `tokenpak savings`

Show compression savings summary.

**Flags:**

- `--days` — Rolling window in days (default: 30)

### `tokenpak setup`

Interactive wizard for first-time TokenPak configuration.

### `tokenpak usage`

Show model token usage summary.

**Flags:**

- `--days` — Rolling window in days (default: 30)

---

