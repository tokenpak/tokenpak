# TokenPak Quick Start Guide

The proxy reference path targets a first receipt in three commands and five
minutes. Pick your path:

| Path | Best for |
|------|----------|
| [**Proxy Path**](#proxy-path-first-measured-receipt) | First measured receipt from a real provider request |
| [**SDK Path**](#sdk-path-protocol-first) | New projects or when you want protocol-level control |

---

## Proxy Path: First Measured Receipt

**You already write prompts. TokenPak routes them and records measured receipts.**

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

Make a substantive project request. Terminal 1 prints the measured receipt
`⚡ TokenPak: 0 tokens saved`. This is expected for supported provider traffic:
in the unmodified reference setup, the built-in Pak builder preserves every
system, user, and assistant conversation turn, including long history. See
[First Measured Request Receipt](./first-receipt.md) for the five-minute
reference target and the separate explicit compression surfaces.

This zero-savings receipt proves the request traversed the proxy and was
recorded without a false reduction claim. `tokenpak compress` and companion
`prune_context` provide supported positive compression measurements. `tokenpak
demo` remains an offline fixture rather than a provider-request receipt.

### Connect your normal client afterward

```bash
tokenpak integrate # list clients and detection status
```

Every `tokenpak integrate <client> --apply` operation previews or applies the
client-specific route, backs up supported config files, and prints rollback
information. This later integration step is not part of the three-command
reference receipt path.

### Editions, security, and compliance

TokenPak ships in two editions: TokenPak, the Apache-2.0 local proxy and
CLI, and TokenPak Pro. Public Pro enrollment is not open, so there is no
signup page to visit; see [multipak.md](./multipak.md) for the shipped
boundary between the two.

For trust review, start with
[security architecture](./guides/enterprise/security-architecture.md) and
[compliance mapping](./guides/enterprise/compliance-mapping.md). Those docs map
deployment controls and report surfaces; they do not change the beta support
model or imply hosted processing by the OSS package.

---

## SDK Path: Protocol-First

**Compile prompts into budgeted, prioritized blocks — no proxy needed, works with any LLM client.**

### Install

```bash
pip install tokenpak
```

### Compile a pack and see the savings

```python
from tokenpak import ContextPack, PackBlock

docs_text = (
    "TokenPak is a deterministic context compiler. It takes structured blocks "
    "of content instructions, knowledge, evidence, conversation history and "
    "compiles them into a budgeted prompt with a full transparency report. "
    "Every decision TokenPak makes (kept, compacted, truncated, removed) is "
    "recorded so you can see exactly what happened to your context and why. "
    "This paragraph is intentionally long and repetitive in a documentation "
    "way so that a small token budget forces real compaction: TokenPak is a "
    "deterministic context compiler that compiles structured content blocks "
    "into a budgeted prompt with a full transparency report of every decision."
)

pack = ContextPack(budget=120)
pack.add(PackBlock(id="system", type="instructions", content="You are a helpful assistant.", priority="critical"))
pack.add(PackBlock(id="docs", type="knowledge", content=docs_text, priority="high", max_tokens=60))
pack.add(PackBlock(id="request", type="conversation", content="Summarize the docs above in one sentence.", priority="medium"))

compiled = pack.compile()
print(compiled.report)
```

```
TokenPak Compile Report
═══════════════════════════════════════════════════════

Summary
───────────────────────────────────────────────────────
Input:   3 blocks, 177 tokens
Output:  3 blocks, 77 tokens
Savings: 56.5% (100 tokens saved)
Budget:  120 tokens (64.2% used)
Time:    0.1ms

Block Decisions
───────────────────────────────────────────────────────

✅ KEPT: [instructions] system
   Priority: critical
   Tokens: 7
   Reason: critical priority

📦 COMPACTED: [knowledge] docs
   Priority: high
   Tokens: 160
   Before: 160 tokens → After: 60 tokens
   Reason: exceeded block budget (max 60)
   Method: extractive_truncation

✅ KEPT: [conversation] request
   Priority: medium
   Tokens: 10
   Reason: medium priority — within budget

Priority Order
───────────────────────────────────────────────────────
 1. system (critical) - 7 tokens
 2. docs (high) - 60 tokens
 3. request (medium) - 10 tokens
```

Each `PackBlock` carries a `priority` (`critical`/`high`/`medium`/`low`) and an
optional per-block `max_tokens` cap. `compile()` keeps, compacts, truncates, or
removes blocks to fit the pack's overall `budget`, and `compiled.report` records
every decision it made and why. Exact token counts vary slightly with your
installed tokenizer (`pip install tokenpak[tokens]` for exact `tiktoken`
counts; without it, TokenPak falls back to a length-based estimate).

### Send the compiled result to any client

`compiled` is stack-neutral — no TokenPak gateway required:

```python
from tokenpak import ContextPack, PackBlock

pack = ContextPack(budget=4000)
pack.add(PackBlock(id="system", type="instructions", content="You are a helpful assistant.", priority="critical"))
pack.add(PackBlock(id="request", type="conversation", content="Summarize the docs above.", priority="medium"))
compiled = pack.compile()

messages = compiled.to_messages()  # OpenAI-style messages list
print(messages)
```

```
[{'role': 'user', 'content': 'You are a helpful assistant.\n\n---\n\nSummarize the docs above.'}]
```

`compiled.to_anthropic()` returns a `(system, messages)` tuple for the
Anthropic SDK, and `compiled.to_prompt()` returns the same content as a single
plain-text string. Pass whichever shape your client expects — for example
`OpenAI().chat.completions.create(model="gpt-4o", messages=messages)` — with
no TokenPak-specific handling required downstream.

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
telemetry and can likewise report a truthful zero-savings receipt. The
three-command reference path above exercises Codex.

### "I use the OpenAI SDK"

```python
from openai import OpenAI

# Change base_url — everything else stays the same.
# The /v1 suffix is required: the SDK appends /chat/completions to it.
client = OpenAI(
 api_key="your-openai-key",
 base_url="http://localhost:8766/v1"
)

response = client.chat.completions.create(
 model="gpt-4",
 messages=[{"role": "user", "content": "Your prompt here"}]
)
```

### "I use LangChain"

```python
from langchain_openai import ChatOpenAI

# Point LangChain at the proxy (keep the /v1 suffix)
llm = ChatOpenAI(
 model="gpt-4",
 openai_api_base="http://localhost:8766/v1",
 openai_api_key="your-key"
)

response = llm.invoke("Your prompt here")
```

### "I use LiteLLM / other frameworks"

Most frameworks support a `base_url` or `api_base` parameter. For OpenAI-compatible clients (LiteLLM, and most frameworks that append `/chat/completions` themselves), set it to `http://localhost:8766/v1`. For Anthropic-native clients (which append `/v1/messages` themselves), set it to `http://localhost:8766` — no `/v1`.

---

## Troubleshooting

### "It's not connecting"

```bash
tokenpak status # is the proxy running?
tokenpak start # start it if not
```

Check that your client is pointing at `http://localhost:8766` (not `https://`).

If your first request returns `404 Not Found`, the base URL is missing its `/v1` suffix: OpenAI-compatible clients must use `http://localhost:8766/v1` (Anthropic-native clients use `http://localhost:8766` without `/v1`).

### "My API key isn't being forwarded"

TokenPak is a passthrough proxy — it never stores or modifies your credentials. Make sure:
- An OAuth client is already signed in; or
- If your chosen SDK/provider uses API keys, its optional key is available to that client.

### "I'm not seeing any savings"

```bash
tokenpak cost --week # check a longer time window
tokenpak demo # inspect the offline fixture only
```

Role-bearing provider conversations correctly report zero TokenPak compression
savings regardless of length. Use the
[three-command first-receipt path](./first-receipt.md) to verify real request
routing and accounting. Use `tokenpak compress` or companion `prune_context`
for explicit positive compression measurements; the demo remains an offline
fixture.

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
