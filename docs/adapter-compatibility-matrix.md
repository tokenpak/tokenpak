# TokenPak Adapter Compatibility Matrix

**Last updated:** 2026-03-08
**Verified against:** Cali system (Python 3.12.3)

This matrix shows which TokenPak versions work with which provider SDK versions.
Use it to verify your combination before installing.

**Legend:**
- ✅ Supported — tested and verified working
- ⚠️ Beta / Experimental — may work with caveats
- ❌ Not supported — known incompatibility
- 🔲 Not tested — likely works but untested

---

## OpenAI SDK Adapter

TokenPak works with OpenAI by accepting `compiled.to_messages()` output, which is
standard OpenAI-compatible `messages` format. No extra package required.

**Reference:** https://pypi.org/project/openai/

| TokenPak | OpenAI SDK  | Python     | Status | Notes |
|----------|-------------|------------|--------|-------|
| v0.9     | 0.28–1.x    | 3.10–3.12  | ⚠️     | `to_messages()` available but unstable |
| v1.0     | 1.x–1.6x    | 3.10–3.13  | ✅     | Stable; `to_messages()` and `to_anthropic()` tested |
| v1.0     | 2.x (2.26+) | 3.10–3.13  | ✅     | Tested on Cali with `openai==2.26.0` |
| v1.0     | >=2.27      | 3.10–3.13  | 🔲     | Not tested; likely works (OpenAI SDK is stable) |

> The proxy (`tokenpak serve`) intercepts calls to `api.openai.com` — no SDK dependency required for proxy mode.

---

## Anthropic SDK Adapter

TokenPak produces Anthropic-compatible output via `compiled.to_anthropic()`, which returns
a `(system, messages)` tuple. No extra package required.

**Reference:** https://pypi.org/project/anthropic/

| TokenPak | Anthropic SDK | Python     | Status | Notes |
|----------|---------------|------------|--------|-------|
| v0.9     | 0.24–0.27     | 3.10–3.12  | ⚠️     | Claude 2 era; `to_anthropic()` not present |
| v1.0     | 0.28–0.35     | 3.10–3.13  | ✅     | Claude 3 Opus/Sonnet/Haiku; tested |
| v1.0     | >=0.36        | 3.10–3.13  | 🔲     | Not tested; API format is stable, likely works |

> The proxy intercepts calls to `api.anthropic.com` — no SDK dependency required for proxy mode.

---

## LiteLLM Integration

TokenPak ships a first-class `tokenpak.integrations.litellm` module with `TokenPakMiddleware`,
`compile_pack`, and a `ProxyHandler`. LiteLLM is an **optional dependency** — install separately.

**Reference:** https://pypi.org/project/litellm/

| TokenPak | LiteLLM     | Python     | Status | Notes |
|----------|-------------|------------|--------|-------|
| v0.9     | any         | 3.10–3.12  | ❌     | LiteLLM integration added in v1.0 |
| v1.0     | 1.0–1.x     | 3.10–3.13  | ✅     | `TokenPakMiddleware`, `patch_completion`, `ProxyHandler` |
| v1.0     | >=2.0       | 3.10–3.13  | 🔲     | Not tested; middleware uses standard kwargs, likely compatible |

**Usage:**
```python
from tokenpak.integrations.litellm import TokenPakMiddleware
```

---

## LangChain Integration (`langchain-tokenpak`)

Separate installable package with `TokenPakContextManager`, `TokenPakState`,
`TokenPakRetriever`, and `TokenPakMemory`.

**Reference:** https://pypi.org/project/langchain-core/

| tokenpak | langchain-tokenpak | langchain-core | Python     | Status | Notes |
|----------|--------------------|----------------|------------|--------|-------|
| v0.9     | —                  | —              | —          | ❌     | Package not yet released |
| v1.0     | 0.1.0              | >=0.1.0        | 3.10–3.12  | ✅     | 18/18 tests passing; installed: `langchain-core==1.2.17` |
| v1.0     | 0.1.0              | >=1.0          | 3.10–3.13  | ✅     | Tested on Cali with `langchain-core==1.2.17` |

**Install:**
```bash
pip install langchain-tokenpak
pip install langchain  # optional: full LangChain stack
```

---

## LlamaIndex Integration (`llamaindex-tokenpak`)

Separate installable package with `TokenPakNodeParser`, `TokenPakRetriever`,
`TokenPakQueryEngine`, and `TokenPakWorkflow`.

**Reference:** https://pypi.org/project/llama-index-core/

| tokenpak | llamaindex-tokenpak | llama-index-core | Python     | Status | Notes |
|----------|---------------------|------------------|------------|--------|-------|
| v0.9     | —                   | —                | —          | ❌     | Package not yet released |
| v1.0     | 0.1.0               | >=0.10.0         | 3.10–3.12  | ✅     | 67/67 tests passing; tested with `llama-index-core==0.14.15` |
| v1.0     | 0.1.0               | >=0.14           | 3.10–3.13  | ✅     | Cali system verified |

**Install:**
```bash
pip install llamaindex-tokenpak
```

---

## CrewAI Integration (`crewai-tokenpak`)

