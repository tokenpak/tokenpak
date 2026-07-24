# TokenPak Quick Start Guide

The proxy reference path targets a first receipt in three commands and five
minutes. Pick your path:

| Path | Best for |
|------|----------|
| [**Proxy Path**](#proxy-path-first-measured-receipt) | First measured receipt from a real provider request |
| [**SDK Path**](#sdk-path-protocol-first) | New projects or when you want protocol-level control |

---

## Proxy Path: First Measured Receipt

**You already write prompts. TokenPak compresses them before they hit the API.**

The supported reference path is three commands. Before starting, sign in to a
supported client. The reference path uses Codex OAuth and its selected/default
model; API keys and explicit model overrides are optional. Run it from a real
project. Provider use may count against a subscription or incur charges.

### Command 1: Install

```bash
python -m pip install tokenpak
```

### Command 2: Start the receipt-enabled proxy

In terminal 1:

```bash
tokenpak serve --profile aggressive --stats-footer
```

Leave it running. The flags apply only to this proxy process; the receipt is
printed in this terminal and is not injected into the provider response.

### Command 3: Launch the authenticated client

In terminal 2:

```bash
tokenpak codex
```

Make a substantive project request, then continue the same topic. A new
conversation can begin with an ineligible request because it has no historical
context. Terminal 1 prints the measured before/after receipt for the first
eligible request. The dollar value is an estimate based on TokenPak's
model-pricing table. See
[First Measured Savings Receipt](./first-receipt.md) for the expected output,
five-minute reference target, and truthful exclusions.

Short or protected inputs may legitimately save zero. TokenPak preserves
system/developer policy and the newest two messages. Byte-preserved routes are
also ineligible for TokenPak compression savings. `tokenpak demo` remains
useful as an offline fixture, but it does not satisfy a real first-request
receipt.

### Connect your normal client afterward

```bash
tokenpak integrate # list clients and detection status
```

Every `tokenpak integrate <client> --apply` operation previews or applies the
client-specific route, backs up supported config files, and prints rollback
information. This later integration step is not part of the three-command
reference receipt path.

### Editions, security, and compliance

The OSS package is the Apache-2.0 local proxy and CLI. Run
`tokenpak upgrade --print-url` to print the current Pro page, and see
[multipak.md](./multipak.md) for the shipped OSS/Pro boundary.

For trust review, start with
[security architecture](./guides/enterprise/security-architecture.md) and
[compliance mapping](./guides/enterprise/compliance-mapping.md). Those docs map
deployment controls and report surfaces; they do not change the beta support
model or imply hosted processing by the OSS package.

---

## SDK Path: Protocol-First

**Use the TokenPak format with any LLM client — no proxy needed.**

### Install

```bash
pip install tokenpak
```

### Compress and send

```python
from tokenpak import TokenPak, Block

pack = TokenPak(budget=4000)
pack.add_instructions("You are a helpful assistant.")
pack.add_knowledge("docs", "Your long documentation here...")
pack.add_conversation([{"role": "user", "content": "Summarize the docs"}])

# Works with any OpenAI-compatible client
from openai import OpenAI
client = OpenAI()
response = client.chat.completions.create(
 model="gpt-4",
 messages=pack.to_messages()
)

# See how much was saved
print(pack.compile().report)
# → Input: 8,420 tokens → Output: 3,200 tokens | Savings: 62%
```

---

## Common Use Cases

### "I use Claude Code"

Point Claude Code at the proxy:

```bash
# Start the proxy
tokenpak start

# Set the base URL in your Claude Code config or environment:
export ANTHROPIC_BASE_URL=http://localhost:8766
```

Claude Code's supported route is byte-preserved. It can use TokenPak routing and
telemetry, but it is intentionally not the reference path for proving positive
TokenPak compression savings. Use the direct eligible request above for that
receipt.

### "I use the OpenAI SDK"

```python
from openai import OpenAI

# Change base_url — everything else stays the same
client = OpenAI(
 api_key="your-openai-key",
 base_url="http://localhost:8766"
)

response = client.chat.completions.create(
 model="gpt-4",
 messages=[{"role": "user", "content": "Your prompt here"}]
)
```

### "I use LangChain"

```python
from langchain_openai import ChatOpenAI

# Point LangChain at the proxy
llm = ChatOpenAI(
 model="gpt-4",
 openai_api_base="http://localhost:8766",
 openai_api_key="your-key"
)

response = llm.invoke("Your prompt here")
```

### "I use LiteLLM / other frameworks"

Most frameworks support a `base_url` or `api_base` parameter. Set it to `http://localhost:8766`.

---

## Troubleshooting

### "It's not connecting"

```bash
tokenpak status # is the proxy running?
tokenpak start # start it if not
```

Check that your client is pointing at `http://localhost:8766` (not `https://`).

### "My API key isn't being forwarded"

TokenPak is a passthrough proxy — it never stores or modifies your credentials. Make sure:
- An OAuth client is already signed in; or
- If your chosen SDK/provider uses API keys, its optional key is available to that client.

### "I'm not seeing any savings"

```bash
tokenpak cost --week # check a longer time window
tokenpak demo # inspect the offline fixture only
```

Short prompts compress less, and byte-preserved routes may correctly report zero
TokenPak compression savings. For a real per-request proof, use the
[three-command first-receipt path](./first-receipt.md); the demo is not a
substitute for that receipt.

### "The proxy started but requests aren't going through"

Verify your client is using the right port:
```bash
curl http://localhost:8766/health
# → {"status": "ok", ...}
```

If the health check fails, restart with `tokenpak restart`.

---

## What Do I Do Next?

- **Check your savings:** `tokenpak cost --week`
- **Audit a prompt file:** `tokenpak optimize --file my-prompt.md` (reports whitespace bloat, repeated phrases, verbose phrasings with concrete replacements)
- **Compress a sample:** `echo "long text here" | tokenpak compress` (works offline, no proxy required)
- **License info:** `tokenpak license` (OSS Free by default) / `tokenpak plan` (list tiers)
- **Tune compression:** See [compression.md](./compression.md) for aggressiveness settings
- **Index your vault:** `tokenpak index ~/your-docs` for semantic search at zero token cost
- **Full CLI reference:** [cli-reference.md](./cli-reference.md) — all commands explained
- **API reference:** [api-reference.md](./api-reference.md) — SDK classes and methods
- **REST API (companion/external dashboards):** [api-tpk-v1.md](./api-tpk-v1.md)

---

## Companion MCP tools (in Claude Code, Cursor, etc.)

When you launch Claude Code with `tokenpak claude`, the agent gets 9 MCP tools it can call mid-conversation:

| tool | what it does |
|------|--------------|
| `estimate_tokens` | Token count for text or a file |
| `check_budget` | Remaining daily + session cost |
| `vault_search` | BM25 search over your indexed vault |
| `vault_retrieve` | Full content of a specific vault block |
| `prune_context` | Head/tail truncate verbose text to a token budget |
| `load_capsule` | Load a saved session memory capsule |
| `journal_read` / `journal_write` | Per-session notes |
| `session_info` | Current process + proxy state |

All tools call the proxy's REST API (`/tpk/v1/*`) so your data lives in exactly one place. See [api-tpk-v1.md](./api-tpk-v1.md) for the underlying endpoints.

---

> **Tip:** Run `tokenpak demo` at any time to see live compression on a sample prompt — no proxy needed.
