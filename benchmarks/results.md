# TokenPak Performance Benchmark Results

**Generated:** 2026-03-17 12:53  
**Environment:** linux | Python 3.12.3

---

## Scenario 1: Compression Effectiveness

| Test | Type | Tokens Before | Tokens After | Saved | Ratio |
|------|------|--------------|--------------|-------|-------|
| python_module | code | 596 | 324 | 272 | 45.6% |
| markdown_readme | text | 313 | 298 | 15 | 4.8% |
| json_config | data | 299 | 208 | 91 | 30.4% |
| javascript_class | code | 722 | 82 | 640 | 88.6% |
| yaml_config | data | 464 | 209 | 255 | 55.0% |
| plain_text_prose | text | 282 | 48 | 234 | 83.0% |
| typescript_interface | code | 511 | 408 | 103 | 20.2% |
| python_test_file | code | 886 | 329 | 557 | 62.9% |
| shell_script | code | 560 | 560 | 0 | 0.0% |
| ci_yaml | data | 441 | 128 | 313 | 71.0% |
| **TOTAL** | | **5,074** | **2,594** | **2,480** | **48.9%** |

- Average processing time: 0.90ms/file

## Scenario 2: Token Counting Cache

| Metric | Value |
|--------|-------|
| Cold cache (avg) | 58.49ms |
| Warm cache (avg) | 0.01ms |
| Cache speedup | **10357x** |

## Scenario 3 & 4: Indexing Performance

| Metric | Value |
|--------|-------|
| Total files | 1,241 |
| Total time | 1915ms |
| Per-file latency | 1.544ms |
| Throughput | **647.9 files/sec** |
| Baseline throughput | 42.2 files/sec |
| Optimized speedup | **15.4x faster** |

## Scenario 5: Live Proxy Metrics (Session)

| Metric | Value |
|--------|-------|
| Total requests | 3,900 |
| Cache hit rate | **89.9%** |
| Token reduction | **9.2%** |
| Avg latency (today) | 5788.0ms |
| Total cost | $268.13 |
| Error rate | 1.0% |

---

## Summary

| Metric | Result | Target | Status |
|--------|--------|--------|--------|
| Token reduction | 48.9% | ≥30% | ✅ |
| Cache speedup | 10357x | ≥100x | ✅ |
| Indexing throughput | 648 files/sec | ≥100 | ✅ |
| Per-file latency | 1.544ms | <20ms | ✅ |
