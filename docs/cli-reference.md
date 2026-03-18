# CLI Reference

Complete reference for all `tokenpak` commands.

---

## Status & Health

### `tokenpak status`
Check proxy health and session stats.

```bash
tokenpak status [--full]
```

| Flag | Description |
|------|-------------|
| `--full` | Include compression pipeline details and recent request log |

---

### `tokenpak health`
Full system health check including DB, index, and proxy subsystems.

```bash
tokenpak health
```

---

### `tokenpak logs`
View proxy logs.

```bash
tokenpak logs [--errors] [--today] [--tail N]
```

| Flag | Description |
|------|-------------|
| `--errors` | Show only error-level entries |
| `--today` | Filter to today's logs |
| `--tail N` | Show last N lines (default: 50) |

---

### `tokenpak doctor`
Comprehensive diagnostics — checks config, proxy, DB, compression pipeline, and connectivity.

```bash
tokenpak doctor
```

---

## Proxy Server

### `tokenpak serve`
Start the proxy server.

```bash
tokenpak serve [--port PORT] [--mode MODE] [--daemon]
```

| Flag | Default | Description |
|------|---------|-------------|
| `--port` | `8766` | Port to listen on |
| `--mode` | `hybrid` | Compression mode: `strict`, `hybrid`, `aggressive` |
| `--daemon` | off | Run in background |

**Compression modes:**

| Mode | Behavior |
|------|----------|
| `strict` | Only compress if token count exceeds threshold |
| `hybrid` | Compress medium+ requests, pass through small ones |
| `aggressive` | Always compress |

---

### `tokenpak stop`
Stop the background proxy daemon.

```bash
tokenpak stop
```

---

## Cost & Budget

### `tokenpak cost`
View cost breakdown.

```bash
tokenpak cost [--today] [--week] [--month] [--by-model] [--by-agent] [--export FORMAT]
```

| Flag | Description |
|------|-------------|
| `--today` | Today's costs |
| `--week` | This week |
| `--month` | This month |
| `--by-model` | Break down by model |
| `--by-agent` | Break down by agent |
| `--export csv\|json` | Export to file |

---

### `tokenpak budget`
Manage spending limits.

```bash
tokenpak budget set --monthly AMOUNT
tokenpak budget alert --at PCT
tokenpak budget status
tokenpak budget reset
```

| Subcommand | Description |
|-----------|-------------|
| `set --monthly N` | Set monthly budget in USD |
| `alert --at PCT` | Alert when budget reaches PCT% (e.g. 80%) |
| `status` | Show current budget and spend |
| `reset` | Clear budget and spend history |

---

### `tokenpak savings`
View cumulative token savings.

```bash
tokenpak savings [--lifetime] [--export csv]
```

---

## Compression

### `tokenpak demo`
Run the compression pipeline on a sample and show what was removed.

```bash
tokenpak demo [--verbose] [--file PATH]
```

---

### `tokenpak compress`
Dry-run compression on a file (no request sent).

```bash
tokenpak compress <file> [--diff] [--mode MODE]
```

| Flag | Description |
|------|-------------|
| `--diff` | Show a diff of what was removed |
| `--mode` | Override compression mode |

---

### `tokenpak trace`
Inspect a pipeline execution trace.

```bash
tokenpak trace [--id ID] [--last]
```

---

## Vault & Indexing

### `tokenpak index`
Index a directory for semantic search.

```bash
tokenpak index [PATH] [--watch] [--status] [--auto-workers] [--max-workers N]
```

| Flag | Description |
|------|-------------|
| `PATH` | Directory to index (default: current dir) |
| `--watch` | Auto re-index when files change |
| `--status` | Show index health without re-indexing |
| `--auto-workers` | Use calibration profile to set parallelism |
| `--max-workers N` | Cap at N parallel workers |

---

### `tokenpak vault search`
Semantic search over the indexed vault.

```bash
tokenpak vault search "query" [--top N] [--type TYPE]
```

| Flag | Description |
|------|-------------|
| `--top N` | Return top N results (default: 5) |
| `--type` | Filter by block type: `code`, `text`, `config` |

---

### `tokenpak vault blocks`
Inspect indexed content blocks.

```bash
tokenpak vault blocks [--stale] [--type TYPE] [--path PATTERN]
```

---

## Benchmarking & Calibration

### `tokenpak benchmark`
Measure compression performance and token savings.

```bash
tokenpak benchmark PATH [--iterations N] [--compare]
```

| Flag | Description |
|------|-------------|
| `--iterations N` | Runs per file (default: 3) |
| `--compare` | Show baseline vs optimized comparison |

---

### `tokenpak calibrate`
Profile your hardware to find optimal worker count.

```bash
tokenpak calibrate PATH [--max-workers N] [--rounds N]
```

| Flag | Description |
|------|-------------|
| `--max-workers N` | Maximum workers to test |
| `--rounds N` | Test rounds per worker count (default: 2) |

Saves profile to `~/.tokenpak/calibration.json`.

---

## Model Routing

### `tokenpak route`
Manage request routing rules.

