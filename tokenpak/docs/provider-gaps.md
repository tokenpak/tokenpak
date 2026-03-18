---
title: "TokenPak Provider Gaps Report"
description: "TokenPak Provider Gaps Report"
status: active
owner: Kevin
created: 2026-03-11
tags: [project]
---
# TokenPak Provider Gaps Report

> Audited: 2026-03-08  
> Cross-reference: `provider-feature-matrix.md`

---

## Gap 1: Function Calling in Google

**Missing in:** Google (`GoogleFormat` adapter)  
**Impact:** 🔴 Workflow-breaking — any agent or workflow relying on tool use cannot use the Google adapter.  
**Evidence:** `GoogleFormat.build_request()` has no `tools` or `function_declarations` parameter. `GoogleContent` has no tool-related fields.  
**Workaround:** Use OpenAI or Anthropic adapter. Both fully support function calling with bidirectional translation.  
**Future plan:** Google Gemini supports function calling natively via `tools[].functionDeclarations`. Implementation would require adding a `tools` param to `build_request()` and a `_tools_openai_to_google()` translator in `translator.py`.  
**Recommendation:** Document as "Google adapter: no tool calling" in quickstart. Mark Google as unsuitable for agentic/tool-using workloads until fixed.

---

## Gap 2: Google Streaming Detection (Body-Level)

**Missing in:** Google (`GoogleFormat.is_streaming()`)  
**Impact:** 🟡 Low severity — streaming still works at the transport layer, but `is_streaming()` always returns `False`. Any code path that checks this flag to decide behavior (e.g., metrics routing, capsule bypass) will treat Google requests as non-streaming even when they are.  
**Evidence:** `google.py` line 113: `return False  # Must be determined from URL`  
**Workaround:** None needed for end-users — streaming requests still stream. Internal code relying on `is_streaming()` may produce incorrect telemetry for Google streaming calls.  
**Future plan:** Pass URL/path context into `is_streaming()` or detect `?alt=sse` at the proxy routing layer and set a request attribute.  
**Recommendation:** Add `streamGenerateContent` URL path detection in proxy routing layer, set a flag on the request context.

---

## Gap 3: Google Input Token Count Missing

**Missing in:** Google (`GoogleFormat.extract_response_tokens()` and telemetry)  
**Impact:** 🟡 Medium — cost attribution for Google is incomplete. Input token costs cannot be calculated from the response body, only output.  
**Evidence:** `google.py` `extract_response_tokens()` reads only `usageMetadata.candidatesTokenCount`. Google API also returns `promptTokenCount` and `totalTokenCount` in `usageMetadata`.  
**Workaround:** Approximate input tokens using `count_tokens_approx()` heuristic (4 chars/token).  
**Future plan:** Update `extract_response_tokens()` or add `extract_input_tokens()` reading `usageMetadata.promptTokenCount`.  
**Recommendation:** Quick fix — add 2 lines to parse `promptTokenCount` from Google response body.

---

## Gap 4: Tool Schema Freezing (Google)

**Missing in:** Google adapter — no Google-specific tool schema normalizer  
**Impact:** 🟡 Medium — when Google function calling is eventually added, tool schema freezing for prompt-cache stability will not work.  
**Evidence:** `tool_schema_registry.py` normalizes OpenAI/Anthropic `tools` array format. Google uses `tools[].functionDeclarations` with different field names.  
**Workaround:** N/A — Google function calling not yet implemented (see Gap 1).  
**Future plan:** Add a Google-specific normalization path in `tool_schema_registry.py` once function calling is implemented.  
**Recommendation:** Defer until Gap 1 is addressed.

---

## Gap 5: OpenAI Input Token Count (Native)

**Missing in:** OpenAI adapter — no native `extract_input_tokens()` method  
**Impact:** 🟢 Low — OpenAI responses include `usage.prompt_tokens`. The adapter only extracts `completion_tokens`. Input token counting falls back to heuristic (4 chars/token).  
**Evidence:** `openai.py` `extract_response_tokens()` reads `usage.completion_tokens` only.  
**Workaround:** Heuristic approximation. Accurate enough for most budgeting purposes.  
**Future plan:** Add `extract_input_tokens()` reading `usage.prompt_tokens` from response.  
**Recommendation:** Low-priority fix. Add alongside Anthropic parity work.

---

## Summary Table

| Gap | Provider | Severity | Breaks Workflows? | Fix Complexity |
|-----|----------|----------|--------------------|----------------|
| Function calling missing | Google | 🔴 High | Yes (tool-using agents) | Medium |
| Streaming body detection | Google | 🟡 Medium | No (transport still works) | Low |
| Input token count missing | Google | 🟡 Medium | No (approximate fallback) | Low |
| Tool schema freezing | Google | 🟡 Medium | No (not yet needed) | Medium |
| Input token count (native) | OpenAI | 🟢 Low | No (approximate fallback) | Low |

---

## Recommended Fix Order

1. **Google input token count** — 2-line fix, immediate accuracy improvement
2. **Google streaming detection** — routing-layer flag, low risk
3. **OpenAI input token extraction** — simple, completes parity
4. **Google function calling** — significant work, enables new use cases
5. **Google tool schema freezing** — dependent on #4
