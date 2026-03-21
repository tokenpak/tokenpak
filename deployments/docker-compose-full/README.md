# TokenPak — Docker Compose (Full Stack)

Runs TokenPak proxy + Redis cache + Prometheus metrics.

## Prerequisites

- Docker 20.10+ with Compose V2
- An API key for your LLM provider

## Quick Start

```bash
# 1. Copy env template and fill in API keys
cp .env.example .env
# Edit .env: add ANTHROPIC_API_KEY and/or OPENAI_API_KEY

# 2. Start the stack
docker compose up -d

# 3. Verify
curl http://localhost:8766/health
```

## Services

| Service | Port | Description |
|---------|------|-------------|
| tokenpak | 8766 | LLM proxy + compression |
| redis | 6379 | Distributed cache (improves hit rate across restarts) |
| prometheus | 9090 | Metrics scraping (optional, see below) |

## Logs

```bash
docker compose logs -f tokenpak      # proxy logs
docker compose logs -f redis         # cache logs
docker compose logs                  # all services
```

## Config File (Optional)

Mount a custom `config.json` to override defaults:

```yaml
# Add to tokenpak service volumes:
volumes:
  - ./config.json:/root/.tokenpak/config.json:ro
```

## Health Checks

```bash
# Proxy health
curl http://localhost:8766/health

# Stats
curl http://localhost:8766/stats | python3 -m json.tool

# Redis ping
docker compose exec redis redis-cli ping
```

## Prometheus (Optional)

Prometheus is included but optional. To disable it, comment out the `prometheus` block
in `docker-compose.yml`. Otherwise access it at http://localhost:9090.

## Scaling

To run multiple proxy replicas (requires a load balancer):

```bash
docker compose up -d --scale tokenpak=3
```

## Shutdown

```bash
docker compose down          # stop and remove containers
docker compose down -v       # also remove volumes (wipes data)
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `healthy` never shows | Check logs: `docker compose logs tokenpak` |
| Redis not connecting | Ensure Redis is healthy before proxy starts (handled by `depends_on`) |
| Port conflict | Change `TOKENPAK_PORT` in `.env` |
| No API keys | Fill in `.env` — container starts but requests will fail without keys |