```bash
tokenpak route set PATTERN MODEL
tokenpak route test "prompt text"
tokenpak route list
tokenpak route history
tokenpak route remove PATTERN
```

**Examples:**

```bash
# Route test/debug queries to a cheaper model
tokenpak route set ".*test.*" gpt-4o-mini
tokenpak route set ".*debug.*" claude-haiku-3-5

# Preview what model a prompt would be routed to
tokenpak route test "write unit tests for auth.py"
# → gpt-4o-mini (matched: .*test.*)
```

---

## Agent Management

### `tokenpak agent`
Manage registered agents.

```bash
tokenpak agent list
tokenpak agent register NAME [--metadata KEY=VALUE]
tokenpak agent unregister NAME
tokenpak agent tasks [--queue] [--agent NAME]
tokenpak agent lock FILE [--ttl SECONDS]
```

---

## Event Triggers

### `tokenpak trigger`
Configure event-based automations.

```bash
tokenpak trigger list
tokenpak trigger add EVENT_TYPE CONDITION COMMAND
tokenpak trigger remove ID
tokenpak trigger log [--last N]
```

**Event types:**

| Type | Condition | Example |
|------|-----------|---------|
| `file-change` | Glob pattern | `"*.py"` |
| `cost-alert` | Percentage | `80%` |
| `model-change` | Model name | `"gpt-4o"` |

```bash
tokenpak trigger add file-change "*.py" "bash lint.sh"
tokenpak trigger add cost-alert 80% "notify --urgent"
```

---

## A/B Testing

### `tokenpak ab`
Run controlled compression experiments.

```bash
tokenpak ab create NAME --variant-a DESC --variant-b DESC
tokenpak ab status NAME
tokenpak ab apply NAME
tokenpak ab presets
tokenpak ab list
```

---

## Replay & Debug

### `tokenpak replay`
Replay a past request (useful for testing recipe changes).

```bash
tokenpak replay list [--last N]
tokenpak replay ID [--no-compress] [--model MODEL] [--diff]
```

| Flag | Description |
|------|-------------|
| `--no-compress` | Replay without compression (baseline) |
| `--model` | Override model for replay |
| `--diff` | Show diff vs original response |

---

### `tokenpak debug`
Toggle detailed request logging.

```bash
tokenpak debug on [--requests N]   # capture next N requests
tokenpak debug off
tokenpak debug status
```

---

## Templates

### `tokenpak template`
Manage prompt templates.

```bash
tokenpak template list
tokenpak template create NAME
tokenpak template use NAME [--var KEY=VALUE]
tokenpak template export NAME [--output FILE]
tokenpak template delete NAME
```

---

## Recipes

### `tokenpak recipe`
Manage compression recipes.

```bash
tokenpak recipe list
tokenpak recipe create NAME [--category CAT] [--domain-example EXAMPLE]
tokenpak recipe validate FILE
tokenpak recipe test FILE --input-file INPUT
tokenpak recipe benchmark FILE [--runs N]
tokenpak recipe install FILE
tokenpak recipe remove NAME
```

See [Recipe Development](guides/recipes.md) for full details.

---

## Audit & Compliance

### `tokenpak audit`
View and export the audit log.

```bash
tokenpak audit list [--since DATE] [--user ID] [--model MODEL]
tokenpak audit export [--format json|csv] [--output FILE]
tokenpak audit prune [--older-than DAYS]
```

---

### `tokenpak compliance`
Generate compliance reports.

```bash
tokenpak compliance report --standard soc2|gdpr|ccpa [--output FILE]
tokenpak compliance status
```

---

## Configuration & Maintenance

### `tokenpak config`
Get or set configuration values.

```bash
tokenpak config get KEY
tokenpak config set KEY VALUE
tokenpak config list
tokenpak config export [--output FILE]
tokenpak config reset
```

**Common config keys:**

| Key | Default | Description |
|-----|---------|-------------|
| `proxy.port` | `8766` | Proxy listen port |
| `proxy.passthrough_url` | OpenAI | Default upstream URL |
| `compression.enabled` | `true` | Master compression switch |
| `compression.level` | `balanced` | `minimal`, `balanced`, `aggressive` |
| `budget.monthly_usd` | `null` | Monthly spend limit |
| `budget.alert_at_pct` | `80` | Alert threshold (%) |
| `vault.watch` | `false` | Auto re-index on changes |

---

### `tokenpak prune`
Remove old data from the local database.

```bash
tokenpak prune [--older-than DURATION] [--dry-run]
```

| Flag | Description |
|------|-------------|
| `--older-than` | Duration string: `30d`, `3m`, `1y` |
| `--dry-run` | Show what would be deleted without deleting |

---

## Global Flags

These flags work with any command:

| Flag | Description |
|------|-------------|
| `--json` | Output as JSON |
| `--quiet` | Suppress non-essential output |
| `--verbose` | Extra debug output |
| `--config FILE` | Use alternate config file |
| `--db FILE` | Use alternate database file |
| `--version` | Show TokenPak version |
| `--help` | Show command help |
