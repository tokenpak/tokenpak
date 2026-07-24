# Onboarding Guide: Day 1 → Day 30

Welcome to TokenPak. This guide takes you from a fresh install to running TokenPak in production, one milestone at a time. Each section ends with a checklist — complete it before moving on.

> **Already signed in to Codex?** The Day 1 path reuses that OAuth session and
> the client's selected/default model. API keys and model overrides are optional.

---

## Day 1: Your First Compressed Request

Your goal today: install TokenPak, send one eligible real request, and see its
measured savings receipt in the same session. The supported reference target is
three commands and no more than five minutes.

Before starting, have Python 3.10+ and an already-authenticated supported
client. The reference path uses Codex OAuth and its normal model selection; it
does not require an API-key variable or an explicit Anthropic model. Run it in
a real project. Provider usage may count against a subscription or incur
charges.

### Command 1: Install

```bash
python -m pip install tokenpak
```

### Command 2: Start the Proxy

In terminal 1:

```bash
tokenpak serve --profile aggressive --stats-footer
```

Leave this foreground process running. Both flags are session-scoped. The
receipt is printed in this terminal and does not modify the provider response.

### Command 3: Launch Your Authenticated Client

In terminal 2:

```bash
tokenpak codex
```

Make a substantive project request, then continue the same topic. A new
conversation's first request may correctly be ineligible because it has no
historical context. The first later eligible request prints the measured
before/after receipt in terminal 1. Its dollar figure is an estimate based on
the model-pricing table. See
[First Measured Savings Receipt](first-receipt.md) for the exact flow and
eligibility boundaries.

Short, protected, or already concise inputs may save zero. Protected policy and
the newest two messages are never capsulized. Byte-preserved routes are not
positive-compression proof paths. `tokenpak demo` is an offline fixture, not a
receipt from your own provider request.

After the proof, use `tokenpak integrate` to review client-specific routing.
Applying an integration is a separate, consented workflow and is not required
for the three-command reference path.

### Day 1 Checklist

- [ ] TokenPak installed with command 1
- [ ] The receipt-enabled proxy started with command 2
- [ ] Command 3 launched an already-authenticated client without requiring a key or model override
- [ ] A real eligible request produced a positive measured token receipt
- [ ] You understand that the dollar figure is estimated and workload-specific

---

## Day 3: Check Your Savings

By day three you have a few sessions of real data. Let's look at what you've saved so far and understand the numbers.

### Weekly and Lifetime Reports

```bash
tokenpak cost --week
```

This shows a breakdown by day and model — you can see which models and use cases save the most tokens.

```bash
tokenpak savings
```

This shows your cumulative savings since install: total tokens saved, estimated dollar savings, and a compression ratio per session.

### What to Look For

A healthy setup measurably reduces tokens on typical mixed workloads — check your own with `tokenpak savings`. If you're seeing little or no reduction, check these:

- Is the proxy running with a compression-capable `balanced`, `aggressive`, or
  `agentic` profile?
- Are your requests using long system prompts or repetitive context? Those compress best.
- Run `tokenpak demo` to inspect the offline fixture, then use `tokenpak savings` for receipt-backed savings.

### The 48.9% Benchmark

TokenPak's headline number — 48.9% token reduction — is reproducible on the included benchmark suite:

```bash
pytest tests/benchmarks/ -v --benchmark-json=benchmark.json
python scripts/check_benchmark_thresholds.py benchmark.json
```

The benchmark runs against the `tests/benchmarks/fixtures/` payloads, which represent real-world coding, writing, and ops tasks. Your real-world savings will vary by workload.

### Day 3 Checklist

- [ ] `tokenpak cost --week` shows data for at least 2 days
- [ ] `tokenpak savings` shows a compression ratio
- [ ] You understand how the proxy's workflow profile affects eligibility

---

## Day 7: Customize a Recipe

Recipes are the heart of TokenPak's compression. Each recipe is a YAML file that describes how to compress a specific content type — code, markdown, JSON, config files. On day seven, you'll add or modify one to fit your workload.

### Find the Built-in Recipes

```bash
ls tokenpak/recipes_oss/
```

You'll see files like `code.yaml`, `markdown.yaml`, `json.yaml`. These ship with TokenPak and work out of the box.

### Read a Recipe

Open `tokenpak/recipes_oss/markdown.yaml`. The structure looks like this:

```yaml
name: markdown
version: "1.0"
description: Compress markdown documents by removing redundant whitespace and collapsing list items
targets:
 - type: knowledge
 - type: docs
directives:
 - strip_trailing_whitespace: true
 - collapse_blank_lines: max=1
 - remove_html_comments: true
 - abbreviate_code_blocks:
 max_lines: 30
 placeholder: "# ... {n} lines omitted ..."
```

Each directive maps to a compressor in `tokenpak/agent/compression/`. The `targets` field tells the pipeline which block types this recipe applies to.

### Modify the Recipe

Say you want to keep more code lines visible. Change `max_lines: 30` to `max_lines: 50`:

```yaml
 - abbreviate_code_blocks:
 max_lines: 50
 placeholder: "# ... {n} lines omitted ..."
```

Save the file.

### Test the Change

```bash
tokenpak compress docs/onboarding.md
```

This does a dry-run — it shows you the before/after token count and the compressed output without sending anything to your LLM. Use it to verify your recipe change has the effect you expect.

```bash
tokenpak compress docs/onboarding.md --verbose
```

