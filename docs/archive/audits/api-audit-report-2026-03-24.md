---
title: "api-audit-report-2026-03-24"
created: 2026-03-24T19:05:55Z
---
# TokenPak API Documentation Audit Report

**Date:** 2026-03-24
**Audited by:** Cali
**Source of truth:** `packages/core/tokenpak/`

---

## Audit Checklist

### Module 1: `tokenpak.proxy` — Proxy Server & Adapters

| Item | Status | Notes |
|------|--------|-------|
| `FormatAdapter` base class documented | ✅ | All abstract + optional methods covered in api-reference.md |
| `detect()` parameters + return type | ✅ | |
| `normalize()` → CanonicalRequest | ✅ | |
| `denormalize()` → bytes | ✅ | |
| `extract_request_tokens()` signature | ✅ | Returns `(model, int)` tuple — was undocumented |
| `extract_response_tokens()` | ✅ | SSE mode flag documented |
| `extract_query_signal()` | ✅ | 50-word limit documented |
| `inject_system_context()` | ✅ | Cache boundary behavior for Anthropic documented |
| `AdapterRegistry.register()` priority system | ✅ | Default priority table added |
| `AdapterRegistry.detect()` error behavior | ✅ | RuntimeError documented |
| `CanonicalRequest` all fields | ✅ | Including `raw_extra` and `generation` semantics |
| `CanonicalResponse` fields | ✅ | |
| All 5 adapters: detection rules | ✅ | |
| Adapter priority order | ✅ | 300/260/250/240/0 |
| `build_default_registry()` function | ✅ | |
| `OpenAIChatAdapter` `functions` → `tools` migration | ✅ | Previously undocumented |

### Module 2: `tokenpak.proxy.cache` — Cache Layer

| Item | Status | Notes |
|------|--------|-------|
| `LRUCache` constructor params | ✅ | |
| `get()` LRU position update behavior | ✅ | Previously undocumented |
| `set()` per-entry TTL override | ✅ | Previously undocumented |
| `delete()` return value | ✅ | Returns bool |
| `evict_expired()` return value | ✅ | Returns count |
| `metrics_dict()` output shape | ✅ | JSON example added |
| `CacheMetrics.hit_rate` property | ✅ | |
| `get_cache()` singleton behavior | ✅ | Thread-safety documented |
| YAML configuration example | ✅ | |
| `CacheEntry.is_expired()` method | ✅ | |

### Module 3: `tokenpak.telemetry.cost` — Cost Engine

| Item | Status | Notes |
|------|--------|-------|
| `CostEngine` constructor | ✅ | |
| `calculate()` all parameters | ✅ | `event_ts` format clarified |
| `CostResult` all fields | ✅ | `data_source` enum values documented |
| `Pricing` dataclass fields | ✅ | |
| `Pricing.input_per_token` / `output_per_token` properties | ✅ | Previously undocumented |
| Seeded pricing table (key models) | ✅ | Added to api-reference.md |
| Module-level helpers (`calculate_baseline`, `calculate_actual`, `calculate_savings`) | ⚠️ | These exist but are internal helpers — not publicly exported; left undocumented intentionally |

### Module 4: `tokenpak.telemetry` — Error Logging

| Item | Status | Notes |
|------|--------|-------|
| `ErrorLogger.__init__` log_dir param | ✅ | |
| `log_error()` all parameters | ✅ | |
| `log_error()` context field table | ✅ | All optional fields listed |
| Log file naming pattern | ✅ | `errors-YYYY-MM-DD.jsonl` |
| Archive directory | ✅ | |
| `get_error_logger()` singleton | ✅ | |
| `log_exception` decorator signature | ✅ | |
| `ErrorContext` dataclass fields | ✅ | |

### Module 5: Environment Variables

| Item | Status | Notes |
|------|--------|-------|
| Core settings | ✅ | |
| Vault injection vars | ✅ | |
| Capsule builder vars | ✅ | |
| Tier 1 feature flags | ✅ | All 4 documented |
| Tier 2A feature flags | ✅ | All 4 documented |
| Tier 2B cache flag | ✅ | |

---

## Gap Report

### Critical Gaps (Fixed)

1. **`extract_request_tokens()` undocumented** — Returns a `(model_name, int)` tuple, not just an int. No prior docs mentioned this. Fixed in `api-reference.md`.

2. **`set()` per-entry TTL override** — The `ttl_seconds` parameter on `LRUCache.set()` was missing from docs. Fixed.

3. **`OpenAIChatAdapter` legacy `functions` → `tools` mapping** — Adapter silently maps the deprecated `functions` key to `tools`. Not documented anywhere. Fixed.

4. **`AdapterRegistry` detection priority table** — No prior docs showed the priority numbers or detection order. Fixed in `api-reference.md`.

5. **`Pricing.input_per_token` / `output_per_token` computed properties** — Existed in code, not documented. Fixed.

6. **Full environment variable reference** — `proxy_v4.py` has 20+ env vars. Only partial coverage existed in scattered places. Full reference table added to `api-reference.md`.

7. **`CostResult.data_source` field** — Enum values (`"official"`, `"estimated"`, `"fallback"`) were undocumented. Fixed.

### Minor Gaps (Fixed)

8. **`LRUCache.delete()` return value** — Returns `bool` (True if key existed). Not documented.

9. **`evict_expired()` return value** — Returns count of evicted entries. Not documented.

10. **`CacheMetrics.to_dict()` output shape** — JSON example added.

11. **`ErrorContext` full field list** — `stack_trace` field was missing from observability docs.

### Pre-existing Docs Status

| Doc | Status |
|-----|--------|
| `docs/README.md` | ✅ Good — accurate, no broken examples |
| `docs/installation.md` | ✅ Good |
| `docs/QUICKSTART.md` | ✅ Good |
| `docs/adapters.md` | ✅ Good — matches code |
| `docs/error-handling.md` | ✅ Good |
| `docs/features.md` | ✅ Good |
| `docs/ERROR_CODES.md` | ✅ Good |
| `docs/observability.md` | ✅ Good — `log_exception` decorator example is accurate |
| `packages/core/docs/adapters/anthropic.md` | ✅ Good — cache-boundary behavior note present |
| `packages/core/docs/adapters/openai.md` | ✅ Good |
| `packages/core/docs/adapters/google.md` | ✅ Good |

### Out of Scope (Not Audited)

- `tokenpak.compression.*` — Not in task scope
- `tokenpak.cli.*` — Not in task scope
- `tokenpak.agentic.*` — Not in task scope
- `tokenpak.middleware.*` — Not in task scope

---

## New Documentation Created

| File | Description |
|------|-------------|
| `docs/api-reference.md` | Full API reference covering all 6 modules with verified signatures, examples, and field tables |

---

## Conclusion

All critical API methods are now documented. The main prior gap was the absence of a unified API reference — existing docs were user-facing guides (how-to), not developer references (what the code actually does). The new `api-reference.md` fills this gap with code-verified signatures.

No broken code examples found in existing docs.
