---
title: "TokenPak Provider Feature Matrix"
description: "TokenPak Provider Feature Matrix"
status: active
owner: Kevin
created: 2026-03-11
tags: [project]
---
# TokenPak Provider Feature Matrix

> Audited: 2026-03-08  
> Source: `~/tokenpak/tokenpak/agent/proxy/providers/`  
> All entries verified against source code — no claims without code evidence.

## Feature Matrix

| Feature | OpenAI | Anthropic | Google |
|---------|--------|-----------|--------|
| **Compression / Capsule** | ✅ Full | ✅ Full | ✅ Full |
| **Streaming** | ✅ Yes | ✅ Yes | ⚠️ URL-only (not body flag) |
| **Function / Tool Calling** | ✅ Yes | ✅ Yes | ❌ Not implemented |
| **Vision / Images** | ✅ Yes | ✅ Yes | ✅ Yes |
| **Tool Schema Freezing** | ✅ Yes | ✅ Yes | ⚠️ Partial (no Google normalizer) |
| **Token Counting** | ⚠️ Estimated | ✅ Native (input + output + cache) | ⚠️ Output only (estimated input) |
| **Rate Limit Headers** | ✅ Forwarded | ✅ Forwarded | ✅ Forwarded |
| **Cost Attribution** | ✅ Yes | ✅ Full (incl. cache tiers) | ⚠️ Partial (output tokens only) |

## Feature Notes

### Compression / Capsule
All three providers benefit from the capsule builder (`capsule_integration.py`) which is provider-agnostic — it operates on message content before the request is formatted for the upstream API.

### Streaming
- **OpenAI**: `is_streaming()` reads `stream: true` from request body. `build_request()` defaults to `stream=True`.
- **Anthropic**: Same pattern — `stream` field in body.
- **Google**: `is_streaming()` always returns `False`. Comment in code: _"Google uses `?alt=sse` for streaming — must be determined from URL, not body."_ Streaming works at the transport layer but the adapter has no body-level detection.

### Function / Tool Calling
- **OpenAI**: `OpenAIMessage` has `tool_calls` and `tool_call_id` fields; `count_tokens_approx()` accounts for `tools` array.
- **Anthropic**: Full bidirectional translation via `translator.py` (`_tools_anthropic_to_openai`, `_tools_openai_to_anthropic`, tool_use blocks ↔ tool_calls).
- **Google**: `GoogleFormat` has no tools/function fields. `GoogleContent.parts` has no tool handling. `build_request()` accepts no tools param. Marked as **stub** in module docstring.

### Vision / Images
- **OpenAI**: `image_url` content part detected in `count_tokens_approx()` (+1000 token estimate).
- **Anthropic**: `type: image` content block detected (+1000 token estimate).
- **Google**: `inline_data` part detected in `count_tokens_approx()` (+1000 token estimate).

### Tool Schema Freezing
`tool_schema_registry.py` is a singleton that normalizes tool arrays deterministically (sorted by name, sorted keys) to stabilize Anthropic prompt cache hits. It works natively with OpenAI/Anthropic format `tools` arrays. Google uses a different schema structure (`contents`/`functionDeclarations`) with no dedicated normalizer.

### Token Counting
- **OpenAI**: `extract_response_tokens()` reads `usage.completion_tokens`. Input token counting uses 4-chars-per-token heuristic (tiktoken is an optional dependency in `budgeter.py`, not in the provider adapter).
- **Anthropic**: `extract_response_tokens()` reads `usage.output_tokens`. `extract_cache_tokens()` additionally reads `cache_read_input_tokens` and `cache_creation_input_tokens`. Most complete implementation.
- **Google**: `extract_response_tokens()` reads `usageMetadata.candidatesTokenCount` (output only). No input token extraction from response body. Input counting is approximate.

### Rate Limit Headers
All providers: upstream response headers are forwarded back to the client via `server_async.py` (filters out hop-by-hop headers like `content-length`, `transfer-encoding`). TokenPak's own internal rate limiter emits `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` headers independently of provider.

### Cost Attribution
- **OpenAI/Anthropic**: Telemetry models (`telemetry/models.py`) track `cost_input`, `cost_output`, `cost_cache_read`, `cost_cache_write`, `cost_total`.
- **Anthropic**: Most complete — cache tier costs tracked separately.
- **Google**: Only output (`candidatesTokenCount`) reported from API response. Input tokens not parsed from response → cost attribution is incomplete.
