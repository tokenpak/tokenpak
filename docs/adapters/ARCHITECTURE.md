# TokenPak Adapter Architecture

**Last updated:** 2026-03-09  

---

## Overview

TokenPak ships with two complementary adapter systems:

| System | Location | Purpose |
|--------|----------|---------|
| **SDK Adapters** | `tokenpak/adapters/` | Route SDK/framework calls through proxy |
| **Platform Adapters** | `tokenpak/agent/adapters/` | Detect calling platform (Claude CLI, SDK clients, etc.) |
| **Telemetry Adapters** | `tokenpak/telemetry/adapters/` | Parse provider payloads into canonical usage types |

This document describes the **SDK Adapters** layer — the unified interface for sending requests through the TokenPak proxy from any SDK or framework.

---

## Design Goals

1. **Zero code changes for callers** — responses are shaped exactly as each provider's SDK returns them
2. **Unified error hierarchy** — no leaking of `requests` or SDK exceptions
3. **Consistent token accounting** — all adapters return the same `extract_tokens()` dict shape
4. **Composable delegation** — LangChain and LiteLLM adapters delegate to Anthropic/OpenAI adapters
5. **Defensive logging** — every adapter uses `self.logger` for DEBUG/INFO/WARNING/ERROR

---

## Base Interface

```python
# tokenpak/adapters/base.py

class TokenPakAdapter(ABC):
    def __init__(self, base_url: str, api_key: str, timeout_s: float = 120.0): ...
    
    def prepare_request(self, request: dict) -> dict: ...
    """Validate + normalise SDK request → proxy format."""
    
    def send(self, prepared_request: dict) -> dict: ...
    """POST to proxy, raise canonical exceptions on failure."""
    
    def parse_response(self, response: dict) -> dict: ...
    """Convert proxy response → provider-native format."""
    
    def extract_tokens(self, response: dict) -> dict: ...
    """Return { input_tokens, output_tokens, cache_read, cache_write, total }."""
    
    def call(self, request: dict) -> dict: ...
    """Convenience: prepare → send → parse_response in one call."""
```

### extract_tokens() Return Shape

Every adapter's `extract_tokens()` returns a dict with these keys:

| Key | Type | Description |
|-----|------|-------------|
| `input_tokens` | int | Billed input tokens |
| `output_tokens` | int | Billed output tokens |
| `cache_read` | int | Tokens served from cache (0 if N/A) |
| `cache_write` | int | Tokens written to cache (0 if N/A) |
| `total` | int | `input_tokens + output_tokens` |

---

## Exception Hierarchy

```
TokenPakAdapterError          — base for all adapter errors
├── TokenPakTimeoutError      — request exceeded timeout_s
├── TokenPakAuthError         — HTTP 401/403 from proxy
└── TokenPakConfigError       — missing/invalid config or request fields
```

Adapters **never** raise bare `requests.exceptions.*` or provider SDK exceptions.

---

## Concrete Adapters

### AnthropicAdapter

**Path:** `tokenpak/adapters/anthropic.py`  
**Proxy endpoint:** `POST /v1/messages`  
**Required request fields:** `model`, `messages`, `max_tokens`

**Request flow:**
1. Validates required fields and message structure
2. Adds `stream: false` default
3. POSTs with `x-api-key` + `anthropic-version: 2023-06-01` headers

**Token extraction:**
- `usage.input_tokens` → `input_tokens`
- `usage.output_tokens` → `output_tokens`  
- `usage.cache_read_input_tokens` → `cache_read`
- `usage.cache_creation_input_tokens` → `cache_write`

**Audit status:**
- ✅ Request format validation
- ✅ Response parsing correct
- ✅ Token counting accurate (prompt cache fields included)
- ✅ Error handling consistent

---

### OpenAIAdapter

**Path:** `tokenpak/adapters/openai.py`  
**Proxy endpoint:** `POST /v1/chat/completions`  
**Required request fields:** `model`, `messages`

**Request flow:**
1. Validates required fields and message structure
2. Promotes legacy `functions` → `tools`
3. Adds `stream: false` default
4. POSTs with `Authorization: Bearer {api_key}` header

