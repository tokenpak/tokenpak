# Claude Code Integration Guide

TokenPak sits transparently between Claude Code and the Anthropic API, injecting vault context, tracking spend, and routing to the right provider — with zero changes to how you use Claude Code. This guide covers all six consumption modes. Pick your mode and you'll be running in under five minutes.

**Six modes covered:** [CLI](#cli-one-shot-scripts-batch) · [TUI](#tui-interactive-dev) · [tmux / multi-instance](#tmux-multi-instance-fleet) · [SDK / Agent SDK](#sdk-claude-agent-sdk) · [IDE](#ide-vscode-jetbrains-cursor-windsurf) · [Cron / CI / agent worker](#cron-ci-agent-worker)

---

## Quick Start

```bash
# 1. Install TokenPak
pip install tokenpak

# 2. Start the proxy (runs on port 8766 by default)
tokenpak serve --port 8766

# 3. Tell Claude Code to route through it
export ANTHROPIC_BASE_URL=http://localhost:8766

# 4. Use Claude Code normally — tokenpak handles the rest
claude "summarize this file" < README.md
```

TokenPak auto-detects your consumption mode and applies the right profile (`claude-code-cli`, `claude-code-tui`, etc.) based on your session headers. No manual profile selection required.

---

## CLI — One-Shot, Scripts, Batch

**Profile: `claude-code-cli`**

### Who it's for

Developers running `claude` from a terminal prompt, shell scripts, or batch processing pipelines where each invocation is independent.

### How to install

```bash
pip install tokenpak
tokenpak serve --port 8766 &

# Add to ~/.bashrc or ~/.zshrc for persistence
echo 'export ANTHROPIC_BASE_URL=http://localhost:8766' >> ~/.bashrc
source ~/.bashrc
```

Verify the proxy is active:

```bash
tokenpak status
# TokenPak proxy: running on :8766
# Active profile: claude-code-cli (auto-detected)
```

### What you get

- **Vault injection** — relevant notes, project docs, and code snippets from your vault are automatically prepended to each request's system prompt (top 5 results, ≤4 000 tokens)
- **Token caching** — repeated substrings (file headers, boilerplate) are deduplicated across calls
- **Cost tracking** — every CLI invocation is logged with model, tokens, and USD cost
- **Request telemetry** — session-level aggregates visible at `http://localhost:8766/dashboard`

### What to expect

```
$ claude "what does this repo do?" < README.md

[tokenpak] profile=claude-code-cli vault_blocks=3 tokens_in=1842 tokens_out=247
 cache_hit=true saved=$0.021
```

The inline savings line appears in stderr and is captured in the proxy log. It does not interfere with stdout, so script pipelines work unmodified.

### Troubleshooting

| Problem | Fix |
|---------|-----|
| `ANTHROPIC_BASE_URL` not picked up by new shells | Add export to `~/.bashrc` (not just `.bash_profile`) and `source` it |
| `Connection refused :8766` | Run `tokenpak serve --port 8766` or install as a service: `tokenpak install --service` |
| Vault blocks not appearing | Check `TOKENPAK_VAULT_INJECT` is not set to `0`; run `tokenpak status --vault` |
| Wrong profile detected | Override: `TOKENPAK_PROFILE=claude-code-cli claude "..."` |
| Slow first request | First vault query builds the index; subsequent calls are fast |

---

## TUI — Interactive Dev

**Profile: `claude-code-tui`**

### Who it's for

Developers running `claude` in interactive (REPL/chat) mode for active coding sessions — the default mode when you run `claude` with no `--print` flag.

### How to install

```bash
pip install tokenpak
tokenpak serve --port 8766 &
export ANTHROPIC_BASE_URL=http://localhost:8766

# Start an interactive session
claude
```

TokenPak detects interactive mode via the `X-Claude-Code-Interactive: 1` header that Claude Code sets automatically.

### What you get

- All CLI features (vault injection, caching, cost tracking)
- **Savings tape** — a live per-turn cost bar rendered in the TUI sidebar showing tokens and USD per exchange
- **Cache hit highlighting** — turns with cache hits show a ✓ indicator in the TUI
- **Session-level cost summary** — displayed when the session ends

### What to expect

```
╭─ Claude ──────────────────────────────────────────╮
│ > explain the auth flow │
│ │
│ [tokenpak] ✓ cache hit · 2,140 tokens · $0.008 │
╰────────────────────────────────────────────────────╯
```

!!! note "Savings tape is a preview feature"
 The inline per-turn display is under active development. Current builds show the savings summary at session end only.

### Troubleshooting

| Problem | Fix |
|---------|-----|
| TUI renders garbled output | Ensure your terminal supports ANSI escape codes; try `TERM=xterm-256color` |
| Session cost not displayed at exit | Check that `TOKENPAK_TRACE=true` (default on `claude-code-tui` profile) |
| Interactive session not auto-detected | Confirm `claude` version ≥ 2.0; earlier versions don't send `X-Claude-Code-Interactive` |
| Vault injection cluttering system prompt | Reduce top-k: `TOKENPAK_INJECT_TOP_K=2` |

---

## tmux / Multi-Instance Fleet

**Profile: `claude-code-tmux`**

### Who it's for

Power users running two or more simultaneous Claude Code sessions in tmux panes — e.g., one session per open project, or parallel research + implementation sessions.

### How to install

```bash
pip install tokenpak
tokenpak serve --port 8766 &
export ANTHROPIC_BASE_URL=http://localhost:8766

# Launch your tmux sessions normally — tokenpak detects multi-session automatically
tmux new-session -s project-a
# (in another pane)
tmux new-session -s project-b
```

TokenPak detects tmux mode by observing multiple concurrent `X-Claude-Code-Session-Id` values within a short time window. No manual configuration needed.

### What you get

- All TUI features
- **Per-session cost isolation** — each tmux session gets its own cost bucket in the dashboard
- **Cross-session cache sharing** — sessions share the token cache, so duplicate context isn't re-sent across panes
- **Compact compression tuned for multi-instance** — threshold lowered to avoid OOM under heavy parallel load

### What to expect

Dashboard at `http://localhost:8766/dashboard` shows per-session spend:

```
Sessions active: 3
 project-a 2,840 tokens $0.014
 project-b 1,180 tokens $0.006
 research 4,200 tokens $0.021
────────────────────────────
 Total 8,220 tokens $0.041 (saved $0.019)
```

### Troubleshooting

| Problem | Fix |
|---------|-----|
| tmux profile not auto-detected | Needs ≥2 active sessions; single-session falls back to `claude-code-tui` |
| Sessions not appearing as separate in dashboard | Each `tmux new-session` must be a fresh shell; re-used shell inherits the same session-id |
| High memory use under many parallel sessions | Set `TOKENPAK_COMPACT_THRESHOLD_TOKENS=2500` to compress earlier |
| Cache shared unintentionally | Expected behavior; set `TOKENPAK_CACHE_SCOPE=session` to isolate |

---

## SDK / claude-agent-sdk

**Profile: `claude-code-sdk`**

### Who it's for

Developers building applications with the [Claude Agent SDK](https://github.com/anthropics/claude-agent-sdk) (`claude_agent_sdk`) or the raw `anthropic` Python/TypeScript SDK. Vault injection is disabled for SDK mode (SDK callers manage their own context); all other features remain active.

### How to install

**Python:**

```python
import anthropic

client = anthropic.Anthropic(
 base_url="http://localhost:8766", # route through tokenpak
)
```

**TypeScript / Node:**

```typescript
import Anthropic from "@anthropic-ai/sdk";

const client = new Anthropic({
 baseURL: "http://localhost:8766",
});
```

**Claude Agent SDK:**

```python
from claude_agent_sdk import ClaudeAgent

agent = ClaudeAgent(
 base_url="http://localhost:8766",
)
```

Start the proxy first:

```bash
tokenpak serve --port 8766 &
```

### What you get

- **Cost tracking per SDK call** — model, tokens, USD logged to the dashboard
- **Provider failover** — if Anthropic's API is unavailable, routes to a fallback provider transparently
- **Rate limit smoothing** — request queue absorbs burst traffic to avoid 429s
- **Telemetry** — per-call latency, token counts, and cache hit rates visible at `/dashboard`

!!! note "Vault injection is off for SDK mode"
 SDK callers own their context; tokenpak does not inject vault content into SDK requests. This is intentional — your application controls the system prompt.

### What to expect

```python
response = client.messages.create(
 model="claude-opus-4-5",
 max_tokens=1024,
 messages=[{"role": "user", "content": "Hello"}],
)
# Proxy log: profile=claude-code-sdk tokens_in=12 tokens_out=34 latency=310ms
```

### Troubleshooting

| Problem | Fix |
|---------|-----|
| `AuthenticationError` when using `base_url` | Pass your real API key to the client; tokenpak forwards it upstream |
| SDK detected as wrong profile | Check `User-Agent`; SDK profile activates on `anthropic-sdk-*` and `claude-agent-sdk-*` UAs |
| Streaming responses interrupted | Ensure `stream=True` and that no network proxy between app and tokenpak buffers the stream |
| Fallback provider not routing | Provider failover is not yet shipped; set `ANTHROPIC_API_KEY` for now |

---

## IDE — VSCode, JetBrains, Cursor, Windsurf

**Profile: `claude-code-ide`**

### Who it's for

Developers using Claude Code inside an IDE extension — VSCode with the Claude Code extension, JetBrains IDEs, Cursor, or Windsurf.

### How to install

Set the proxy URL in your shell environment **before** launching the IDE:

```bash
export ANTHROPIC_BASE_URL=http://localhost:8766
tokenpak serve --port 8766 &

# Then launch your IDE from the same shell:
code . # VSCode
cursor . # Cursor
windsurf . # Windsurf
# JetBrains: set ANTHROPIC_BASE_URL in IDE → Settings → Tools → Terminal environment
```

**VSCode settings alternative** — add to `.vscode/settings.json`:

```json
{
 "terminal.integrated.env.linux": {
 "ANTHROPIC_BASE_URL": "http://localhost:8766"
 },
 "terminal.integrated.env.osx": {
 "ANTHROPIC_BASE_URL": "http://localhost:8766"
 }
}
```

TokenPak detects IDE mode via the `User-Agent` header set by IDE extensions (e.g., `claude-code/2.x (vscode)`, `cursor/x.y`).

### What you get

- **Vault injection** — relevant docs injected into every IDE agent turn
- **Per-file cost tracking** — view spend by file or project in the dashboard
- **Token caching** — file content already sent in prior turns is cached; repeat file references are free
- **Profile-aware compression** — IDE profile uses `hybrid` mode to compress large files without touching code structure

### What to expect

```
# In the IDE terminal after a Claude Code request:
[tokenpak] profile=claude-code-ide vault_blocks=2 tokens_in=3210 tokens_out=512
 cache_hit=partial saved=$0.038
```

### Troubleshooting

| Problem | Fix |
|---------|-----|
| IDE doesn't pick up `ANTHROPIC_BASE_URL` | Launch the IDE from a terminal with the env var set, not from a dock/launcher icon |
| JetBrains not routing through proxy | Set the env var in IDE → Settings → Tools → Terminal → Environment variables |
| Cursor / Windsurf always calls Anthropic directly | Some versions embed the API key separately; check extension settings for a "custom endpoint" field |
| IDE profile shows as `claude-code-cli` | Update your IDE extension; older versions don't include the IDE identifier in `User-Agent` |
| Large file uploads are slow | Enable compression: `TOKENPAK_MODE=hybrid` (already default on `claude-code-ide`) |

---

## Cron / CI / Agent Worker

**Profile: `claude-code-cron`**

### Who it's for

Scheduled jobs, CI pipelines, and agent worker scripts that invoke `claude --print` non-interactively — such as nightly summaries, automated PR reviews, or multi-agent orchestration workers.

### How to install

```bash
pip install tokenpak

# Start proxy as a persistent service
tokenpak install --service
# or manually: tokenpak serve --port 8766 &

export ANTHROPIC_BASE_URL=http://localhost:8766
```

For CI (GitHub Actions, etc.):

```yaml
# .github/workflows/claude-review.yml
env:
 ANTHROPIC_BASE_URL: http://localhost:8766
 ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}

steps:
 - name: Start TokenPak proxy
 run: |
 pip install tokenpak
 tokenpak serve --port 8766 &
 sleep 1
 - name: Run Claude review
 run: claude --print "review this PR diff" < diff.txt
```

TokenPak detects cron/worker mode via the `X-Claude-Code-NonInteractive: 1` header. The `agent-claude-worker.sh` script sets this header automatically via `ANTHROPIC_CUSTOM_HEADERS`.

### What you get

- **Budget enforcement** — set `TOKENPAK_BUDGET_DAILY_LIMIT_USD=5` to hard-stop runaway jobs
- **Cost-per-run logging** — each cron invocation logged with timestamp, tokens, and USD
- **Vault injection** — cron jobs can draw on vault knowledge (useful for scheduled summaries)
- **Non-interactive compression** — aggressive token compression by default; cron payloads rarely need context preservation

### What to expect

```bash
$ ANTHROPIC_BASE_URL=http://localhost:8766 claude --print "daily standup summary"
[tokenpak] profile=claude-code-cron tokens_in=892 tokens_out=134 saved=$0.011
 budget_remaining=$4.83 / $5.00 today
```

### Troubleshooting

| Problem | Fix |
|---------|-----|
| Profile shows as `claude-code-cli` in cron | Ensure `ANTHROPIC_CUSTOM_HEADERS='{"X-Claude-Code-NonInteractive": "1"}'` is set in the cron environment |
| Budget limit hit mid-run | Increase `TOKENPAK_BUDGET_DAILY_LIMIT_USD` or check for runaway loops |
| CI proxy not reachable | Add `sleep 1` after starting proxy; CI runners start services asynchronously |
| Vault injection in CI injects wrong context | Set `TOKENPAK_VAULT_INJECT=0` to disable vault in CI; re-enable for specific jobs |
| `tokenpak install --service` not available | The installer is not yet released; use `tokenpak serve &` with a process manager instead |

---

## Cross-Mode Features

These features apply across all six modes.

### Vault Injection

**Status: shipped**

TokenPak automatically enriches requests with relevant content from your vault (markdown notes, project docs, code snippets). Defaults: top 5 results, max 4 000 tokens, min relevance score 2.0.

```bash
# Disable for a single call
TOKENPAK_VAULT_INJECT=0 claude "..."

# Tune depth
TOKENPAK_INJECT_TOP_K=3 TOKENPAK_INJECT_BUDGET=2000 claude "..."
```

Vault injection is disabled for the `claude-code-sdk` profile.

### Compliance Routing (Bedrock)

**Status: shipped**

For teams in regulated industries (HIPAA, FedRAMP, SOC 2), tokenpak can route all Claude Code traffic through AWS Bedrock instead of `api.anthropic.com`. Claude Code never knows — the wire format is translated transparently in both directions.

**Global opt-in (all requests):**

```bash
export TOKENPAK_COMPLIANCE_PROVIDER=bedrock
export AWS_ACCESS_KEY_ID=<your-key>
export AWS_SECRET_ACCESS_KEY=<your-secret>
export AWS_DEFAULT_REGION=us-east-1 # default; change to your Bedrock region

# Now all claude invocations route to Bedrock automatically
claude "explain this function" < src/main.py
```

**Per-request opt-in (some sessions via Bedrock, others direct):**

```bash
# Set header for a single call — overrides TOKENPAK_COMPLIANCE_PROVIDER
ANTHROPIC_CUSTOM_HEADERS='{"X-TokenPak-Compliance": "bedrock"}' \
 claude "sensitive query here"
```

**What happens under the hood:**

1. tokenpak reads `TOKENPAK_COMPLIANCE_PROVIDER=bedrock` (or `X-TokenPak-Compliance: bedrock` header).
2. Request body is translated: `model` field removed (moved to URL), `anthropic_version: bedrock-2023-05-31` added.
3. Model ID is translated: `claude-3-5-sonnet-20241022` → `anthropic.claude-3-5-sonnet-20241022-v2:0`.
4. Request is signed with AWS SigV4 using your env credentials — no boto3 required.
5. Bedrock responds; tokenpak restores the original model ID and forwards as a standard Anthropic response.
6. Streaming: Bedrock's camelCase event names (`messageStart`, `contentBlockDelta`) are translated to Anthropic snake_case (`message_start`, `content_block_delta`) mid-stream.

**Supported models:**

| Claude Code model alias | Bedrock model ID |
|-------------------------|-----------------|
| `claude-3-5-sonnet-20241022` | `anthropic.claude-3-5-sonnet-20241022-v2:0` |
| `claude-3-5-haiku-20241022` | `anthropic.claude-3-5-haiku-20241022-v1:0` |
| `claude-3-opus-20240229` | `anthropic.claude-3-opus-20240229-v1:0` |
| `claude-sonnet-4-5` | `anthropic.claude-sonnet-4-5-20251101-v1:0` |
| `claude-sonnet-4-6` | `anthropic.claude-sonnet-4-6-20260101-v1:0` |

Unknown model IDs pass through unchanged (forward-compatible).

**Troubleshooting:**

| Problem | Fix |
|---------|-----|
| `403 Forbidden` from Bedrock | AWS credentials missing or invalid; check `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` |
| `ValidationException: The model is not supported` | Model not enabled in your Bedrock account; enable it in the AWS console under Bedrock → Model access |
| Wrong region | Set `AWS_DEFAULT_REGION` to the region where your Bedrock quota is provisioned |
| Streaming stops mid-response | Your Bedrock endpoint may emit binary EventStream; tokenpak handles this automatically but verify `Content-Type: application/vnd.amazon.eventstream` is not being stripped by a network proxy |

---

## FAQ

**Q: Does TokenPak change what Claude Code does or sees?**
No. TokenPak is a transparent proxy. All requests and responses are forwarded unmodified (in `transparent` mode) or with opt-in compression. Claude Code behaves identically through a local hop.

**Q: Do I need an API key?**
Yes. TokenPak forwards your `ANTHROPIC_API_KEY` to Anthropic's API unchanged. It does not provide its own API credentials.

**Q: Does this work with Claude Code's subscription (OAuth) auth?**
Yes. Claude Code's OAuth flow uses a bearer token in the `Authorization` header, which tokenpak forwards transparently. You don't need to set `ANTHROPIC_API_KEY` in this mode.

**Q: How do I know which profile is active?**
Run `tokenpak status` or check the proxy log. The active profile is logged on every request: `profile=claude-code-tui`.

**Q: Can I force a specific profile?**
Yes: `TOKENPAK_PROFILE=claude-code-tui claude "..."`. The env var override takes precedence over all auto-detection.

**Q: Will TokenPak work in a Docker container?**
Yes. Run `tokenpak serve` as a sidecar and set `ANTHROPIC_BASE_URL=http://tokenpak:8766` in your app container. See [Docker deployment](DOCKER.md) for compose examples.

**Q: Does vault injection affect my privacy?**
Vault content is injected client-side (in the proxy, before the request leaves your machine) and sent to Anthropic as part of the normal request payload. No vault content is stored by Anthropic beyond the normal request logging policies.

**Q: How do I update TokenPak?**
`pip install --upgrade tokenpak`. Profile definitions are bundled with the package; restart the proxy after upgrading.

**Q: What happens if the proxy crashes mid-session?**
Claude Code falls back to calling Anthropic directly if `ANTHROPIC_BASE_URL` is unreachable (it retries once, then drops the proxy). No requests are lost. Restart the proxy to resume tracking.

**Q: Is there a team/shared proxy option?**
Single-host proxy only for now. Multi-user deployments (shared proxy on a LAN or behind a reverse proxy) work but are not officially supported. See [production SLA notes](production-sla.md).

---

*See also: [TokenPak vs. the Alternatives](comparison.md#for-claude-code-users-specifically) — how TokenPak's Claude Code features compare to Helicone, LiteLLM, Portkey, Langfuse, LangSmith, and OpenRouter.*
