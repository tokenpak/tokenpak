# Team Server Deployment Guide

Run a shared TokenPak proxy for your whole team.

The shared server is under active development. This guide documents the planned architecture and configuration. Some features may not yet be available in the current release.

---

## Overview

A TokenPak Team Server is a central proxy instance that:

- Routes all team member requests through a shared proxy
- Aggregates cost tracking across agents and users
- Enforces team-wide budget limits and routing policies
- Provides an admin dashboard with per-user and per-agent breakdowns

```
Agent A ─┐
Agent B ─┤→ TokenPak Team Server → Provider APIs
Agent C ─┘        ↓
                Admin Dashboard (web UI)
```

---

## Deployment Options

### Option 1: Bare Metal / VM

```bash
# Install on your server
pip install tokenpak

# Start in team server mode
tokenpak serve --team --port 8766 --host 0.0.0.0

# Enable admin dashboard
tokenpak config set dashboard.enabled true
tokenpak config set dashboard.admin_token "your-admin-secret"
```

### Option 2: Docker

```dockerfile
FROM python:3.11-slim
RUN pip install tokenpak
EXPOSE 8766
CMD ["tokenpak", "serve", "--team", "--host", "0.0.0.0", "--port", "8766"]
```

```bash
docker run -d \
  -p 8766:8766 \
  -v ~/.tokenpak:/root/.tokenpak \
  -e TOKENPAK_MODE=hybrid \
  tokenpak/tokenpak:latest
```

### Option 3: Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: tokenpak
spec:
  replicas: 1
  selector:
    matchLabels:
      app: tokenpak
  template:
    metadata:
      labels:
        app: tokenpak
    spec:
      containers:
        - name: tokenpak
          image: tokenpak/tokenpak:latest
          ports:
            - containerPort: 8766
          env:
            - name: TOKENPAK_MODE
              value: hybrid
          volumeMounts:
            - name: config
              mountPath: /root/.tokenpak
      volumes:
        - name: config
          persistentVolumeClaim:
            claimName: tokenpak-config
---
apiVersion: v1
kind: Service
metadata:
  name: tokenpak
spec:
  selector:
    app: tokenpak
  ports:
    - port: 8766
      targetPort: 8766
```

---

## Team Configuration

`~/.tokenpak/config.json` on the server:

```json
{
  "proxy": {
    "port": 8766,
    "host": "0.0.0.0",
    "mode": "hybrid",
    "team_mode": true
  },
  "team": {
    "admin_token": "your-admin-secret",
    "allow_agent_registration": true,
    "budget": {
      "monthly_usd": 500,
      "alert_at_pct": 80,
      "per_agent_monthly_usd": 100
    }
  },
  "dashboard": {
    "enabled": true,
    "port": 8767,
    "require_auth": true
  }
}
```

---

## Agent Onboarding

Each team member/agent points their LLM client at the team server:

```bash
# Claude Code
export ANTHROPIC_BASE_URL=http://team-server:8766

# Register this agent (optional, enables per-agent tracking)
tokenpak agent register "cali" --server http://team-server:8766
```

---

## Directive Cache

The team server caches common intelligence directives from upstream, reducing round trips:

```
Without cache: Agent → Team Server → Intelligence Server (every request)
With cache:    Agent → Team Server (cached, TTL 5min)
```

Configure:

```json
{
  "cache": {
    "directives_ttl_seconds": 300,
    "invalidate_on_recipe_update": true
  }
}
```

---

## Admin Dashboard

Access at `http://team-server:8767` (default dashboard port).

**Views available:**
- **Team Overview** — total spend, active agents, requests/hour
- **Per-Agent Breakdown** — cost and token usage per registered agent
- **Budget Status** — team and per-agent budget consumption
- **Active Sessions** — live request stream
- **Routing Rules** — current model routing config

---

## SSO / Auth (Planned)

The team server supports SAML and OIDC hooks for enterprise auth integration:

```json
{
  "auth": {
    "mode": "sso",
    "provider": "okta",
    "client_id": "your-client-id",
    "metadata_url": "https://your-org.okta.com/app/.../metadata"
  }
}
```

Fallback: API key auth when SSO is not configured.

---

## Telemetry Endpoints

Team telemetry is available via REST:

```bash
# Team-wide summary
curl -H "X-Admin-Token: your-token" http://team-server:8766/v1/telemetry/team

# Per-agent detail
curl -H "X-Admin-Token: your-token" http://team-server:8766/v1/telemetry/agents/cali
```

See [API Reference](../api-reference.md) for full details.

---

## Security Considerations

1. **Never expose port 8766 to the internet** without authentication
2. Use a reverse proxy (nginx, Caddy) with TLS if team members are remote
3. The admin token should be strong and rotated regularly
4. Agent API keys pass through to providers — the server never stores them

### Nginx Example

```nginx
server {
    listen 443 ssl;
    server_name tokenpak.yourcompany.internal;

    ssl_certificate     /etc/ssl/tokenpak.crt;
    ssl_certificate_key /etc/ssl/tokenpak.key;

    location / {
        proxy_pass http://localhost:8766;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```
