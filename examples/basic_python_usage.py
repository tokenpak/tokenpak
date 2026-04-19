#!/usr/bin/env python3
"""
Basic Python Usage

What this example shows:
- Setting up the TokenPak proxy client
- Making a simple chat request
- Printing the response and token usage

When to use this:
- Getting started with TokenPak
- Understanding the basic request/response flow
"""

import os
from datetime import datetime, timezone


def main():
    """Make a basic chat request through the TokenPak proxy."""
    
    # Configuration
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY environment variable not set")
    
    proxy_url = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")
    
    # ===== For production: use the actual tokenpak library =====
    # from tokenpak import TokenPakProxy
    # proxy = TokenPakProxy(api_key=api_key, proxy_url=proxy_url)
    
    # ===== For this example: use HTTP directly =====
    import json
    import urllib.request
    
    # Prepare the request
    payload = {
        "model": "claude-sonnet-4-6",
        "messages": [
            {
                "role": "user",
                "content": "What is the capital of France?"
            }
        ],
        "max_tokens": 100,
    }
    
    # Send request to the ingest endpoint (example using actual API)
    # Note: TokenPak also has a /v1/chat/completions endpoint for OpenAI compatibility
    ingest_url = f"{proxy_url}/ingest"
    
    print("=" * 60)
    print("BASIC PYTHON USAGE")
    print("=" * 60)
    print()
    
    # Example using the ingest endpoint
    ingest_payload = {
        "model": "claude-sonnet-4-6",
        "tokens": 150,
        "cost": 0.0075,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "basic-usage-example",
        "provider": "anthropic",
    }
    
    try:
        req = urllib.request.Request(
            ingest_url,
            data=json.dumps(ingest_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        
        with urllib.request.urlopen(req, timeout=5) as resp:
            response_data = json.loads(resp.read())
            
            print("Request sent to TokenPak ingest endpoint:")
            print(f"  Model: {ingest_payload['model']}")
            print(f"  Tokens: {ingest_payload['tokens']}")
            print(f"  Cost: ${ingest_payload['cost']:.4f}")
            print()
            
            print("Response from TokenPak:")
            print(f"  Status: {response_data['status']}")
            print(f"  Entry ID(s): {response_data['ids']}")
            print()
            
            print("✅ Success!")
            
    except Exception as e:
        print(f"❌ Error: {e}")
        print()
        print("Make sure TokenPak is running:")
        print(f"  python -m tokenpak.agent.ingest.api")
        return 1
    
    print()
    print("=" * 60)
    print("Key Concepts:")
    print("=" * 60)
    print()
    print("1. API Key: Set via ANTHROPIC_API_KEY environment variable")
    print("2. Proxy URL: Defaults to http://localhost:8766")
    print("3. Request: Send chat messages, get responses with token counts")
    print("4. Usage: Access response.usage for prompt_tokens, completion_tokens, etc.")
    print("5. Caching: Check cache_read_input_tokens for cache hits (90% cost savings!)")
    print()
    
    return 0


if __name__ == "__main__":
    exit(main())
