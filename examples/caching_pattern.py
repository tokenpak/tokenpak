#!/usr/bin/env python3
"""
Caching Pattern: Hit/Miss Tracking

What this example shows:
- How TokenPak detects cache hits vs misses
- Tracking cache_read_input_tokens in responses
- Cost savings from cache hits (90% reduction)
- Monitoring cache behavior

When to use this:
- Understanding cache behavior
- Optimizing for cache hits
- Estimating savings from repeated context
"""

import json
import os
from datetime import datetime, timezone
import urllib.request


def main():
    """Demonstrate cache hit and miss tracking."""
    
    proxy_url = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")
    
    print("=" * 60)
    print("CACHING PATTERN: HIT/MISS TRACKING")
    print("=" * 60)
    print()
    
    print("Cache Behavior:")
    print("-" * 60)
    print()
    print("When the same context (system prompt, document, conversation) is")
    print("used in multiple requests, TokenPak can detect cache hits.")
    print()
    print("✅ First request: cache miss")
    print("   - All tokens counted as prompt_tokens")
    print("   - cache_read_input_tokens = 0")
    print()
    print("✅ Second request (same context): cache hit")
    print("   - Context tokens read from cache")
    print("   - cache_read_input_tokens > 0")
    print("   - Cost: 90% less than full tokens ($0.0003 vs $0.003 per 1K)")
    print()
    print("=" * 60)
    print()
    
    # Simulate a sequence of requests
    requests = [
        {
            "request_num": 1,
            "description": "Initial request with system prompt + document",
            "context": "You are a code reviewer.\n\nDocument: [10KB code snippet]",
            "prompt_tokens": 2048,
            "cache_read_tokens": 0,
            "output_tokens": 256,
        },
        {
            "request_num": 2,
            "description": "Same context, different question",
            "context": "You are a code reviewer.\n\nDocument: [10KB code snippet]",
            "prompt_tokens": 100,  # New question only
            "cache_read_tokens": 2048,  # Context from cache!
            "output_tokens": 256,
        },
        {
            "request_num": 3,
            "description": "Another question, same document",
            "context": "You are a code reviewer.\n\nDocument: [10KB code snippet]",
            "prompt_tokens": 85,  # Different question
            "cache_read_tokens": 2048,  # Still cached
            "output_tokens": 200,
        },
    ]
    
    # Pricing (Anthropic Claude models)
    CACHE_MISS_RATE = 0.003  # $0.003 per 1K input tokens
    CACHE_HIT_RATE = 0.0003  # $0.0003 per 1K cache read tokens (90% savings)
    OUTPUT_RATE = 0.015      # $0.015 per 1K output tokens
    
    total_cost_no_cache = 0.0
    total_cost_with_cache = 0.0
    
    print("[Simulation] Request Sequence")
    print("-" * 60)
    print()
    
    for req in requests:
        prompt = req["prompt_tokens"]
        cache_read = req["cache_read_tokens"]
        output = req["output_tokens"]
        
        # Cost calculation
        cost_no_cache = ((prompt + cache_read) / 1000) * CACHE_MISS_RATE + (output / 1000) * OUTPUT_RATE
        cost_with_cache = (prompt / 1000) * CACHE_MISS_RATE + (cache_read / 1000) * CACHE_HIT_RATE + (output / 1000) * OUTPUT_RATE
        
        total_cost_no_cache += cost_no_cache
        total_cost_with_cache += cost_with_cache
        
        print(f"Request #{req['request_num']}: {req['description']}")
        print(f"  Context: {req['context'][:60]}...")
        print()
        print(f"  Tokens:")
        print(f"    • New input: {prompt:,} tokens @ ${CACHE_MISS_RATE:.4f}/1K = ${prompt/1000*CACHE_MISS_RATE:.4f}")
        print(f"    • Cache read: {cache_read:,} tokens @ ${CACHE_HIT_RATE:.4f}/1K = ${cache_read/1000*CACHE_HIT_RATE:.4f}")
        print(f"    • Output: {output:,} tokens @ ${OUTPUT_RATE:.4f}/1K = ${output/1000*OUTPUT_RATE:.4f}")
        print()
        print(f"  Cost per request:")
        print(f"    • Without cache: ${cost_no_cache:.4f}")
        print(f"    • With cache:    ${cost_with_cache:.4f}")
        print(f"    • Savings:       ${cost_no_cache - cost_with_cache:.4f} ({100*(cost_no_cache - cost_with_cache)/cost_no_cache:.1f}%)")
        print()
    
    print("=" * 60)
    print("[Summary] Total Cost Across Requests")
    print("=" * 60)
    print()
    print(f"Without caching: ${total_cost_no_cache:.4f}")
    print(f"With caching:    ${total_cost_with_cache:.4f}")
    print(f"Total savings:   ${total_cost_no_cache - total_cost_with_cache:.4f}")
    print(f"Savings rate:    {100*(total_cost_no_cache - total_cost_with_cache)/total_cost_no_cache:.1f}%")
    print()
    
    # Now demonstrate actual ingest requests with cache tracking
    print("=" * 60)
    print("[Live Example] Simulated Ingest Requests")
    print("=" * 60)
    print()
    
    # Request 1: Cache miss
    print("[Request 1] Initial request with document context")
    payload1 = {
        "model": "claude-sonnet-4-6",
        "tokens": 2048,  # Context + question
        "cost": 0.1024,  # Calculated: 2048/1000 * 0.003 + 256/1000 * 0.015
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "cache-demo",
        "provider": "anthropic",
        "extra": {
            "cache_tokens": 0,
            "description": "Cache miss - full document loaded"
        }
    }
    
    try:
        req = urllib.request.Request(
            f"{proxy_url}/ingest",
            data=json.dumps(payload1).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            print(f"✅ Entry ID: {data['ids'][0]}")
            print(f"   Total tokens: {payload1['tokens']:,}")
            print(f"   Cost: ${payload1['cost']:.4f}")
            print(f"   Cache hit: No (cache_tokens = {payload1['extra']['cache_tokens']})")
            print()
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1
    
    # Request 2: Cache hit
    print("[Request 2] Follow-up request with same document")
    payload2 = {
        "model": "claude-sonnet-4-6",
        "tokens": 356,  # New question + output (document from cache)
        "cost": 0.0130,  # Reduced cost: cache hit!
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "cache-demo",
        "provider": "anthropic",
        "extra": {
            "cache_tokens": 2048,  # Reused from cache!
            "description": "Cache hit - document reused"
        }
    }
    
    try:
        req = urllib.request.Request(
            f"{proxy_url}/ingest",
            data=json.dumps(payload2).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            print(f"✅ Entry ID: {data['ids'][0]}")
            print(f"   Total tokens: {payload2['tokens']:,} (new) + {payload2['extra']['cache_tokens']:,} (cached)")
            print(f"   Cost: ${payload2['cost']:.4f}")
            print(f"   Cache hit: Yes (cache_tokens = {payload2['extra']['cache_tokens']:,})")
            print()
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1
    
    print("=" * 60)
    print("Key Takeaways")
    print("=" * 60)
    print()
    print("1. Cache hits reduce cost by ~90% on cached tokens")
    print("2. Reusing system prompts, documents, or conversations triggers cache")
    print("3. Cache read tokens are much cheaper than regular input tokens")
    print("4. Monitor cache_read_input_tokens in responses to track savings")
    print("5. Structure requests to maximize cache hits (fixed context + variable questions)")
    print()
    
    return 0


if __name__ == "__main__":
    exit(main())
