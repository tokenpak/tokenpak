# TokenPak Configuration

TokenPak uses environment variables and optional config files for setup. Most users need zero configuration.

---

## Environment Variables (Required)

### API Keys

Choose at least one:

```bash
# Anthropic Claude
export ANTHROPIC_API_KEY=sk-ant-...

# OpenAI
export OPENAI_API_KEY=sk-...

# Vertex AI (Google Cloud)
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json
export VERTEX_PROJECT_ID=your-project-id
export VERTEX_REGION=us-central1

# AWS Bedrock
export AWS_ACCESS_KEY_ID=AKIA...
export AWS_SECRET_ACCESS_KEY=...
export AWS_REGION=us-west-2
```

---

## Optional Environment Variables

```bash
# Proxy server
TOKENPAK_PORT=8766              # Default: 8766
TOKENPAK_HOST=0.0.0.0           # Default: 0.0.0.0 (all interfaces)
TOKENPAK_WORKERS=4              # Default: auto-detect CPU count

# Caching
TOKENPAK_CACHE_TTL_SECONDS=3600         # Default: 1 hour
TOKENPAK_REDIS_URL=redis://localhost:6379  # Optional: use Redis instead of in-memory

# Logging
TOKENPAK_LOG_LEVEL=INFO         # Options: DEBUG, INFO, WARNING, ERROR
TOKENPAK_LOG_FORMAT=json         # Options: json, text

# Safety
TOKENPAK_BUDGET_TOKENS=100000    # Daily token budget limit
TOKENPAK_DRY_RUN=false           # Dry-run mode (no actual API calls)
```

---

## Config File (Optional)

Create `.tokenpak/config.yaml` in your home directory:

```yaml
server:
  port: 8766
  host: 0.0.0.0
  workers: 4

cache:
  ttl_seconds: 3600
  backend: memory  # or 'redis'
  redis_url: redis://localhost:6379  # if backend is redis

logging:
  level: INFO
  format: json

budget:
  enabled: true
  daily_tokens: 100000

safety:
  dry_run: false
  timeout_seconds: 30
```

Precedence: CLI args > environment variables > config file

---

## Verify Configuration

```bash
# Check loaded config
tokenpak config show

# Test API connection
tokenpak doctor

# View health status
curl http://localhost:8766/health
```

---

## Docker / Compose Configuration

In `docker-compose.yml`:

```yaml
services:
  tokenpak:
    image: tokenpak:latest
    ports:
      - "8766:8766"
    environment:
      ANTHROPIC_API_KEY: ${ANTHROPIC_API_KEY}
      TOKENPAK_LOG_LEVEL: INFO
      TOKENPAK_WORKERS: 4
    volumes:
      - ~/.tokenpak/config.yaml:/etc/tokenpak/config.yaml
```

Or via `.env` file:

```bash
cp .env.example .env
# Edit .env with your API keys
docker compose up -d
```

---

See [INSTALLATION.md](installation.md) for setup steps.
