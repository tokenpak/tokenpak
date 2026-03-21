# TokenPak — Docker Standalone

Run TokenPak as a single Docker container (no Compose, no orchestrator).

## Prerequisites

- Docker 20.10+
- An API key for your LLM provider (Anthropic, OpenAI, etc.)

## Quick Start

```bash
# Pull or build the image
docker build -t tokenpak:latest .

# Run with env vars
docker run -d \
  --name tokenpak \
  -p 8766:8766 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  --restart unless-stopped \
  tokenpak:latest

# Verify it's running
curl http://localhost:8766/health
```

## With a Config File (Optional)

Mount a local config.json to persist settings:

```bash
mkdir -p ~/.tokenpak
cat > ~/.tokenpak/config.json << 'EOF'
{
  "proxy": { "port": 8766 },
  "compression": { "enabled": true, "level": "balanced" },
  "budget": { "monthly_usd": null }
}
EOF

docker run -d \
  --name tokenpak \
  -p 8766:8766 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -v ~/.tokenpak:/root/.tokenpak \
  --restart unless-stopped \
  tokenpak:latest
```

## Port Mapping

Change the host port (left side of `:`) — the container always listens on 8766:

```bash
docker run -d -p 9000:8766 -e ANTHROPIC_API_KEY=sk-ant-... tokenpak:latest
# Then point clients at localhost:9000
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Anthropic API key (required for Claude) |
| `OPENAI_API_KEY` | — | OpenAI API key (required for GPT) |
| `GOOGLE_API_KEY` | — | Google API key (required for Gemini) |
| `TOKENPAK_PORT` | `8766` | Proxy listen port |
| `TOKENPAK_MODE` | `hybrid` | Compression mode: `strict`, `hybrid`, `aggressive` |
| `TOKENPAK_LOG_LEVEL` | `info` | Log level: `debug`, `info`, `warn`, `error` |

## View Logs

```bash
docker logs -f tokenpak
```

## Health Check

The container includes a built-in health check:

```bash
docker inspect --format='{{.State.Health.Status}}' tokenpak
# Expected: healthy
```

## Graceful Shutdown

```bash
docker stop tokenpak   # sends SIGTERM, waits for clean exit
docker rm tokenpak     # remove container (data persists in volume)
```

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Port conflict | Change `-p 8766:8766` to `-p 9000:8766` |
| Container exits immediately | `docker logs tokenpak` — usually a missing API key |
| Health check failing | Verify API key is correct; check logs |
| "Cannot connect" | Confirm `-p` flag maps the right port |