The `--verbose` flag shows which directives fired and how many tokens each removed.

### Create a Custom Recipe

You can create new recipes in `tokenpak/recipes_oss/` or in `~/.tokenpak/recipes/` (user-local, not overwritten on upgrade).

See [Recipe Development](guides/recipes.md) for the full YAML reference and [Recipe SDK](recipe-sdk.md) for building recipes programmatically.

### Day 7 Checklist

- [ ] You've read at least one built-in recipe YAML
- [ ] You've modified a directive and tested it with `tokenpak compress`
- [ ] `tokenpak compress` shows a before/after token count

---

## Day 14: Set a Budget and Configure Alerts

On day 14, you're using TokenPak regularly and ready to put guardrails in place. A budget prevents runaway costs; an alert tells you before you hit it.

### Set a Monthly Budget

```bash
tokenpak budget set --monthly 50
```

This sets a $50/month hard limit. Requests that would exceed the budget are blocked and return an error to your client. You can adjust this anytime.

### Configure an Alert

```bash
tokenpak budget alert --at 80%
```

This triggers a warning at 80% of your monthly budget ($40 in this example). By default the alert logs to `~/.tokenpak/logs/budget.log`.

### Configure a Slack Alert

To send alerts to Slack, add your webhook URL to `~/.tokenpak/config.json`:

```json
{
 "budget": {
 "monthly_usd": 50,
 "alert_at_pct": 80,
 "alerts": {
 "slack_webhook": "https://hooks.slack.com/services/YOUR/WEBHOOK/URL"
 }
 }
}
```

Then restart the proxy:

```bash
tokenpak stop
tokenpak serve --port 8766 &
```

### Verify the Alert Fires

Test it by temporarily setting a very low budget:

```bash
tokenpak budget set --monthly 0.01
```

Make one request through your LLM client. You should see a budget warning in the logs (and in Slack if configured). Then restore your real budget:

```bash
tokenpak budget set --monthly 50
```

### Day 14 Checklist

- [ ] `tokenpak budget set --monthly 50` ran without error
- [ ] `tokenpak budget alert --at 80%` is configured
- [ ] `~/.tokenpak/config.json` shows the correct `monthly_usd` value
- [ ] Alert triggered successfully with the low test budget
- [ ] Budget restored to working value

---

## Day 30: Deploy to Production

By now TokenPak is part of your workflow. Day 30 is about making it reliable, monitored, and ready for sustained use — whether that's a personal workstation, a team server, or a cloud VM.

### Option A: systemd Service (Linux)

Create a service file at `/etc/systemd/system/tokenpak.service`:

```ini
[Unit]
Description=TokenPak Proxy
After=network.target

[Service]
Type=simple
User=tokenpak
ExecStart=/usr/local/bin/tokenpak serve --port 8766 --workers 4
Restart=on-failure
RestartSec=5
Environment=TOKENPAK_LOG_LEVEL=info

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable tokenpak
sudo systemctl start tokenpak
sudo systemctl status tokenpak
```

### Option B: Docker

```bash
docker pull tokenpak/tokenpak:latest
docker run -d \
 --name tokenpak \
 --restart unless-stopped \
 -p 8766:8766 \
 -v ~/.tokenpak:/root/.tokenpak \
 tokenpak/tokenpak:latest
```

Or use Docker Compose for a full stack with Redis cache:

```bash
cp config/tokenpak.config.json.example config/tokenpak.config.json
# Edit config/tokenpak.config.json with your settings
docker compose --profile with-cache up -d
```

### Monitoring

Check proxy health programmatically:

```bash
curl http://localhost:8766/health
# {"status": "ok", "version": "...", "uptime_seconds": ...}
```

View the dashboard (if configured):

```bash
tokenpak cost --month --json | jq .
```

For longer-term monitoring, the `/metrics` endpoint exposes Prometheus-compatible metrics. Point Grafana or your existing monitoring stack at it.

### Audit Log Query

Every request is logged locally. Query recent activity:

```bash
tokenpak trace --last 50
```

Filter by model or cost:

```bash
tokenpak trace --last 50 --model claude-3-5-sonnet
tokenpak trace --last 50 --min-cost 0.01
```

Export the full log for analysis:

```bash
tokenpak trace --export --format json > audit.json
```

### Production Checklist

- [ ] Proxy is running as a managed service (systemd or Docker)
- [ ] Service restarts automatically on failure
- [ ] `curl http://localhost:8766/health` returns `"status": "ok"`
- [ ] Monthly budget is set
- [ ] At least one alert channel is configured
- [ ] `tokenpak trace --last 50` returns recent request data
- [ ] You know how to query the audit log

---

## Where to Go From Here

You've completed the onboarding journey. Here's what's next:

| Goal | Where to look |
|------|---------------|
| Run Claude Code / Codex with the companion (MCP tools) | [Companion & MCP Setup](companion-mcp.md) |
| Advanced proxy config (SSL, multi-provider) | [Proxy Setup](guides/proxy-setup.md) |
| Build a custom compression recipe | [Recipe Development](guides/recipes.md) |
| Deep dive on compression algorithms | [Compression](compression.md) |
| Deploy a shared team server | [Team Server](guides/team-server.md) |
| Production deployment deep dive | [DEPLOYMENT.md](DEPLOYMENT.md) |
| Full CLI reference | [CLI Reference](cli-reference.md) |