**Token extraction:**
- `usage.prompt_tokens` → `input_tokens`
- `usage.completion_tokens` → `output_tokens`
- `usage.prompt_tokens_details.cached_tokens` → `cache_read`
- `cache_write` always 0 (OpenAI doesn't expose cache-write counts)

**Audit status:**
- ✅ Request format validation
- ✅ Base URL routing correct
- ✅ Response format matches OpenAI spec
- ✅ Token counting accurate (cached tokens field included)
- ✅ Legacy `functions` → `tools` promotion

---

### LangChainAdapter

**Path:** `tokenpak/adapters/langchain.py`  
**Delegates to:** `AnthropicAdapter` or `OpenAIAdapter`

**Extra responsibilities:**
- Normalises LangChain role names: `human` → `user`, `ai` → `assistant`
- Reads `provider` field from request to select delegate adapter
- Strips LangChain-specific metadata before forwarding
- Provider detection on response: uses response shape to pick delegate

**Audit status:**
- ✅ Role normalisation (human/ai/system/function/tool)
- ✅ Provider routing (openai / anthropic)
- ✅ Token counting delegates correctly
- ⚠️ `ChatAnthropic` requires `max_tokens` in request (enforced via AnthropicAdapter validation)

---

### LiteLLMAdapter

**Path:** `tokenpak/adapters/litellm.py`  
**Delegates to:** `AnthropicAdapter` or `OpenAIAdapter`

**Extra responsibilities:**
- Parses `provider/model` prefixes (e.g. `"anthropic/claude-3-5-sonnet-20241022"`)
- Strips prefix before forwarding bare model name to proxy
- Falls back to OpenAI adapter for unknown prefixes (LiteLLM default)

**Supported prefixes:**
- `anthropic/...` → AnthropicAdapter
- `claude...` → AnthropicAdapter
- `openai/...` → OpenAIAdapter
- `gpt...` → OpenAIAdapter
- `o1...`, `o3...` → OpenAIAdapter
- (others) → OpenAIAdapter (fallback)

**Audit status:**
- ✅ Provider prefix parsing
- ✅ All provider routing works
- ✅ Token tracking accurate (delegates to provider adapter)
- ✅ OpenAI fallback for unknown prefixes

---

## Adding a New Adapter

To add a new adapter (e.g. `CursorAdapter`):

1. Create `tokenpak/adapters/cursor.py`
2. Subclass `TokenPakAdapter`
3. Set `provider_name = "cursor"`
4. Implement all four abstract methods
5. Raise only from the canonical exception hierarchy
6. Add to `tokenpak/adapters/__init__.py`
7. Add unit tests in `tests/unit/test_adapters.py`
8. Add a row to this architecture doc's audit table

### Checklist for New Adapters

```
[ ] Subclasses TokenPakAdapter
[ ] provider_name set
[ ] prepare_request: validates required fields, raises TokenPakConfigError
[ ] send: wraps requests.Timeout → TokenPakTimeoutError
[ ] send: wraps 401/403 → TokenPakAuthError
[ ] send: wraps other HTTP errors → TokenPakAdapterError
[ ] parse_response: surfaces provider error blocks
[ ] extract_tokens: returns all 5 keys (input, output, cache_read, cache_write, total)
[ ] Uses self.logger (not print/bare logging)
[ ] Unit tests covering: prepare validation, extract_tokens, error cases
[ ] Integration test (can be skipped if requires external service)
```

---

## Relationship to Other Adapter Systems

### Platform Adapters (`tokenpak/agent/adapters/`)

Detect **which client platform** is calling the proxy (Claude CLI, SDK clients, Generic).
Used internally by the proxy pipeline for routing hints. Not for external SDK use.

### Telemetry Adapters (`tokenpak/telemetry/adapters/`)

Parse **provider response payloads** into canonical `CanonicalRequest` / `CanonicalResponse` /
`CanonicalUsage` types for the metrics pipeline. These operate on raw dicts at the
proxy layer, not at the SDK layer.

### SDK Adapters (`tokenpak/adapters/`)  ← this document

Provide a **developer-facing interface** for routing SDK/framework calls through the
proxy. These are what library consumers interact with directly.

---

## Proxy Format Handlers (`tokenpak/agent/proxy/providers/`)

The proxy core uses `AnthropicFormat`, `OpenAIFormat`, `GoogleFormat` classes to handle
the wire format at the HTTP level. These are internal proxy plumbing, separate from the
SDK adapters. Common operations abstracted there:

| Method | AnthropicFormat | OpenAIFormat | GoogleFormat |
|--------|----------------|--------------|--------------|
| `parse_request(body)` | ✅ | ✅ | ✅ |
| `extract_model(data)` | ✅ | ✅ | ✅ (stub) |
| `extract_system(data)` | ✅ | ✅ | ✅ |
| `count_tokens_approx(data)` | ✅ | ✅ | ✅ |
| `is_streaming(data)` | ✅ | ✅ | ⚠️ missing |
| `build_request(...)` | ✅ | ✅ | ❌ TODO |
| `inject_system_content(...)` | ✅ | ❌ missing | ❌ missing |
| `extract_response_tokens(body)` | ✅ | ✅ | ❌ missing |
| `extract_cache_tokens(body)` | ✅ | ❌ missing | ❌ missing |

**Gaps in proxy format handlers (future work):**
- `OpenAIFormat.inject_system_content()` — not implemented
- `GoogleFormat.build_request()` — stub only
- `GoogleFormat.is_streaming()` — not present
- `GoogleFormat.extract_response_tokens()` — not present
- `OpenAIFormat.extract_cache_tokens()` — not present
