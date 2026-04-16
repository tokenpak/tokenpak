# TokenPak Production Deployment Guide

This guide covers deploying TokenPak in production environments: **bare metal**, **Docker**, and **Kubernetes**.

---

## Prerequisites

- Python 3.10+
- SQLite 3.35+ (WAL mode, built-in on macOS/Linux)
- Network access to LLM provider endpoints (unless air-gapped)

---

## Bare Metal

### 1. Install

```bash
pip install tokenpak
# Verify
tokenpak doctor
```

### 2. Configure

Create `~/.tokenpak/config.yaml`:

```yaml
proxy:
  host: 0.0.0.0
  port: 8766
  tls: true
  tls_cert: /etc/tokenpak/tls/cert.pem
  tls_key:  /etc/tokenpak/tls/key.pem

audit:
  enabled: true
  db: /var/lib/tokenpak/audit.db
  retention_days: 90

providers:
  - name: openai
    api_key_env: OPENAI_API_KEY
  - name: anthropic
    api_key_env: ANTHROPIC_API_KEY
```

### 3. Run as a systemd service

```ini
# /etc/systemd/system/tokenpak.service
[Unit]
Description=TokenPak Proxy
After=network.target

[Service]
User=tokenpak
ExecStart=/usr/local/bin/tokenpak serve --port 8766
Restart=on-failure
RestartSec=5s
Environment=TOKENPAK_HOME=/var/lib/tokenpak
EnvironmentFile=/etc/tokenpak/env

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now tokenpak
sudo systemctl status tokenpak
```

### 4. TLS (production)

Use Let's Encrypt or your CA:

```bash
certbot certonly --standalone -d tokenpak.example.com
# Then point tls_cert / tls_key in config.yaml
```

---

## Docker

### Quick Start

```bash
docker pull tokenpak/tokenpak:latest

docker run -d \
  --name tokenpak \
  -p 8766:8766 \
  -v tokenpak_data:/var/lib/tokenpak \
  -e OPENAI_API_KEY=sk-... \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  tokenpak/tokenpak:latest
```

### Docker Compose

```yaml
# docker-compose.yml
version: "3.9"

services:
  tokenpak:
    image: tokenpak/tokenpak:latest
    ports:
      - "8766:8766"
    volumes:
      - tokenpak_data:/var/lib/tokenpak
      - ./config.yaml:/etc/tokenpak/config.yaml:ro
    environment:
      - OPENAI_API_KEY
      - ANTHROPIC_API_KEY
      - TOKENPAK_ORG=Acme Corp
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8766/health"]
      interval: 30s
      timeout: 10s
      retries: 3

volumes:
  tokenpak_data:
```

```bash
docker compose up -d
docker compose logs -f tokenpak
```

### Building a custom image

```dockerfile
FROM python:3.12-slim
RUN pip install tokenpak==<version>
ENV TOKENPAK_HOME=/var/lib/tokenpak
VOLUME /var/lib/tokenpak
EXPOSE 8766
ENTRYPOINT ["tokenpak"]
CMD ["serve", "--port", "8766"]
```

---

## Kubernetes (Helm)

### Add the Helm repo

```bash
helm repo add tokenpak https://charts.tokenpak.ai
helm repo update
```

### Install

```bash
helm install tokenpak tokenpak/tokenpak \
  --namespace tokenpak \
  --create-namespace \
  --set secrets.openaiApiKey="sk-..." \
  --set secrets.anthropicApiKey="sk-ant-..." \
  --set audit.enabled=true \
  --set audit.retentionDays=90 \
  --set replicaCount=2
```

### values.yaml (full example)

```yaml
replicaCount: 2

image:
  repository: tokenpak/tokenpak
  tag: latest
  pullPolicy: IfNotPresent

service:
  type: ClusterIP
  port: 8766

ingress:
  enabled: true
  className: nginx
  hosts:
    - host: tokenpak.example.com
      paths:
        - path: /
          pathType: Prefix
  tls:
    - secretName: tokenpak-tls
      hosts:
        - tokenpak.example.com

audit:
  enabled: true
  retentionDays: 90
  storageClass: standard
  storageSize: 10Gi

secrets:
  openaiApiKey: ""
  anthropicApiKey: ""

resources:
  requests:
    memory: "256Mi"
    cpu: "100m"
  limits:
    memory: "512Mi"
    cpu: "500m"

autoscaling:
  enabled: false
  minReplicas: 1
  maxReplicas: 5
  targetCPUUtilizationPercentage: 80
```

### Network Topology

```
┌─────────────────────────────────────────────────┐
│  Kubernetes Cluster                             │
│                                                 │
│  ┌────────────┐    ┌──────────────────────┐    │
│  │  Ingress   │───▶│  TokenPak (2x pods)  │    │
│  │  (nginx)   │    │  port 8766           │    │
│  └────────────┘    └──────┬───────────────┘    │
│       ▲                   │                     │
│  ┌────┴─────┐     ┌───────▼──────────────┐    │
│  │  Agent   │     │  PersistentVolume    │    │
│  │  Clients │     │  (audit.db, data)    │    │
│  └──────────┘     └──────────────────────┘    │
└────────────────────────┬────────────────────────┘
                         │ HTTPS (external)
                    ┌────▼─────┐
                    │  LLM APIs │
                    │ (OpenAI,  │
                    │ Anthropic)│
                    └──────────┘
```

### Air-Gapped Topology

```
┌─────────────────────────────────────────────────┐
│  Air-Gapped Network                             │
│                                                 │
│  ┌───────────┐    ┌──────────────────────┐    │
│  │  Agents   │───▶│  TokenPak Proxy      │    │
│  └───────────┘    │  (offline mode)      │    │
│                   └──────┬───────────────┘    │
│                          │                     │
│                   ┌──────▼───────────────┐    │
│                   │  Local Intelligence  │    │
│                   │  Server + Audit DB   │    │
│                   └──────────────────────┘    │
│                                                 │
│  ✗ No outbound internet connections            │
│  ✓ Recipes via offline bundle                  │
│  ✓ License: offline RSA validation             │
└────────────────────────────────────────────────┘
```

---

## Post-Deploy Verification

```bash
# Check proxy health
curl http://localhost:8766/health

# Verify audit log
tokenpak audit summary

# Run compliance report
tokenpak compliance report --standard soc2

# Run doctor checks
tokenpak doctor
```

---

## Upgrading

```bash
# Bare metal
pip install --upgrade tokenpak

# Docker
docker compose pull && docker compose up -d

# Kubernetes
helm upgrade tokenpak tokenpak/tokenpak --reuse-values
```

---

## Backup & Recovery

```bash
# Backup audit DB
sqlite3 /var/lib/tokenpak/audit.db ".backup /backup/audit-$(date +%Y%m%d).db"

# Restore
cp /backup/audit-20260101.db /var/lib/tokenpak/audit.db

# Verify after restore
tokenpak audit verify
```
