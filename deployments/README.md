# TokenPak — Deployment Examples

Ready-to-use deployment configurations for common environments.

## Scenarios

| Scenario | Directory | Description |
|----------|-----------|-------------|
| **Docker Standalone** | `docker-standalone/` | Single container, no orchestrator |
| **Docker Compose (Full)** | `docker-compose-full/` | Proxy + Redis + Prometheus |
| **Kubernetes** | `k8s/` | Deployment, Service, ConfigMap, Secrets, PVC |
| **AWS ECS (Fargate)** | `aws-ecs/` | Serverless containers on AWS |
| **GCP Cloud Run** | `gcp-cloud-run/` | Serverless containers on Google Cloud |

## Quick Start (Docker, Simplest)

```bash
docker build -t tokenpak:latest .

docker run -d \
  --name tokenpak \
  -p 8766:8766 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  --restart unless-stopped \
  tokenpak:latest

curl http://localhost:8766/health
```

## Quick Start (Docker Compose)

```bash
cd deployments/docker-compose-full
cp .env.example .env
# Edit .env with your API keys
docker compose up -d
curl http://localhost:8766/health
```

## Choosing a Scenario

| Use case | Recommended |
|----------|-------------|
| Local development | Docker Standalone |
| Single server (production) | Docker Compose Full |
| Team/shared infra | Kubernetes |
| AWS-native stack | AWS ECS Fargate |
| GCP-native stack | GCP Cloud Run |

## Security Checklist (All Scenarios)

- ✅ API keys via env vars or secrets manager — never hardcoded
- ✅ `.env` files git-ignored
- ✅ Run as non-root user where supported
- ✅ Health checks configured
- ✅ Graceful shutdown (SIGTERM) handled by proxy

## Verifying Deployments

All scenarios expose the same endpoints:

```bash
# Health check
curl http://PROXY_HOST:8766/health

# Token + cost stats
curl http://PROXY_HOST:8766/stats
```
