#!/usr/bin/env python3
"""
quickstart-test.py — TokenPak 5-Minute Quickstart Test (cross-platform Python)

Sends 3 identical requests through the proxy and prints cache hit stats.
Works with Anthropic OR OpenAI — auto-detects which key is set.

Usage:
    python3 scripts/quickstart-test.py
    TOKENPAK_PORT=9000 python3 scripts/quickstart-test.py
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path


# Color codes
class Color:
    RED = '\033[0;31m'
    GREEN = '\033[0;32m'
    YELLOW = '\033[1;33m'
    BLUE = '\033[0;34m'
    NC = '\033[0m'  # No Color


def section(title):
    """Print a section header."""
    print(f"\n{Color.BLUE}▸ {title}{Color.NC}")


def check_api_key():
    """Detect API key and return (provider, api_key, model)."""
    if os.environ.get('ANTHROPIC_API_KEY'):
        return 'anthropic', os.environ['ANTHROPIC_API_KEY'], 'claude-opus-4-5'
    elif os.environ.get('OPENAI_API_KEY'):
        return 'openai', os.environ['OPENAI_API_KEY'], 'gpt-4o'
    else:
        print(f"{Color.RED}❌ Error: No API key found{Color.NC}\n")
        print("Please set one of:")
        print("  export ANTHROPIC_API_KEY='sk-ant-...' (or add to .env)")
        print("  export OPENAI_API_KEY='sk-...' (or add to .env)")
        print("\n📖 Setup guide: https://docs.tokenpak.dev/getting-started")
        sys.exit(1)


def check_proxy(proxy_url):
    """Check if proxy is running."""
    try:
        req = urllib.request.Request(
            f"{proxy_url}/health",
            method='GET'
        )
        with urllib.request.urlopen(req, timeout=2):
            pass
    except Exception:
        print(f"{Color.RED}❌ Proxy not running{Color.NC}\n")
        print("Start TokenPak:")
        print("  tokenpak start\n")
        print("Or with Docker:")
        print("  docker compose up -d\n")
        print("📖 Troubleshooting: https://docs.tokenpak.dev/troubleshooting")
        sys.exit(1)


def make_request(proxy_url, provider, api_key, model):
    """Make a request to the proxy and return the JSON response."""
    request_payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": "In exactly 10 words, summarize the purpose of a proxy server in software engineering. Just the 10 words, nothing else."
            }
        ]
    }
    
    req = urllib.request.Request(
        f"{proxy_url}/v1/messages",
        data=json.dumps(request_payload).encode('utf-8'),
        headers={
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key}',
            'X-Tokenpak-Provider': provider
        },
        method='POST'
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode('utf-8')
        try:
            error_data = json.loads(error_body)
            error_msg = error_data.get('error', {}).get('message', str(e))
        except:
            error_msg = error_body
        
        print(f"{Color.RED}❌ API Error: {error_msg}{Color.NC}")
        sys.exit(1)
    except Exception as e:
        print(f"{Color.RED}❌ Network Error: {e}{Color.NC}")
        print("\n📖 Troubleshooting: https://docs.tokenpak.dev/troubleshooting")
        sys.exit(1)


def extract_token_counts(response):
    """Extract cache_read_input_tokens and input_tokens from response."""
    usage = response.get('usage', {})
    cache_read = usage.get('cache_read_input_tokens', 0)
    input_tokens = usage.get('input_tokens', 0)
    return cache_read, input_tokens


def main():
    # Configuration
    proxy_host = os.environ.get('TOKENPAK_HOST', 'localhost')
    proxy_port = os.environ.get('TOKENPAK_PORT', '8766')
    proxy_url = f"http://{proxy_host}:{proxy_port}"
    
    # Detect provider and API key
    provider, api_key, model = check_api_key()
    
    print(f"{Color.GREEN}╭─ TokenPak Quickstart Test{Color.NC}")
    print(f"{Color.GREEN}╰─ Testing cache hits and cost savings{Color.NC}")
    
    # Check proxy is running
    section("Checking proxy...")
    check_proxy(proxy_url)
    print(f"{Color.GREEN}✓ Proxy is running{Color.NC}")
    
    # Make 3 identical requests: miss → hit → hit
    section("Sending 3 identical requests...")
    
    print("  Request 1 (cache miss)...", end='', file=sys.stderr, flush=True)
    response_1 = make_request(proxy_url, provider, api_key, model)
    cache_hit_1, input_tokens_1 = extract_token_counts(response_1)
    print(" ✓", file=sys.stderr)
    
    time.sleep(0.5)
    
    print("  Request 2 (cache hit)...", end='', file=sys.stderr, flush=True)
    response_2 = make_request(proxy_url, provider, api_key, model)
    cache_hit_2, input_tokens_2 = extract_token_counts(response_2)
    print(" ✓", file=sys.stderr)
    
    time.sleep(0.5)
    
    print("  Request 3 (cache hit)...", end='', file=sys.stderr, flush=True)
    response_3 = make_request(proxy_url, provider, api_key, model)
    cache_hit_3, input_tokens_3 = extract_token_counts(response_3)
    print(" ✓", file=sys.stderr)
    
    # Calculate totals
    total_cache_hit = cache_hit_1 + cache_hit_2 + cache_hit_3
    total_input_tokens = input_tokens_1 + input_tokens_2 + input_tokens_3
    
    # Calculate savings
    if total_input_tokens > 0:
        cache_hit_count = sum([1 for hit in [cache_hit_2, cache_hit_3] if hit > 0])
        savings_percent = (total_cache_hit * 100) // total_input_tokens
    else:
        cache_hit_count = 0
        savings_percent = 0
    
    # Estimate cost savings
    cache_write_cost = (cache_hit_1 * 125) // 100  # First request writes cache (25% extra)
    cache_read_cost = (total_cache_hit * 10) // 100  # Other requests read from cache (90% cheaper)
    regular_cost = total_input_tokens - total_cache_hit
    actual_cost = cache_write_cost + cache_read_cost + regular_cost
    baseline_cost = total_input_tokens
    cost_saved = baseline_cost - actual_cost
    
    # ========================================================================
    # Summary
    # ========================================================================
    
    section("Cache Hit Summary")
    print(f"  Requests sent:      {Color.GREEN}3{Color.NC}")
    print(f"  Cache hits:         {Color.GREEN}{cache_hit_count} / 3{Color.NC}")
    print(f"  Cached tokens:      {Color.GREEN}{total_cache_hit}{Color.NC}")
    print(f"  Total input tokens: {Color.YELLOW}{total_input_tokens}{Color.NC}")
    print(f"  Tokens saved:       {Color.GREEN}{total_cache_hit}{Color.NC}")
    print(f"  Savings:            {Color.GREEN}{savings_percent}%{Color.NC}")
    
    if cost_saved > 0:
        print(f"  Cost reduction:     {Color.GREEN}~{cost_saved} token units{Color.NC}")
    
    section("What's Next?")
    print("  🔍 Check proxy status:")
    print("     tokenpak status\n")
    print("  📊 View detailed savings:")
    print("     tokenpak savings\n")
    print("  🗂️  List cached models:")
    print("     tokenpak models\n")
    print("  📈 Open dashboard:")
    print("     tokenpak dashboard  (or visit http://localhost:8766/dashboard)\n")
    print("  🛑 Stop proxy when done:")
    print("     tokenpak stop\n")
    
    if cache_hit_count == 0:
        print(f"{Color.YELLOW}⚠️  Cache hits not detected{Color.NC} — this may be expected on first run.")
        print("    Try running the test again to see cache hits from previous requests.")
    else:
        print(f"{Color.GREEN}✅ Cache is working! Try running again to increase savings.{Color.NC}")
    
    print()
    print(f"{Color.BLUE}📖 Learn more: https://docs.tokenpak.dev{Color.NC}")
    print(f"{Color.BLUE}💬 Issues & feedback: https://github.com/repliflow/tokenpak/issues{Color.NC}")
    print()


if __name__ == '__main__':
    main()
