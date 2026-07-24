# Getting Started (Day 1 Quick Start)

Get TokenPak running in under 5 minutes.

> **Want the full journey?** This page covers Day 1 only. For Day 3 through Day 30 — savings reports, recipe customization, budgets, and production deployment — see the [full Onboarding Guide](onboarding.md).

---

## Requirements

- Python 3.10+
- An existing authenticated LLM client (Codex, Claude Code, an SDK, etc.)

Provider API keys and explicit model overrides are optional client-specific
choices, not TokenPak installation requirements. The first-receipt reference
path reuses an existing Codex OAuth login and its normal model selection.

---

## Install

=== "pip"
 ```bash
 pip install tokenpak
 ```

=== "From source"
 ```bash
 git clone https://github.com/tokenpak/tokenpak
 cd tokenpak
 pip install -e .
 ```

=== "With optional extras"
 ```bash
 # Semantic/vector search
 pip install tokenpak[retrieval]

 # LLMLingua compression engine
 pip install tokenpak[compression]

 # Exact OpenAI-compatible token counts
 pip install tokenpak tiktoken
 ```

---

## Prove One Measured Request

For the canonical first-use path, follow
[First Measured Savings Receipt](first-receipt.md). It installs TokenPak, starts
one receipt-enabled foreground proxy, and sends one eligible request using your
current project's real `README.md`. Complete that path before starting another
proxy or applying a client integration.

`tokenpak demo` remains an optional offline fixture; it is not a receipt from
your own provider request.

---

## Configure a Saved Local Proxy (optional wizard)

Run the setup wizard once after installing:

```bash
tokenpak setup
```

The wizard detects available clients and any optional provider credentials,
asks for a port and compression profile, writes `~/.tokenpak/config.yaml`, and
starts a background proxy. It does not require or persist a provider key and
does not silently rewrite client configuration.

!!! tip "Non-interactive / CI"
 Configure the proxy with environment variables and use the manual client
 connection instructions below; the interactive setup wizard exits without
 reconfiguration when no terminal is attached.

To connect supported clients with preview, backup, verification, and revert
guidance, use `tokenpak integrate` after the proxy is running.

---

## Start the Proxy Without the Wizard

```bash
tokenpak serve --port 8766
```

The proxy starts on `http://localhost:8766` and is ready to accept requests
immediately. Skip this command if `tokenpak setup` already started the proxy.

!!! tip "Run in background"
 ```bash
 tokenpak serve --port 8766 &
 # Stop with:
 tokenpak stop
 ```

---

## Connect Your LLM Client

Review detected integrations and client-specific instructions first:

```bash
tokenpak integrate
```

You can also configure a client manually:

=== "Claude Code"
 Configure in `~/.claude/settings.json`:
 ```json
 {
 "env": {
 "ANTHROPIC_BASE_URL": "http://localhost:8766"
 }
 }
 ```
 Or set in your shell config:
 ```bash
 export ANTHROPIC_BASE_URL=http://localhost:8766
 ```

=== "OpenAI Python"
 ```python
 from openai import OpenAI

 client = OpenAI(
 base_url="http://localhost:8766/v1",
 api_key="your-key-here"
 )
 ```

=== "OpenAI CLI"
 ```bash
 export OPENAI_BASE_URL=http://localhost:8766/v1
 ```

=== "Codex OAuth"
 ```bash
 tokenpak codex
 ```
 When the local proxy is healthy, the TokenPak launcher supplies the base-URL
 override for this invocation. Codex retains ownership of its OAuth login and
 selected/default model.

=== "Any HTTP client"
 Replace your provider base URL with `http://localhost:8766`.
 TokenPak auto-detects the provider from the `Authorization` header and routes accordingly.

Client-supplied credentials pass through unchanged. TokenPak never stores them.

---

## Verify Ongoing Traffic

```bash
tokenpak status
```

Expected output:
```
✓ Proxy: running on :8766
✓ Compression: enabled (balanced mode)
✓ Cost tracking: active
✓ Session: 0 requests
```

Make a normal, eligible request through your client, then:

```bash
tokenpak cost
# Requests today: 1 | Cost today: <your measured total>
# Run tokenpak savings after real traffic for receipt-backed savings.
```

---

## Index Your Vault (Optional, Zero Tokens)

If you work with a large codebase or notes vault, index it for instant semantic search:

```bash
tokenpak index ~/notes
tokenpak vault repair # Check index health and rebuild stale entries
```

This uses a local SQLite registry — no LLM calls, no cost.

---

## Auto-Calibration (Recommended)

Let TokenPak calibrate optimal parallelism for your hardware:

```bash
tokenpak calibrate ~/notes --max-workers 8 --rounds 2
```

This runs once and saves a profile to `~/.tokenpak/calibration.json`. Future indexing runs use it automatically.

---

## Set a Budget (Optional)

Protect yourself from runaway costs:

```bash
tokenpak budget set --monthly 50 # $50/month limit
tokenpak budget alert --at 80% # warn at 80%
```

---

## Next Steps

- [Onboarding Guide](onboarding.md) — Day 3 through Day 30: savings reports, custom recipes, budgets, production deployment
- [Companion & MCP Setup](companion-mcp.md) — run Claude Code / Codex with the companion (budget, journal, vault tools) and the first-run MCP cold-start fix
- [Proxy Setup](guides/proxy-setup.md) — advanced proxy configuration, SSL, multi-provider
- [CLI Reference](cli-reference.md) — full command reference
- [Recipe Development](guides/recipes.md) — custom compression recipes
