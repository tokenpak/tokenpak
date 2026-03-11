# TokenPak Adapter Compatibility Matrix

## Overview

This matrix documents which TokenPak versions are compatible with which SDK versions. TokenPak adapters communicate via HTTP, so compatibility is determined by:

1. **API shape preservation** — the adapter correctly translates between TokenPak and the provider's API
2. **Token usage reporting** — the adapter correctly extracts usage metrics from responses
3. **Error handling** — common error cases are handled correctly

**Status Legend:**
- ✅ **Supported** — tested and working
- ⚠️ **Beta/Experimental** — working but limited testing
- 🔲 **Not tested** — likely works (same API shape) but not verified
- ❌ **Not supported** — breaking changes or deprecated

---

## OpenAI SDK Adapter

The OpenAI adapter routes `chat.completions.create` requests through TokenPak while preserving the full OpenAI Chat Completions API shape.

### Compatibility

| TokenPak | OpenAI SDK | Python | Status | Notes |
|----------|-----------|--------|--------|-------|
| v1.0+ | 1.0–1.x | 3.10+ | ✅ | Stable. Tested with 1.x; API stable since v1.0 |
| v1.0+ | 2.0–2.x | 3.10+ | ✅ | Stable. Tested with 2.26.0 (current). Function calling, vision support |
| v1.0+ | 0.28–0.45 | 3.10+ | 🔲 | Not tested but likely works; API shape stable across v0/v1 |
| v0.9.x | 0.28–1.x | 3.8+ | ⚠️ | Legacy. No longer tested; may have subtle issues |

### Implementation Notes

- **Location:** `tokenpak/adapters/openai.py`
- **HTTP client:** requests library (optional dependency)
- **Token tracking:** Extracts from `usage.prompt_tokens`, `usage.completion_tokens`, `usage.prompt_tokens_details.cached_tokens`
- **Streaming:** Supports streaming responses via server-sent events
- **Tools/Functions:** Handles both `tools` and legacy `functions` (auto-promoted)

