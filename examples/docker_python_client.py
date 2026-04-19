#!/usr/bin/env python3
"""
Docker + Python Client

What this example shows:
- Running TokenPak in a Docker container
- Connecting Python clients to containerized proxy
- Environment-based configuration
- Production deployment pattern

When to use this:
- Production deployments
- Containerized environments (K8s, Docker Compose)
- Microservices architectures
"""

import os
import json
from datetime import datetime, timezone
import urllib.request


def main():
    """Demonstrate Docker + Python integration."""
    
    print("=" * 60)
    print("DOCKER + PYTHON CLIENT")
    print("=" * 60)
    print()
    
    print("=" * 60)
    print("Step 1: Create a Dockerfile")
    print("=" * 60)
    print()
    
    print("Example Dockerfile:")
    print("""
FROM python:3.11-slim

WORKDIR /app

# Copy TokenPak source
COPY . /app

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \\
    CMD curl -f http://localhost:8766/health || exit 1

# Default port
ENV TOKENPAK_PORT=8766
EXPOSE $TOKENPAK_PORT

# Start the proxy
CMD ["python", "-m", "tokenpak.agent.ingest.api"]
    """)
    
    print()
    print("=" * 60)
    print("Step 2: Build and Run")
    print("=" * 60)
    print()
    
    print("Build the image:")
    print("  docker build -t tokenpak:latest .")
    print()
    
    print("Run the container:")
    print("  docker run \\")
    print("    -e ANTHROPIC_API_KEY=sk-... \\")
    print("    -p 8766:8766 \\")
    print("    tokenpak:latest")
    print()
    
    print("Or use docker-compose:")
    print("""
version: '3.8'
services:
  tokenpak:
    image: tokenpak:latest
    environment:
      ANTHROPIC_API_KEY: \${ANTHROPIC_API_KEY}
      TOKENPAK_PORT: 8766
      TOKENPAK_LOG_LEVEL: info
    ports:
      - "8766:8766"
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8766/health"]
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 10s
    restart: unless-stopped

  client:
    image: python:3.11
    depends_on:
      - tokenpak
    environment:
      TOKENPAK_PROXY_URL: http://tokenpak:8766
    volumes:
      - ./client.py:/app/client.py
    command: python /app/client.py
    """)
    
    print()
    print("=" * 60)
    print("Step 3: Connect from Python Client")
    print("=" * 60)
    print()
    
    print("Code (within container or external):")
    print("""
import os
import urllib.request
import json
from datetime import datetime, timezone

class TokenPakClient:
    def __init__(self, proxy_url=None):
        # Allow proxy URL to be overridden via environment variable
        self.proxy_url = proxy_url or os.environ.get(
            "TOKENPAK_PROXY_URL",
            "http://localhost:8766"  # Local default
        )
    
    def health_check(self) -> bool:
        '''Verify proxy is running.'''
        try:
            with urllib.request.urlopen(
                f"{self.proxy_url}/health",
                timeout=5
            ) as resp:
                return resp.status == 200
        except Exception:
            return False
    
    def ingest_entry(self, model: str, tokens: int, cost: float) -> str:
        '''Ingest a usage entry.'''
        payload = {
            "model": model,
            "tokens": tokens,
            "cost": cost,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        
        req = urllib.request.Request(
            f"{self.proxy_url}/ingest",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data["ids"][0]

# Usage
client = TokenPakClient()

if client.health_check():
    print("✅ TokenPak is healthy")
    entry_id = client.ingest_entry(
        model="claude-sonnet-4-6",
        tokens=1500,
        cost=0.045
    )
    print(f"✅ Ingested entry: {entry_id}")
else:
    print("❌ TokenPak is not responding")
    """)
    
    print()
    print("=" * 60)
    print("Step 4: Example Docker Compose Deployment")
    print("=" * 60)
    print()
    
    # Check if Docker and docker-compose are available
    proxy_url = os.environ.get("TOKENPAK_PROXY_URL", "http://localhost:8766")
    
    print(f"Current proxy URL: {proxy_url}")
    print()
    
    # Try to check health
    try:
        with urllib.request.urlopen(f"{proxy_url}/health", timeout=2) as resp:
            if resp.status == 200:
                print("✅ TokenPak is running (health check passed)")
                print()
                
                # Make a test request
                payload = {
                    "model": "claude-sonnet-4-6",
                    "tokens": 500,
                    "cost": 0.015,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "agent": "docker-example",
                }
                
                req = urllib.request.Request(
                    f"{proxy_url}/ingest",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read())
                    print("[Live] Successful ingest request from Docker example")
                    print(f"  Entry ID: {data['ids'][0]}")
                    print(f"  Status: {data['status']}")
                    print()
    except Exception as e:
        print(f"⚠️  Could not verify Docker proxy: {e}")
        print()
    
    print("=" * 60)
    print("Production Checklist")
    print("=" * 60)
    print()
    print("□ Use multi-stage Docker build (minimize image size)")
    print("□ Set health checks (automatic restart on failure)")
    print("□ Use environment variables (no hardcoded config)")
    print("□ Mount config file as volume (enable updates without rebuild)")
    print("□ Set resource limits (memory, CPU)")
    print("□ Use restart policy: unless-stopped")
    print("□ Collect logs to stdout/stderr (for docker logs)")
    print("□ Use .dockerignore (exclude unnecessary files)")
    print("□ Sign image (for registry security)")
    print("□ Scan for vulnerabilities (security scanning)")
    print()
    
    print("=" * 60)
    print("Network Architecture")
    print("=" * 60)
    print()
    print("""
    ┌─────────────────┐
    │  Your Python    │
    │  Application    │
    │                 │
    │ import client   │
    │ client.ingest() │
    └────────┬────────┘
             │ HTTP POST /ingest
             │
    ┌────────▼────────────┐
    │  TokenPak Container │
    │                     │
    │ Port 8766           │
    │ /health             │
    │ /ingest             │
    │ /ingest/batch       │
    └─────────────────────┘
    """)
    
    print()
    print("=" * 60)
    print("Environment Variables")
    print("=" * 60)
    print()
    print("Set these in your docker-compose.yml or docker run -e:")
    print()
    print("  ANTHROPIC_API_KEY      Your API key")
    print("  TOKENPAK_PORT          Port to listen on (default: 8766)")
    print("  TOKENPAK_LOG_LEVEL     Log level (debug/info/warning/error)")
    print("  TOKENPAK_PROXY_URL     Proxy URL for clients")
    print()
    
    return 0


if __name__ == "__main__":
    exit(main())
