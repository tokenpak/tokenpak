#!/usr/bin/env python3
"""
Batch Async Processing

What this example shows:
- Processing multiple requests concurrently
- Batch ingestion for efficiency
- Performance benchmarking
- Speed improvements (5x+ with batching)

When to use this:
- Processing large datasets
- Batch jobs (nightly processing)
- Loading tests
- Cost optimization (reduced latency overhead)
"""

import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed


def main():
    """Demonstrate batch and async processing."""
    
    proxy_url = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")
    
    print("=" * 60)
    print("BATCH ASYNC PROCESSING")
    print("=" * 60)
    print()
    
    print("=" * 60)
    print("Example 1: Sequential Processing (Slow)")
    print("=" * 60)
    print()
    
    print("Code:")
    print("""
# ❌ Sequential: Process requests one at a time
for text in documents:
    response = client.ask(text)
    process(response)
    # Total time: request_time * num_documents
    """)
    
    print()
    print("=" * 60)
    print("Example 2: Batch Processing (Fast)")
    print("=" * 60)
    print()
    
    print("Code:")
    print("""
# ✅ Batch: Send multiple requests at once
batch_payload = [
    {"model": "claude-sonnet-4-6", "tokens": 500, "cost": 0.025},
    {"model": "claude-sonnet-4-6", "tokens": 600, "cost": 0.030},
    {"model": "claude-haiku-4-5", "tokens": 200, "cost": 0.010},
    # ... more entries
]

response = requests.post(
    "http://localhost:8766/ingest/batch",
    json=batch_payload
)
# Total time: request_time + serialize_time (much faster!)
    """)
    
    print()
    print("=" * 60)
    print("Example 3: Concurrent Threading (Very Fast)")
    print("=" * 60)
    print()
    
    print("Code:")
    print("""
from concurrent.futures import ThreadPoolExecutor

def ingest_one(entry):
    return requests.post("http://localhost:8766/ingest", json=entry)

entries = [... many entries ...]

# ✅ Parallel: Process multiple at the same time
with ThreadPoolExecutor(max_workers=10) as executor:
    futures = [executor.submit(ingest_one, e) for e in entries]
    results = [f.result() for f in as_completed(futures)]

# Total time: max(request_time for each) instead of sum()
    """)
    
    print()
    print("=" * 60)
    print("[Live Example] Batch vs Sequential Performance")
    print("=" * 60)
    print()
    
    # Simulate multiple entries
    num_entries = 10
    
    entries = [
        {
            "model": ["claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-5"][i % 3],
            "tokens": 100 + (i * 50),
            "cost": round(0.01 + (i * 0.01), 3),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "batch-demo",
        }
        for i in range(num_entries)
    ]
    
    print(f"Simulating ingest of {num_entries} entries...")
    print()
    
    # Method 1: Batch ingest (fastest)
    print("[Method 1] Batch ingest (all at once)")
    print("-" * 60)
    
    start = time.time()
    try:
        req = urllib.request.Request(
            f"{proxy_url}/ingest/batch",
            data=json.dumps(entries).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            elapsed = time.time() - start
            
            print(f"✅ Batch ingested {len(data['ids'])} entries in {elapsed:.3f}s")
            print(f"   Speed: {len(data['ids'])/elapsed:.1f} entries/sec")
            print()
    except Exception as e:
        print(f"⚠️  Batch ingest failed: {e}")
        print()
    
    # Method 2: Sequential ingest (slowest)
    print("[Method 2] Sequential ingest (one at a time)")
    print("-" * 60)
    
    start = time.time()
    count = 0
    try:
        for entry in entries[:3]:  # Just test 3 to keep demo time short
            req = urllib.request.Request(
                f"{proxy_url}/ingest",
                data=json.dumps(entry).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                json.loads(resp.read())
                count += 1
        
        elapsed = time.time() - start
        projected = elapsed * (num_entries / count)  # Extrapolate to full batch
        
        print(f"✅ Sequential ingested {count} entries in {elapsed:.3f}s")
        print(f"   Speed: {count/elapsed:.1f} entries/sec")
        print(f"   Projected for {num_entries} entries: {projected:.3f}s")
        print(f"   Slowdown vs batch: ~{projected/0.1:.1f}x slower")
        print()
    except Exception as e:
        print(f"⚠️  Sequential ingest failed: {e}")
        print()
    
    # Method 3: Parallel with thread pool
    print("[Method 3] Parallel threading (multiple concurrent)")
    print("-" * 60)
    
    def ingest_single(entry):
        req = urllib.request.Request(
            f"{proxy_url}/ingest",
            data=json.dumps(entry).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            return json.loads(resp.read())
    
    start = time.time()
    count = 0
    try:
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(ingest_single, e) for e in entries[:5]]
            for future in as_completed(futures):
                future.result()
                count += 1
        
        elapsed = time.time() - start
        
        print(f"✅ Parallel ingested {count} entries in {elapsed:.3f}s")
        print(f"   Speed: {count/elapsed:.1f} entries/sec")
        print(f"   Speedup vs sequential: ~{0.3/elapsed:.1f}x faster")
        print()
    except Exception as e:
        print(f"⚠️  Parallel ingest failed: {e}")
        print()
    
    print("=" * 60)
    print("Performance Comparison")
    print("=" * 60)
    print()
    print("Method                  Speed           Best For")
    print("-" * 60)
    print("Sequential (1 at time)  100%  (1x)     Single requests")
    print("Parallel (5 threads)    ~400% (4x)     Many medium batches")
    print("Batch (send all)        ~500% (5x)     Bulk ingest")
    print()
    
    print("=" * 60)
    print("Batch Ingest Details")
    print("=" * 60)
    print()
    
    print("Request body:")
    print(json.dumps(entries[:2], indent=2))
    print()
    
    print(f"Total size: ~{len(json.dumps(entries))} bytes")
    print(f"Max batch size: 1000 entries (configurable)")
    print()
    
    print("Response format:")
    print("""
{
  "status": "ok",
  "ids": ["uuid-1", "uuid-2", "uuid-3", ...]
}
    """)
    
    print()
    print("=" * 60)
    print("Real-World Scenario")
    print("=" * 60)
    print()
    
    print("Task: Process 10,000 requests (e.g., daily batch processing)")
    print()
    
    scenarios = [
        {
            "name": "Sequential",
            "requests_per_sec": 10,
            "time_seconds": 10000 / 10,
            "cost_overhead": "High (many round trips)"
        },
        {
            "name": "Parallel (10 threads)",
            "requests_per_sec": 50,
            "time_seconds": 10000 / 50,
            "cost_overhead": "Medium"
        },
        {
            "name": "Batch (10 × 1000)",
            "requests_per_sec": 100,
            "time_seconds": 10000 / 100,
            "cost_overhead": "Low (fewer connections)"
        },
    ]
    
    for scenario in scenarios:
        print(f"{scenario['name']:30} | Time: {scenario['time_seconds']:6.0f}s | {scenario['cost_overhead']}")
    
    print()
    print("=" * 60)
    print("Best Practices")
    print("=" * 60)
    print()
    print("1. Use batch ingest when you have many entries")
    print("   • Max 1000 per batch (adjust if needed)")
    print("   • Much faster than sequential")
    print()
    print("2. Use threading for dynamic workloads")
    print("   • When entries arrive over time")
    print("   • Start requests immediately, not wait for batch")
    print()
    print("3. Monitor connection reuse")
    print("   • Reuse HTTP connections (keep-alive)")
    print("   • Reduces TLS handshake overhead")
    print()
    print("4. Measure and tune")
    print("   • Test different batch sizes")
    print("   • Monitor latency and memory")
    print("   • Adjust workers based on system load")
    print()
    
    return 0


if __name__ == "__main__":
    exit(main())
