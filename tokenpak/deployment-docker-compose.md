# Deployment: Docker Compose (Full Stack)

Run TokenPak with Redis cache and Prometheus metrics via Docker Compose.

## Prerequisites

- Docker 20.10+ with Compose V2 (`docker compose` not `docker-compose`)
- API key for your LLM provider

## Quick Start

```bash
cd deployments/docker-compose-full
cp .env.example .env
# Edit .env: add ANTHROPIC_API_KEY and/or OPENAI_API_KEY
docker compose up -d
curl http://localhost:8766/health
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| tokenpak | 8766 | LLM proxy + compression |
| redis | 6379 | Distributed cache |
| prometheus | 9090 | Metrics (optional) |

## Logs

```bash
docker compose logs -f tokenpak
```

## Health Checks

```bash
# Proxy
curl http://localhost:8766/health

# Proxy stats
curl http://localhost:8766/stats | python3 -m json.tool

# Redis
docker compose exec redis redis-cli ping
```

## Scaling

```bash
docker compose up -d --scale tokenpak=3
```

## Shutdown

```bash
docker compose down         # stop containers, keep volumes
docker compose down -v      # stop and wipe all volumes
```

## Deployment Files

See `deployments/docker-compose-full/` for all config files.
