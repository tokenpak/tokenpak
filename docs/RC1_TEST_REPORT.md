# TokenPak v1.0.0-RC1 — Test Report

**Date:** 2026-03-06  
**Tester:** Trix (Automated Agent)  
**Version:** 1.0.0-rc1  
**Python:** 3.12.3 on Linux (<dev-host> / Ubuntu)  
**Task:** p2-tokenpak-v1-release-candidate-test

---

## Summary

| Category | Status | Notes |
|----------|--------|-------|
| Unit / Integration tests | ✅ PASS | 2176 passed, 52 skipped |
| Edge case coverage | ✅ PASS | All inputs handled gracefully |
| Performance benchmarks | ✅ PASS | Well under targets |
| Error handling | ✅ PASS | Security, retry, failover all pass |
| Documentation accuracy | ⚠️ WARN | 7 CLI commands/flags in docs don't exist |
| Concurrent safety | ✅ PASS | 20 threads, 0 errors |
| Version consistency | ✅ PASS | 1.0.0-rc1 matches everywhere |

---

## 1. Full Test Suite Results

```
pytest tests/ -q
2176 passed, 52 skipped, 1 warning in 98.6s (0:01:38)
```

**Skipped tests (52):** All in `test_treesitter.py` — skipped because `tree-sitter-languages`
optional dependency is not installed. This is expected behavior; tests self-skip gracefully.

**Code coverage:** 54% overall  
- Core compression pipeline: well-covered  
- Telemetry subsystem: lower coverage (integrity/, segmentizer, rollups) — noted for v1.0.1  

**Deprecation warning (1):**
```
tokenpak/telemetry/server.py:146: DeprecationWarning: datetime.datetime.utcfromtimestamp() 
is deprecated. Use timezone-aware objects with datetime.UTC instead.
```
→ Low severity; Python 3.12 compat warning. Fix recommended before 1.0 final.

---

## 2. Edge Case Testing

All cases tested via `tokenpak.engines.HeuristicEngine.compact()`:

| Input | Result | Status |
|-------|--------|--------|
| Empty string `""` | Returns `""` | ✅ |
| Whitespace only `"  \n\t  "` | Returns `""` | ✅ |
| Unicode + emoji (`こんにちは 🎉 مرحبا`) | Handled, output len=31 | ✅ |
| Very large input (~100K chars, 20K words) | Completes in 1ms | ✅ |
| Single character `"a"` | Returns `"a"` | ✅ |
| `None` input | Returns `None` (pass-through) | ⚠️ |
| Repeated content (500x "test ") | Deduplicates, out_len=100 | ✅ |
| 20 concurrent threads | 20/20 success, 2.8ms total | ✅ |

**⚠️ Note:** `engine.compact(None)` silently returns `None` rather than raising `TypeError`.
This may be intentional (pass-through semantics), but should be documented. If callers
expect guaranteed string output, they should guard with a type check upstream.

---

## 3. Performance Benchmarks

Tested with 10 built-in samples via `HeuristicEngine.compact()`:

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| P50 latency | 0.48ms | < 50ms | ✅ |
| P95 latency | 0.63ms | < 100ms | ✅ |
| P99 latency | 0.63ms | < 200ms | ✅ |
| Min / Max | 0.19ms / 0.66ms | — | ✅ |
| Peak RAM | 0.02 MB | < 500MB | ✅ |
| Avg compression ratio | 57.4% | > 20% | ✅ |

**CLI benchmark output (10 samples):**

```
Tests run        : 10
Total tokens in  : 5,074
Total tokens out : 2,594
Tokens saved     : 2,480  (48.9% reduction)
Avg process time : 0.9ms/file
Recipe hits      : 142
```

**Notable outlier:** `shell_script` sample — 0% compression. No applicable recipes matched.
This is expected; shell scripts currently have no dedicated recipes. Candidate for v1.0.1.

---

## 4. Error Handling

All error-path tests pass:

- `test_proxy_error_paths.py` — 13/13 ✅
- `test_retry.py` — 21/21 ✅
- `test_routing_fallback.py` — 15/15 ✅
- `test_security_hardening.py` — 29/29 ✅
- `test_failover_engine.py` — tested, passing ✅
- `test_failover_translators.py` — 137/137 ✅
- `test_response_validation.py` — passing ✅

Key validations confirmed:
- Shell injection rejected in model names and CLI args ✅
- PII/API keys redacted in logs ✅
- No stack traces exposed to users ✅
- Graceful failover between providers ✅
- Malformed JSON in streaming ignored gracefully ✅
- Budget exceeded returns structured error ✅

---