Separate installable package providing `TokenPakContextAllocator` for multi-agent context budgeting.

**Reference:** https://pypi.org/project/crewai/

| tokenpak | crewai-tokenpak | crewai    | Python     | Status | Notes |
|----------|-----------------|-----------|------------|--------|-------|
| v0.9     | —               | —         | —          | ❌     | Package not yet released |
| v1.0     | 0.1.0           | >=0.1.0   | 3.10–3.12  | ✅     | 1/1 tests passing; tested with `crewai==1.10.1` |
| v1.0     | 0.1.0           | >=1.0     | 3.10–3.13  | ✅     | Cali system: `crewai==1.10.1` ✅ |

**Install:**
```bash
pip install crewai-tokenpak
```

---

## AutoGen Integration (`autogen-tokenpak`)

Separate installable package. Compatible with both `pyautogen` and the newer
`autogen-agentchat` / `autogen-core` packages.

**Reference:** https://pypi.org/project/pyautogen/ | https://pypi.org/project/autogen-agentchat/

| tokenpak | autogen-tokenpak | pyautogen  | autogen-core | Python     | Status | Notes |
|----------|------------------|------------|--------------|------------|--------|-------|
| v0.9     | —                | —          | —            | —          | ❌     | Package not yet released |
| v1.0     | 0.1.0            | >=0.2.0    | —            | 3.10–3.12  | ✅     | 1/1 tests passing |
| v1.0     | 0.1.0            | 0.10.0     | 0.7.5        | 3.10–3.13  | ✅     | Cali system: both packages installed |
| v1.0     | 0.1.0            | >=0.10     | >=0.7        | 3.10–3.13  | 🔲     | Newer versions not tested |

**Install:**
```bash
pip install autogen-tokenpak
```

---

## Langfuse Integration (`langfuse-tokenpak`)

Separate installable package for tracing and observability. Langfuse is optional.

**Reference:** https://pypi.org/project/langfuse/

| tokenpak | langfuse-tokenpak | langfuse   | Python     | Status | Notes |
|----------|-------------------|------------|------------|--------|-------|
| v0.9     | —                 | —          | —          | ❌     | Package not yet released |
| v1.0     | 0.1.0             | >=2.0.0    | 3.10–3.12  | ✅     | 30/30 tests passing (langfuse itself not required for tests) |
| v1.0     | 0.1.0             | >=3.0      | 3.10–3.13  | 🔲     | Not tested; langfuse SDK is mostly optional |

**Install:**
```bash
pip install langfuse-tokenpak
pip install "langfuse-tokenpak[langfuse]"   # to include langfuse SDK
pip install "langfuse-tokenpak[langchain]"  # langchain + langfuse combo
```

---

## Google Vertex AI / Gemini

TokenPak proxy intercepts `googleapis.com` (rate-limit detection). Native SDK output
format is not implemented — Google-bound calls go through the proxy or via LiteLLM routing.

**Reference:** https://cloud.google.com/vertex-ai/docs

| TokenPak | Vertex AI SDK | Python     | Status | Notes |
|----------|---------------|------------|--------|-------|
| v0.9     | any           | —          | ❌     | Not targeted |
| v1.0     | any           | 3.10–3.13  | ⚠️     | Proxy passthrough only; no `to_vertex()` output format |
| future   | TBD           | —          | 🔲     | Planned in roadmap (native Gemini format) |

> If you need Vertex AI support today, route through LiteLLM (`litellm` supports Vertex natively).

---

## Tiktoken (Token Counting)

Optional dependency for accurate token counting. Without it, TokenPak uses a character-based heuristic.

**Reference:** https://pypi.org/project/tiktoken/

| TokenPak | tiktoken    | Python     | Status | Notes |
|----------|-------------|------------|--------|-------|
| v0.9     | >=0.5.0     | 3.10–3.12  | ⚠️     | Optional; basic heuristic used if absent |
| v1.0     | >=0.5.0     | 3.10–3.13  | ✅     | Optional extra: `pip install "tokenpak[tokens]"` |
| v1.0     | 0.12.0      | 3.10–3.13  | ✅     | Tested on Cali with `tiktoken==0.12.0` |

---

## Python Version Support

| Python | TokenPak v0.9 | TokenPak v1.0 |
|--------|--------------|--------------|
| 3.9    | ⚠️            | ❌            |
| 3.10   | ✅            | ✅            |
| 3.11   | ✅            | ✅            |
| 3.12   | ✅            | ✅ (primary dev env: 3.12.3) |
| 3.13   | 🔲            | ✅            |

---

## Quick Install Reference

```bash
# Core only (proxy mode, no SDK deps)
pip install tokenpak

# With token counting
pip install "tokenpak[tokens]"

# Framework adapters (install the ones you need)
pip install langchain-tokenpak
pip install llamaindex-tokenpak
pip install crewai-tokenpak
pip install autogen-tokenpak
pip install langfuse-tokenpak

# LiteLLM integration (built into tokenpak, install litellm separately)
pip install litellm
```

---

*Matrix generated by Cali — 2026-03-08. Based on code audit of `~/tokenpak/`, installed package versions,
and test results from `packages/ADAPTER-STATUS-2026-03-07.md`.*
