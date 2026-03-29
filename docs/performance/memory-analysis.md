
======================================================================
📊 TOKENPAK PROXY MEMORY PROFILER — BASELINE REPORT
======================================================================
Profile Date: 2026-03-25T05:47:07.893698
Proxy URL:   http://localhost:8766

### Memory Usage Summary
| Scenario | Memory (MB) | Notes |
|----------|-------------|-------|
| Idle | 30.12 | System baseline |
| Single Request | +0.01 | Peak after 1 request |
| 50 Concurrent Requests | +1.7 | Peak delta |
| 100 Concurrent Requests | +0.02 | Peak delta |
| 250 Concurrent Requests | +0.1 | Peak delta |

### Detailed Results

{
  "idle": {
    "memory_mb": 30.12,
    "memory_percent": 0.79,
    "timestamp": "2026-03-25T05:47:01.200440"
  },
  "single_request": {
    "memory_before_mb": 30.12,
    "memory_after_mb": 30.13,
    "memory_delta_mb": 0.01,
    "response_status": 401,
    "timestamp": "2026-03-25T05:47:01.401986"
  },
  "concurrent_50": {
    "num_requests": 50,
    "memory_before_mb": 30.13,
    "memory_after_mb": 31.83,
    "memory_peak_mb": 31.83,
    "memory_delta_mb": 1.7,
    "elapsed_seconds": 2.04,
    "requests_per_second": 24.55,
    "timestamp": "2026-03-25T05:47:03.439380"
  },
  "concurrent_100": {
    "num_requests": 100,
    "memory_before_mb": 31.83,
    "memory_after_mb": 31.85,
    "memory_peak_mb": 31.85,
    "memory_delta_mb": 0.02,
    "elapsed_seconds": 1.37,
    "requests_per_second": 73.13,
    "timestamp": "2026-03-25T05:47:04.807573"
  },
  "concurrent_250": {
    "num_requests": 250,
    "memory_before_mb": 31.85,
    "memory_after_mb": 31.95,
    "memory_peak_mb": 31.95,
    "memory_delta_mb": 0.1,
    "elapsed_seconds": 3.08,
    "requests_per_second": 81.06,
    "timestamp": "2026-03-25T05:47:07.892677"
  }
}

### Memory Optimization Candidates
1. **Index Lazy-Loading**: Defer full index load until needed
2. **Cache Eviction**: Implement LRU cache with size limits
3. **Object Pooling**: Reuse request/response objects
4. **Compression Caching**: Cache decompressed blocks to reduce repeated work
5. **Memory Monitoring**: Add periodic GC and memory tracking

======================================================================