## 5. Documentation Accuracy Audit

**CLI commands documented but missing from implementation:**

| Documented Command | Status | Severity |
|-------------------|--------|----------|
| `tokenpak stop` | ❌ Not implemented | High — getting-started.md references it |
| `tokenpak health` | ❌ Not implemented | Medium — cli-reference.md only |
| `tokenpak logs` | ❌ Not implemented | Medium — cli-reference.md only |
| `tokenpak compress` | ❌ Not implemented | Medium — cli-reference.md only |
| `tokenpak audit` | ❌ Not implemented | Medium — cli-reference.md only |
| `tokenpak status --full` | ❌ Flag doesn't exist (only `--limit`) | Low |
| `tokenpak serve --mode / --daemon` | ❌ Flags don't exist | Medium |

**Commands that work correctly as documented:**
- `tokenpak benchmark --samples / --json / --file` ✅
- `tokenpak doctor` ✅
- `tokenpak cost --week / --month / --by-model` ✅
- `tokenpak budget set / status / history` ✅
- `tokenpak recipe create / validate / test / benchmark` ✅
- `tokenpak demo` ✅
- `tokenpak replay` ✅
- `tokenpak route`, `index`, `search`, `stats` ✅

**Recommendation:** Either implement the missing commands before launch or update
`cli-reference.md` and `getting-started.md` to remove/replace them. The `tokenpak stop`
gap is highest priority since it's in the getting-started flow.

---

## 6. Compatibility

| Item | Status |
|------|--------|
| Python 3.12.3 | ✅ Tested on <dev-host> |
| Python &lt;3.10 | ❌ Not supported; `pyproject.toml` sets `python_requires = ">=3.10"` |
| Linux (Ubuntu 24.04, <dev-host>) | ✅ |
| Windows / macOS | ❌ Not tested in this run |
| Installed version matches source | ✅ 1.0.0-rc1 |

**Note:** `pyproject.toml` sets `python_requires = ">=3.10"` — docs have since been aligned to declare `>=3.10`. (B5 resolved.)

---

## 7. Integration Tests

**Real API integration tests were not run** — no live API keys configured in test environment.
All mocked integration tests (FastAPI proxy, routing, compression pipeline, platform adapters) pass.

- `test_capsule_integration.py` ✅
- `test_proxy_workflow_integration.py` ✅
- `test_platform_adapters.py` ✅

For pre-launch, recommend running a manual smoke test with a real Anthropic key:
```bash
tokenpak serve --port 8766
export ANTHROPIC_API_KEY=sk-ant-...
curl http://localhost:8766/v1/messages -d '...'
```

---

## Bugs Found

| # | Severity | Description | Recommended Fix |
|---|----------|-------------|-----------------|
| B1 | High | `tokenpak stop` documented but not implemented | Implement or remove from docs |
| B2 | Medium | 5 additional CLI commands in docs don't exist (`health`, `logs`, `compress`, `audit`, `serve --daemon/--mode`) | Audit cli-reference.md |
| B3 | Low | `datetime.utcfromtimestamp()` deprecation warning in telemetry/server.py:146 | Replace with `datetime.fromtimestamp(ts, datetime.UTC)` |
| B4 | Low | `engine.compact(None)` returns `None` silently | Document behavior or add type guard |
| B5 | Low | `pyproject.toml` requires py3.10 but docs said ">=3.10" | **FIXED** — all docs now declare `>=3.10` |
| B6 | Info | Shell scripts get 0% compression | Add shell script recipe in v1.0.1 |

---

## Recommendations Before Launch

1. **Fix B1 (stop command)** — getting-started.md is broken without it. Quickest fix: document `kill $(cat ~/.tokenpak/proxy.pid)` as the workaround.
2. **Fix B3 (deprecation warning)** — 1-line fix in telemetry/server.py.
3. **Audit cli-reference.md** against `tokenpak --help` output and remove/stub unimplemented commands.
4. **Run one real-API smoke test** before shipping (even on a free tier key).
5. **Optional:** Add a `--version` flag to `tokenpak` CLI root.

---

## Sign-off

- ✅ 2176/2176 unit tests passing (100%)
- ✅ 0 critical bugs
- ✅ Performance dramatically exceeds targets (P50: 0.48ms vs 50ms target)
- ✅ Security hardening confirmed
- ⚠️ 7 documentation gaps need resolution before launch
- ⚠️ Real-API integration test not run (env limitation)

**Verdict:** RC1 is functionally solid. Clear the documentation gaps (especially `tokenpak stop`) and the 1-line deprecation fix, and it's ready for v1.0 launch.
