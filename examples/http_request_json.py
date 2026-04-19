#!/usr/bin/env python3
"""
HTTP Request with JSON

What this example shows:
- Making raw HTTP POST requests to TokenPak endpoints
- Sending JSON payloads directly (curl-style)
- Parsing JSON responses
- Error handling (400, 422, 500 errors)

When to use this:
- Testing endpoints directly (no Python library needed)
- Integrating with non-Python systems
- Debugging proxy issues
- Understanding the HTTP API
"""

import json
import os
import urllib.request
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError


def main():
    """Make raw HTTP requests to TokenPak endpoints."""
    
    proxy_url = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")
    
    print("=" * 60)
    print("HTTP REQUEST WITH JSON")
    print("=" * 60)
    print()
    
    # Test 1: Health check
    print("[1] Health Check")
    print("-" * 60)
    
    try:
        with urllib.request.urlopen(f"{proxy_url}/health", timeout=5) as resp:
            data = json.loads(resp.read())
            print(f"✅ Status: {resp.status}")
            print(f"   Response: {json.dumps(data, indent=2)}")
    except URLError as e:
        print(f"❌ Connection error: {e}")
        print(f"   Make sure TokenPak is running at {proxy_url}")
        return 1
    
    print()
    
    # Test 2: Single ingest request
    print("[2] Single Entry Ingest")
    print("-" * 60)
    
    payload = {
        "model": "claude-sonnet-4-6",
        "tokens": 1024,
        "cost": 0.05,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent": "http-example",
        "provider": "anthropic",
    }
    
    print(f"Request body:")
    print(json.dumps(payload, indent=2))
    print()
    
    try:
        req = urllib.request.Request(
            f"{proxy_url}/ingest",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            print(f"✅ Status: {resp.status}")
            print(f"   Response:")
            print(json.dumps(data, indent=2))
    except HTTPError as e:
        print(f"❌ HTTP {e.code}: {e.reason}")
        print(f"   Response: {e.read().decode()}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print()
    
    # Test 3: Batch ingest
    print("[3] Batch Ingest (Multiple Entries)")
    print("-" * 60)
    
    batch_payload = [
        {
            "model": "claude-haiku-4-5",
            "tokens": 256,
            "cost": 0.01,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "http-example",
        },
        {
            "model": "claude-opus-4-5",
            "tokens": 2048,
            "cost": 0.15,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "agent": "http-example",
        },
    ]
    
    print(f"Request body (JSON array):")
    print(json.dumps(batch_payload, indent=2))
    print()
    
    try:
        req = urllib.request.Request(
            f"{proxy_url}/ingest/batch",
            data=json.dumps(batch_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            print(f"✅ Status: {resp.status}")
            print(f"   Response:")
            print(json.dumps(data, indent=2))
    except HTTPError as e:
        print(f"❌ HTTP {e.code}: {e.reason}")
        print(f"   Response: {e.read().decode()}")
    except Exception as e:
        print(f"❌ Error: {e}")
    
    print()
    
    # Test 4: Invalid request (error handling)
    print("[4] Invalid Request (Error Handling)")
    print("-" * 60)
    
    invalid_payload = {
        "model": "claude-sonnet-4-6",
        "tokens": -100,  # Invalid: negative tokens
        "cost": 0.05,
    }
    
    print(f"Request body (invalid):")
    print(json.dumps(invalid_payload, indent=2))
    print()
    
    try:
        req = urllib.request.Request(
            f"{proxy_url}/ingest",
            data=json.dumps(invalid_payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            print(f"✅ Status: {resp.status}")
            print(f"   Response: {json.dumps(data, indent=2)}")
    except HTTPError as e:
        print(f"✅ Expected error received:")
        print(f"   HTTP {e.code}: {e.reason}")
        error_detail = json.loads(e.read().decode())
        print(f"   Detail: {json.dumps(error_detail, indent=2)}")
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
    
    print()
    print("=" * 60)
    print("HTTP API Reference")
    print("=" * 60)
    print()
    print("Endpoints:")
    print("  GET  /health           - Health check")
    print("  POST /ingest           - Ingest single entry")
    print("  POST /ingest/batch     - Ingest multiple entries")
    print()
    print("Content-Type: application/json")
    print()
    print("Request body (single):")
    print("  {")
    print('    "model": "string",              # Required')
    print('    "tokens": number,               # Required (>= 0)')
    print('    "cost": number,                 # Required (>= 0.0)')
    print('    "timestamp": "ISO-8601",        # Optional (defaults to now)')
    print('    "agent": "string",              # Optional')
    print('    "provider": "string",           # Optional')
    print('    "session_id": "string"          # Optional')
    print("  }")
    print()
    print("Response (success 200):")
    print("  {")
    print('    "status": "ok",')
    print('    "ids": ["uuid", "uuid", ...]')
    print("  }")
    print()
    
    return 0


if __name__ == "__main__":
    exit(main())
