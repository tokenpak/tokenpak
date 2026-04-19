#!/usr/bin/env python3
"""
OpenAI SDK Integration

What this example shows:
- Using TokenPak as a drop-in replacement for OpenAI SDK
- Redirecting requests to the TokenPak proxy
- Compatibility with existing OpenAI client code
- Minimal code changes needed

When to use this:
- Migrating existing OpenAI code to TokenPak
- Using OpenAI SDK without changing your code
- Testing OpenAI compatibility
"""

import os
from datetime import datetime, timezone
import json
import urllib.request


def main():
    """Demonstrate OpenAI SDK integration with TokenPak."""
    
    proxy_url = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    
    if not api_key:
        api_key = "sk-test"  # For demo purposes
    
    print("=" * 60)
    print("OPENAI SDK INTEGRATION")
    print("=" * 60)
    print()
    
    print("TokenPak exposes an OpenAI-compatible API endpoint:")
    print(f"  Proxy URL: {proxy_url}")
    print(f"  Endpoint:  {proxy_url}/v1/chat/completions")
    print()
    
    print("Example 1: Using OpenAI Python Library")
    print("-" * 60)
    print()
    
    print("Original code (OpenAI API):")
    print("""
from openai import OpenAI

client = OpenAI(
    api_key="sk-..."  # Your Anthropic key
)

response = client.chat.completions.create(
    model="claude-sonnet-4-6",
    messages=[
        {"role": "user", "content": "What is 2+2?"}
    ]
)

print(response.choices[0].message.content)
    """)
    
    print()
    print("TokenPak integration (just one change!):")
    print("""
from openai import OpenAI

client = OpenAI(
    api_key="sk-...",
    base_url="http://localhost:8766/v1"  # Add this line
)

# Everything else stays the same!
response = client.chat.completions.create(
    model="claude-sonnet-4-6",
    messages=[
        {"role": "user", "content": "What is 2+2?"}
    ]
)

print(response.choices[0].message.content)
    """)
    
    print()
    print("=" * 60)
    print("Example 2: HTTP Request (Raw API)")
    print("-" * 60)
    print()
    
    # Simulate a chat completion request
    chat_request = {
        "model": "claude-sonnet-4-6",
        "messages": [
            {
                "role": "user",
                "content": "What is the capital of France?"
            }
        ],
        "max_tokens": 100,
    }
    
    print("Request to /v1/chat/completions:")
    print(json.dumps(chat_request, indent=2))
    print()
    
    # In a real scenario, you'd call the /v1/chat/completions endpoint
    # For this demo, we'll show the expected response format
    mock_response = {
        "id": "chatcmpl-9a8b7c6d",
        "object": "chat.completion",
        "created": 1710712345,
        "model": "claude-sonnet-4-6",
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": "The capital of France is Paris."
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {
            "prompt_tokens": 20,
            "completion_tokens": 10,
            "total_tokens": 30,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    }
    
    print("Expected response:")
    print(json.dumps(mock_response, indent=2))
    print()
    
    # Now ingest this usage
    print("=" * 60)
    print("Usage Tracking")
    print("=" * 60)
    print()
    
    usage = mock_response["usage"]
    total_cost = (
        (usage["prompt_tokens"] / 1000) * 0.003 +
        (usage["completion_tokens"] / 1000) * 0.015
    )
    
    print(f"Tokens used:")
    print(f"  Prompt:     {usage['prompt_tokens']:>3,} tokens @ $0.003/1K")
    print(f"  Completion: {usage['completion_tokens']:>3,} tokens @ $0.015/1K")
    print(f"  Total:      {usage['total_tokens']:>3,} tokens")
    print()
    print(f"Cost: ${total_cost:.4f}")
    print()
    
    # Ingest the usage
    entry = {
        "model": "claude-sonnet-4-6",
        "tokens": usage["total_tokens"],
        "cost": total_cost,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "openai-integration-demo",
        "provider": "anthropic",
        "extra": {
            "prompt_tokens": usage["prompt_tokens"],
            "completion_tokens": usage["completion_tokens"],
            "cache_tokens": usage.get("cache_read_input_tokens", 0),
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
            print(f"✅ Usage tracked")
            print(f"   Entry ID: {data['ids'][0]}")
            print()
    except Exception as e:
        print(f"⚠️  Could not ingest (server may not be running): {e}")
        print()
    
    print("=" * 60)
    print("Compatibility Matrix")
    print("=" * 60)
    print()
    print("✅ Supported:")
    print("  • Chat completions (streaming & non-streaming)")
    print("  • Custom system prompts")
    print("  • Tool/function calls")
    print("  • Temperature, max_tokens, top_p settings")
    print("  • Message history")
    print()
    print("⚠️  Partial:")
    print("  • Embeddings (use Anthropic API directly)")
    print("  • Image input (use supported models)")
    print()
    print("❌ Not supported:")
    print("  • Fine-tuning endpoints")
    print("  • Organization ID")
    print("  • Usage-based billing (use TokenPak tracking)")
    print()
    
    print("=" * 60)
    print("Migration Checklist")
    print("=" * 60)
    print()
    print("□ Change base_url to TokenPak proxy")
    print("□ Test with existing code (minimal changes needed)")
    print("□ Update model names if using OpenAI models")
    print("□ Monitor usage through TokenPak dashboard")
    print("□ Adjust timeout if requests are slow")
    print()
    
    return 0


if __name__ == "__main__":
    exit(main())