### Reference
- [OpenAI Python SDK Releases](https://github.com/openai/openai-python/releases)
- [OpenAI API Versioning](https://platform.openai.com/docs/api-reference/versioning)

---

## Anthropic SDK Adapter

The Anthropic adapter routes `messages.create` requests through TokenPak while preserving the full Anthropic Messages API shape.

### Compatibility

| TokenPak | Anthropic SDK | Python | Status | Notes |
|----------|---------------|--------|--------|-------|
| v1.0+ | 0.28–0.x | 3.10+ | ✅ | Stable. Tested with current SDK; Messages API stable |
| v1.0+ | 0.24–0.27 | 3.8+ | 🔲 | Not tested; API shape compatible, but older tokens reporting |
| v0.9.x | 0.24–0.x | 3.8+ | ⚠️ | Legacy. No longer tested |

### Implementation Notes

- **Location:** `tokenpak/adapters/anthropic.py`
- **HTTP client:** requests library (optional dependency)
- **Token tracking:** Exact usage in every response: `usage.input_tokens`, `usage.output_tokens`, `usage.cache_read_input_tokens`, `usage.cache_creation_input_tokens`
- **Streaming:** Supports streaming responses with token counting via `message_start`, `message_stop` events
- **Models:** Works with Claude 3, 3.5, and newer variants

### Reference
- [Anthropic Python SDK Releases](https://github.com/anthropics/anthropic-sdk-python/releases)
- [Claude API Documentation](https://docs.anthropic.com)

---

## LangChain Integration

The `langchain-tokenpak` package provides a TokenPak callback handler for LangChain.

### Compatibility

| TokenPak | LangChain | Python | Status | Notes |
|----------|-----------|--------|--------|-------|
| v1.0+ | 0.1+ | 3.10+ | ✅ | Stable. Tested with langchain-core 1.2.17 |
| v1.0+ | 0.0.x | 3.9+ | 🔲 | Not tested; likely works with adapters |
| v0.9.x | 0.1+ | 3.10+ | ⚠️ | Legacy. Callback interface may have changed |

### Implementation Notes

- **Location:** `packages/langchain-tokenpak/`
- **Integration type:** LangChain callback handler (integrates at request/response boundary)
- **Supported models:** Works with any LangChain LLM that uses OpenAI/Anthropic backends
- **Token tracking:** Derives from provider token counts reported by LangChain

### Reference
- [LangChain Python SDK Releases](https://github.com/langchain-ai/langchain/releases)
- [LangChain Callbacks Documentation](https://python.langchain.com/docs/modules/callbacks/)

---

## LiteLLM Integration

The `tokenpak/integrations/litellm/` module provides TokenPak adapter for LiteLLM.

### Compatibility

| TokenPak | LiteLLM | Python | Status | Notes |
|----------|---------|--------|--------|-------|
| v1.0+ | 1.0+ | 3.10+ | ✅ | Stable. Tested via adapter interface; works with 1.x+ models |
| v1.0+ | 0.9.x | 3.10+ | 🔲 | Not tested; likely works; API stable |
| v0.9.x | 0.9+ | 3.10+ | ⚠️ | Legacy. No longer tested |

### Implementation Notes

- **Location:** `tokenpak/integrations/litellm/`
- **Integration type:** Adapter wrapping LiteLLM's proxy mode
- **Supported providers:** All LiteLLM-supported providers (OpenAI, Anthropic, Google, Azure, etc.)
- **Token tracking:** Derives from provider-specific token reporting

### Reference
- [LiteLLM Documentation](https://docs.litellm.ai)
- [LiteLLM GitHub Releases](https://github.com/BerriAI/litellm/releases)

---

## Google Vertex AI (via Generative AI SDK)

TokenPak includes proxy-mode support for Google Vertex AI via the generative AI SDK.

### Compatibility

| TokenPak | Google SDK | Python | Status | Notes |
|----------|-----------|--------|--------|-------|
| v1.0+ | 0.45+ | 3.10+ | ⚠️ | Beta. Implemented in proxy_v4.py; limited real-world testing |
| v1.0+ | <0.45 | 3.10+ | 🔲 | Not tested; API shape may differ |

### Implementation Notes

- **Location:** `tokenpak/proxy_v4.py` (provider routing layer)
- **Integration type:** Proxy mode (HTTP request translation)
- **Supported models:** Gemini models via Vertex AI API
- **Token tracking:** Google SDK provides token counts in `usage_metadata`

### Reference
- [Google Generative AI SDK Releases](https://github.com/googleapis/python-genai/releases)
- [Vertex AI API Documentation](https://cloud.google.com/vertex-ai/docs/generative-ai/start/quickstarts/api-quickstart)

---

## Framework Adapters Status

### AutoGen (Microsoft)

| TokenPak | AutoGen | Python | Status | Notes |
|----------|---------|--------|--------|-------|
| v1.0+ | 0.2+ | 3.10+ | 🔲 | Not tested; supports custom client override |

**Location:** Example in docs or framework-adapter ecosystem  
**Integration:** Use OpenAI/Anthropic adapters as custom client  
**Reference:** [AutoGen Documentation](https://microsoft.github.io/autogen/)

### CrewAI

| TokenPak | CrewAI | Python | Status | Notes |
|----------|--------|--------|--------|-------|
| v1.0+ | 0.27+ | 3.10+ | 🔲 | Not tested; uses LangChain/LiteLLM underneath |

**Location:** Via LangChain or LiteLLM adapter  
**Integration:** Override LLM provider with TokenPak-wrapped client  
**Reference:** [CrewAI Documentation](https://docs.crewai.com)

### LlamaIndex

| TokenPak | LlamaIndex | Python | Status | Notes |
|----------|-----------|--------|--------|-------|
| v1.0+ | 0.9+ | 3.10+ | 🔲 | Not tested; use OpenAI/Anthropic adapters as custom LLM |

**Location:** Via custom LLM callback  
**Integration:** Override default LLM with TokenPak wrapper  
**Reference:** [LlamaIndex Documentation](https://docs.llamaindex.ai)

### Langfuse

| TokenPak | Langfuse | Python | Status | Notes |
|----------|----------|--------|--------|-------|
| v1.0+ | 2.0+ | 3.10+ | 🔲 | Not tested; telemetry integration possible |

**Location:** Observability layer on top of adapters  
**Integration:** Combine with any TokenPak adapter for telemetry  
**Reference:** [Langfuse Documentation](https://langfuse.com)

---

## Python Version Support

TokenPak requires **Python 3.10+** (as of v1.0).

**Tested versions:**
- ✅ Python 3.10
- ✅ Python 3.11
- ✅ Python 3.12
- ✅ Python 3.13

**Older Python (3.8–3.9):** Not supported by TokenPak v1.0+. Use v0.9.x if needed.

---

## Proxy Mode Provider Support

The TokenPak proxy (`proxy_v4.py`) supports routing to these providers:

| Provider | Status | Notes |
|----------|--------|-------|
| OpenAI | ✅ | Stable. `/v1/chat/completions` passthrough |
| Anthropic | ✅ | Stable. `/v1/messages` passthrough |
| Google (Vertex AI) | ⚠️ | Beta. Generative AI SDK passthrough |
| Azure (via OpenAI compat) | 🔲 | Not tested but likely works (OpenAI API compatible) |
| LiteLLM | 🔲 | Not tested; acts as provider aggregator |

---

## Known Issues & Workarounds

### Issue: Old OpenAI SDK + Streaming
If using OpenAI SDK < 1.0 with streaming, token counts may be delayed or incomplete.  
**Workaround:** Upgrade to OpenAI SDK 1.x or 2.x.

### Issue: Anthropic SDK Cache Tokens (< 0.24)
Older Anthropic SDK versions don't report cache tokens in usage.  
**Workaround:** Upgrade to latest Anthropic SDK (0.28+).

### Issue: LangChain LLM vs Provider Token Counts
LangChain may derive token counts differently than the underlying provider.  
**Workaround:** Use TokenPak telemetry for ground-truth token tracking; verify against provider bills.

---

## How to Request New Adapter Support

1. **Identify the SDK/framework:** Does it have a documented API?
2. **Check HTTP shape:** Can TokenPak translate HTTP requests/responses?
3. **Token reporting:** Does the SDK report usage metrics?
4. **Open an issue:** [TokenPak GitHub Issues](https://github.com/kaywhy331/tokenpak/issues)

Include:
- SDK name and version
- Expected use case
- API documentation link
- Example code showing how you'd use TokenPak with this SDK

---

## Last Updated

- **Date:** 2026-03-11
- **By:** Cali
- **TokenPak Version:** 1.0+

For current adapter status, check:
- `tokenpak/adapters/` directory
- `packages/*/` subdirectories
- `tokenpak/integrations/` directory
