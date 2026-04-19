#!/usr/bin/env python3
"""
Cost Tracking

What this example shows:
- Calculating cost from token usage
- Tracking tokens saved through caching
- Monitoring cumulative spend
- Building a simple usage dashboard

When to use this:
- Monitoring API costs
- Tracking savings from caching
- Setting up cost alerts
"""

import json
import os
from datetime import datetime, timezone
import urllib.request


def main():
    """Track and display token costs."""
    
    proxy_url = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")
    
    print("=" * 60)
    print("COST TRACKING")
    print("=" * 60)
    print()
    
    print("Pricing (Claude Sonnet 4.6)")
    print("-" * 60)
    print("  Input tokens:          $0.003 per 1K tokens")
    print("  Output tokens:         $0.015 per 1K tokens")
    print("  Cache read tokens:     $0.0003 per 1K tokens (90% savings!)")
    print("  Cache creation tokens: $0.003 per 1K tokens (same as input)")
    print()
    
    # Track multiple requests
    requests = [
        {
            "name": "Document Analysis",
            "input": 3000,
            "output": 500,
            "cache_read": 0,
            "cache_write": 0,
        },
        {
            "name": "Follow-up Question",
            "input": 100,
            "output": 300,
            "cache_read": 3000,  # Document cached
            "cache_write": 0,
        },
        {
            "name": "Another Question",
            "input": 150,
            "output": 250,
            "cache_read": 3000,  # Document still cached
            "cache_write": 0,
        },
        {
            "name": "New Document",
            "input": 5000,
            "output": 600,
            "cache_read": 0,
            "cache_write": 5000,  # Cache this document
        },
        {
            "name": "Question on New Doc",
            "input": 120,
            "output": 400,
            "cache_read": 5000,  # Reuse new document
            "cache_write": 0,
        },
    ]
    
    # Pricing constants
    PRICE_INPUT = 0.003 / 1000       # per token
    PRICE_OUTPUT = 0.015 / 1000      # per token
    PRICE_CACHE_READ = 0.0003 / 1000 # per token
    PRICE_CACHE_WRITE = 0.003 / 1000 # per token
    
    print("=" * 60)
    print("[Live Example] Request Tracking")
    print("=" * 60)
    print()
    
    total_input = 0
    total_output = 0
    total_cache_read = 0
    total_cache_write = 0
    total_cost = 0.0
    total_cost_without_cache = 0.0
    
    for i, req in enumerate(requests, 1):
        input_tokens = req["input"]
        output_tokens = req["output"]
        cache_read_tokens = req["cache_read"]
        cache_write_tokens = req["cache_write"]
        
        # Calculate costs
        cost_input = input_tokens * PRICE_INPUT
        cost_output = output_tokens * PRICE_OUTPUT
        cost_cache_read = cache_read_tokens * PRICE_CACHE_READ
        cost_cache_write = cache_write_tokens * PRICE_CACHE_WRITE
        
        cost_with_cache = cost_input + cost_output + cost_cache_read + cost_cache_write
        cost_without_cache = (input_tokens + cache_read_tokens + cache_write_tokens) * PRICE_INPUT + output_tokens * PRICE_OUTPUT
        
        total_cost += cost_with_cache
        total_cost_without_cache += cost_without_cache
        total_input += input_tokens
        total_output += output_tokens
        total_cache_read += cache_read_tokens
        total_cache_write += cache_write_tokens
        
        print(f"[{i}] {req['name']}")
        print(f"    Input:          {input_tokens:>5,} tokens  @ ${PRICE_INPUT*1000:.4f}/1K = ${cost_input:.4f}")
        if output_tokens > 0:
            print(f"    Output:         {output_tokens:>5,} tokens  @ ${PRICE_OUTPUT*1000:.4f}/1K = ${cost_output:.4f}")
        if cache_read_tokens > 0:
            print(f"    Cache read:     {cache_read_tokens:>5,} tokens  @ ${PRICE_CACHE_READ*1000:.4f}/1K = ${cost_cache_read:.4f}")
        if cache_write_tokens > 0:
            print(f"    Cache write:    {cache_write_tokens:>5,} tokens  @ ${PRICE_CACHE_WRITE*1000:.4f}/1K = ${cost_cache_write:.4f}")
        print(f"    Cost:           ${cost_with_cache:.4f}")
        if cache_read_tokens > 0 or cache_write_tokens > 0:
            savings = cost_without_cache - cost_with_cache
            print(f"    Savings:        ${savings:.4f} ({100*savings/cost_without_cache:.1f}%)")
        print()
    
    print("=" * 60)
    print("[Summary] Total Usage & Cost")
    print("=" * 60)
    print()
    print(f"Total tokens used:")
    print(f"  Input:         {total_input:>6,} tokens")
    print(f"  Output:        {total_output:>6,} tokens")
    print(f"  Cache read:    {total_cache_read:>6,} tokens (reused)")
    print(f"  Cache write:   {total_cache_write:>6,} tokens (stored)")
    print()
    print(f"Cost breakdown:")
    print(f"  Input cost:    ${total_input * PRICE_INPUT:>8.4f}")
    print(f"  Output cost:   ${total_output * PRICE_OUTPUT:>8.4f}")
    print(f"  Cache read:    ${total_cache_read * PRICE_CACHE_READ:>8.4f}")
    print(f"  Cache write:   ${total_cache_write * PRICE_CACHE_WRITE:>8.4f}")
    print(f"  {'─' * 30}")
    print(f"  Total cost:    ${total_cost:>8.4f}")
    print()
    print(f"Without caching:")
    print(f"  Total cost:    ${total_cost_without_cache:>8.4f}")
    print()
    print(f"Total savings:   ${total_cost_without_cache - total_cost:>8.4f}")
    print(f"Savings rate:    {100*(total_cost_without_cache - total_cost)/total_cost_without_cache:>7.1f}%")
    print()
    
    # Simulate ingesting these requests
    print("=" * 60)
    print("[Live] Simulating Ingest Requests")
    print("=" * 60)
    print()
    
    # Ingest the first request
    entry = {
        "model": "claude-sonnet-4-6",
        "tokens": requests[0]["input"] + requests[0]["output"],
        "cost": (
            requests[0]["input"] * PRICE_INPUT +
            requests[0]["output"] * PRICE_OUTPUT
        ),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "cost-tracking-demo",
        "provider": "anthropic",
        "extra": {
            "input_tokens": requests[0]["input"],
            "output_tokens": requests[0]["output"],
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
        }
    }
    
    try:
        req = urllib.request.Request(
            f"{proxy_url}/ingest",
            data=json.dumps(entry).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            print(f"✅ Ingested first request")
            print(f"   Entry ID: {data['ids'][0]}")
            print(f"   Cost tracked: ${entry['cost']:.4f}")
            print()
    except Exception as e:
        print(f"❌ Error: {e}")
        return 1
    
    print("=" * 60)
    print("Monitoring Tips")
    print("=" * 60)
    print()
    print("1. Track input, output, and cache tokens separately")
    print("2. Monitor cache hit rate (higher = better cost)")
    print("3. Set cost alerts if daily/monthly spend exceeds threshold")
    print("4. Review patterns to optimize cache usage")
    print("5. Use dashboard to visualize cost trends")
    print()
    
    return 0


if __name__ == "__main__":
    exit(main())
