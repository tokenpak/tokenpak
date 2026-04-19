# TokenPak Docker

Quick start for running TokenPak in Docker.

## Quick Start

```bash
cp .env.example .env
# Edit .env with your API keys
docker compose up -d
curl http://localhost:8766/health
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `TOKENPAK_PORT` | `8766` | Proxy port |
| `TOKENPAK_LOG_LEVEL` | `info` | Log level (debug/info/warn/error) |
| `ANTHROPIC_API_KEY` | — | Required: Anthropic API key |

## Logs & Debugging

```bash
# View logs
docker logs -f <container-name>

# Check health
curl http://localhost:8766/health
```

## Security

- Never hardcode API keys in Dockerfile or docker-compose.yml
- Use `.env` file (git-ignored) to inject secrets
- Verify no secrets in image: `docker inspect <image>`

## Troubleshooting

- **Port already in use:** Change `TOKENPAK_PORT` in `.env`
- **Container won't start:** Check `docker logs <container>`
- **Health check failing:** Verify API key is set in `.env`
- **Permission denied:** Ensure Docker daemon is running
