#!/usr/bin/env python3
"""
Fallback Chain: Multi-Provider Failover

What this example shows:
- Configuring fallback chains for reliability
- Trying multiple providers if one fails
- Graceful degradation (cheap → expensive models)
- Cost optimization with failover

When to use this:
- Production systems requiring high reliability
- Optimizing costs with smart fallback
- Testing different models
"""

import json
import os
from datetime import datetime, timezone
import urllib.request
from urllib.error import HTTPError, URLError


def main():
    """Demonstrate fallback chain configuration."""
    
    proxy_url = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")
    
    print("=" * 60)
    print("FALLBACK CHAIN: MULTI-PROVIDER FAILOVER")
    print("=" * 60)
    print()
    
    print("Use case: Run requests through multiple providers, failing over gracefully")
    print()
    
    print("=" * 60)
    print("Example 1: Model Fallback (Cost Optimization)")
    print("=" * 60)
    print()
    
    print("Strategy: Try cheap model first, upgrade if needed")
    print()
    
    print("Code:")
    print("""
class ModelFallback:
    def __init__(self):
        self.chain = [
            {"model": "claude-haiku-4-5", "max_tokens": 500},      # Cheap, fast
            {"model": "claude-sonnet-4-6", "max_tokens": 1000},    # Balanced
            {"model": "claude-opus-4-5", "max_tokens": 2000},      # Expensive, smart
        ]
    
    async def query(self, prompt: str, required_tokens: int = None) -> dict:
        '''Try each model in sequence until one succeeds.'''
        
        for model_config in self.chain:
            try:
                response = await self.call_api(
                    model=model_config["model"],
                    prompt=prompt,
                    max_tokens=model_config["max_tokens"]
                )
                
                # Check if response is good enough
                if required_tokens and len(response) < required_tokens:
                    print(f"  {model_config['model']}: Response too short, trying next...")
                    continue
                
                print(f"✅ {model_config['model']}: Success")
                return response
                
            except Exception as e:
                print(f"⚠️  {model_config['model']}: {e}, trying next...")
                continue
        
        raise Exception("All models failed")

fallback = ModelFallback()

# Will try: Haiku → Sonnet → Opus
response = await fallback.query("Explain quantum computing")
    """)
    
    print()
    print("=" * 60)
    print("Example 2: Provider Fallback (Reliability)")
    print("=" * 60)
    print()
    
    print("Strategy: Try Anthropic, fall back to OpenAI if down")
    print()
    
    print("Code:")
    print("""
class ProviderFallback:
    def __init__(self):
        self.providers = [
            {
                "name": "Anthropic",
                "base_url": "http://localhost:8766/v1",
                "models": ["claude-sonnet-4-6"],
                "priority": 1  # Try first
            },
            {
                "name": "OpenAI",
                "base_url": "https://api.openai.com/v1",
                "models": ["gpt-4", "gpt-3.5-turbo"],
                "priority": 2  # Fallback
            },
            {
                "name": "Google",
                "base_url": "https://generativelanguage.googleapis.com/v1",
                "models": ["gemini-pro"],
                "priority": 3  # Last resort
            }
        ]
    
    def query(self, prompt: str) -> dict:
        '''Try providers in priority order.'''
        
        providers_sorted = sorted(self.providers, key=lambda x: x["priority"])
        
        for provider in providers_sorted:
            try:
                response = self.call_provider(
                    provider["name"],
                    provider["base_url"],
                    provider["models"][0],
                    prompt
                )
                print(f"✅ Used {provider['name']}")
                return response
            except URLError as e:
                print(f"⚠️  {provider['name']} unavailable: {e}")
                continue
            except Exception as e:
                print(f"⚠️  {provider['name']} error: {e}")
                continue
        
        raise Exception("All providers failed")

fallback = ProviderFallback()
response = fallback.query("What is TokenPak?")
    """)
    
    print()
    print("=" * 60)
    print("Example 3: Latency-Based Fallback")
    print("=" * 60)
    print()
    
    print("Strategy: Use fast model, upgrade if response quality is poor")
    print()
    
    models_latency = [
        {"model": "claude-haiku-4-5", "latency_ms": 200, "quality_score": 0.75},
        {"model": "claude-sonnet-4-6", "latency_ms": 400, "quality_score": 0.95},
        {"model": "claude-opus-4-5", "latency_ms": 800, "quality_score": 0.99},
    ]
    
    print("Model characteristics:")
    print("-" * 60)
    for m in models_latency:
        print(f"  {m['model']:25} | Latency: {m['latency_ms']:4}ms | Quality: {m['quality_score']:.0%}")
    print()
    
    print("Decision tree:")
    print("-" * 60)
    print("""
    Start with fastest (Haiku)
         ↓
    Quality ≥ 0.9?
    ├─ YES → Use Haiku (cost: $0.01)
    └─ NO → Try Sonnet
         ├─ Quality ≥ 0.95?
         │  ├─ YES → Use Sonnet (cost: $0.05)
         │  └─ NO → Use Opus (cost: $0.15)
    """)
    
    print()
    print("=" * 60)
    print("Example 4: Cost-Optimized Fallback with Caching")
    print("=" * 60)
    print()
    
    # Simulate request with fallback
    chain = [
        {"model": "haiku", "tokens": 150, "cost": 0.01, "quality": 0.70},
        {"model": "sonnet", "tokens": 200, "cost": 0.05, "quality": 0.95},
    ]
    
    print("Fallback sequence:")
    print("-" * 60)
    print()
    
    # Request 1
    print("[Request 1] Try Haiku first (low cost)")
    payload = {
        "model": "claude-haiku-4-5",
        "tokens": chain[0]["tokens"],
        "cost": chain[0]["cost"],
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "fallback-demo",
        "extra": {"quality": chain[0]["quality"]}
    }
    
    try:
        req = urllib.request.Request(
            f"{proxy_url}/ingest",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            print(f"✅ Haiku response recorded")
            print(f"   Cost: ${chain[0]['cost']:.4f}")
            print(f"   Quality: {chain[0]['quality']:.0%}")
            print()
    except Exception as e:
        print(f"❌ Haiku failed: {e}")
        print()
        print("[Request 1 Retry] Fall back to Sonnet")
    
    # Request 2
    print("[Request 2] Reuse context (cache hit!) with better model")
    payload2 = {
        "model": "claude-sonnet-4-6",
        "tokens": chain[1]["tokens"] - 50,  # Less due to cache
        "cost": chain[1]["cost"] * 0.75,    # Reduced due to cache
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "fallback-demo",
        "extra": {
            "quality": chain[1]["quality"],
            "cache_tokens": 150,
            "reason": "Fallback + cache hit"
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
            print(f"✅ Sonnet response recorded (with cache)")
            print(f"   Cost: ${payload2['cost']:.4f} (reduced by cache)")
            print(f"   Quality: {payload2['extra']['quality']:.0%}")
            print()
    except Exception as e:
        print(f"⚠️  Could not ingest: {e}")
        print()
    
    print("=" * 60)
    print("Fallback Chain Benefits")
    print("=" * 60)
    print()
    print("✅ Cost optimization")
    print("   • Start with cheapest option")
    print("   • Upgrade only when necessary")
    print("✅ Reliability")
    print("   • Graceful degradation if service fails")
    print("   • Automatic provider switching")
    print("✅ Performance")
    print("   • Fast responses from fast models")
    print("   • Complex queries get better models")
    print("✅ Monitoring")
    print("   • Track which fallback was used")
    print("   • Identify patterns in quality/cost")
    print()
    
    return 0


if __name__ == "__main__":
    exit(main())
