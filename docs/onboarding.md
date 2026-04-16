# Onboarding Guide: Day 1 → Day 30

Welcome to TokenPak. This guide takes you from a fresh install to running TokenPak in production, one milestone at a time. Each section ends with a checklist — complete it before moving on.

> **Using Claude Code?** See the [Claude Code Integration Guide](claude-code-integration.md) for mode-specific setup before starting Day 1.

---

## Day 1: Your First Compressed Request

Your goal today: install TokenPak, start the proxy, send one request, and confirm compression is working.

### Install

```bash
pip install "tokenpak[serve]"
```

The `[serve]` extra installs FastAPI, required for the proxy server. If you only want the compression SDK (no proxy), use plain `pip install tokenpak`.

Confirm it installed:

```bash
tokenpak --version
```

### Configure Your LLM Client

Run the setup wizard once. It detects your installed LLM client and writes the proxy URL into the right config file — no manual editing.

```bash
tokenpak setup
```

What it does per client:

- **Claude Code** — writes `ANTHROPIC_BASE_URL=http://localhost:8766` into `~/.claude/settings.json`
- **OpenAI SDK** — prints the one-line export command to add to your shell config
- **Google AI SDK** — prints the one-line export command

The wizard never reads or writes API keys — only proxy URLs.

> **Non-interactive / CI:** Run `tokenpak setup` and answer the prompts, or configure the proxy URL manually by editing your LLM client config directly.

### Start the Proxy

```bash
tokenpak serve --port 8766
```

You'll see startup output confirming the port and worker count. Leave this terminal open, or run it in the background:

```bash
tokenpak serve --port 8766 &
```

### Send a Request

Use your LLM client as normal — make any request. TokenPak intercepts it transparently and compresses the context before forwarding it to your provider.

### Verify Compression Is Active

```bash
tokenpak status
```

Expected output:

```
✓ Proxy: running on :8766
✓ Compression: enabled (balanced mode)
✓ Cost tracking: active
✓ Session: 1 requests
```

Then check your first savings:

```bash
tokenpak cost
# Cost today: $0.002 | Tokens saved: 1,847 (38%)
```

If you see token savings above zero, compression is working.

### Day 1 Checklist

- [ ] `tokenpak --version` prints a version string
- [ ] `tokenpak setup` ran without errors
- [ ] `tokenpak serve` started on port 8766
- [ ] `tokenpak status` shows proxy running and compression enabled
- [ ] `tokenpak cost` shows at least one request processed

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

A healthy setup saves 20–50% tokens on typical mixed workloads. If you're seeing less than 15%, check these:

- Is compression set to `balanced` or `aggressive` mode? (Check `~/.tokenpak/config.json`, key `compression.level`)
- Are your requests using long system prompts or repetitive context? Those compress best.
- Run `tokenpak demo --verbose` to see the pipeline applied to a sample payload.

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
- [ ] You understand where to find the `compression.level` setting

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
| Advanced proxy config (SSL, multi-provider) | [Proxy Setup](guides/proxy-setup.md) |
| Build a custom compression recipe | [Recipe Development](guides/recipes.md) |
| Deep dive on compression algorithms | [Compression](compression.md) |
| Deploy a shared team server | [Team Server](guides/team-server.md) |
| Production deployment deep dive | [DEPLOYMENT.md](DEPLOYMENT.md) |
| Full CLI reference | [CLI Reference](cli-reference.md) |
