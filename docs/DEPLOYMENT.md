# TokenPak Deployment Guide

This guide covers production deployment of the TokenPak LLM proxy — from a single machine to load-balanced multi-instance setups.

> **Quick reference:** For basic local install, see the root [DEPLOYMENT.md](../DEPLOYMENT.md).
> This guide focuses on hardened, production-grade deployments.

---

## System Requirements

### Minimum Hardware

| Resource | Minimum | Recommended |
|---|---|---|
| CPU | 1 core | 2+ cores |
| RAM | 256 MB | 512 MB–1 GB |
| Disk | 100 MB | 1 GB+ (telemetry DB + vault index) |
| Python | 3.10+ | 3.11+ |
| OS | Linux / macOS / Windows | Linux (Ubuntu 22.04 LTS+) |

RAM is low because TokenPak is a lightweight async proxy. The main consumer is the optional vault index — budget ~100 MB per 10,000 indexed files.

### Network Requirements

| Port | Protocol | Purpose | Required |
|---|---|---|---|
| `8766` | TCP | Proxy ingress (clients → TokenPak) | Yes |
| `8766/dashboard` | HTTP | Web dashboard | Optional |
| `443` | TCP (outbound) | LLM provider APIs | Yes |

**Firewall rules (ufw example):**

```bash
# Allow proxy port only from trusted IPs/networks
sudo ufw allow from 10.0.0.0/8 to any port 8766 proto tcp

# Deny public access to the proxy (it holds your API keys)
sudo ufw deny 8766

# Outbound HTTPS must be allowed
sudo ufw allow out 443/tcp
```

> ⚠️ **Never expose port 8766 to the public internet.** The proxy forwards requests with your API keys. Treat it like a database port.

---

## Installation

### Option 1: pip (recommended)

```bash
pip install tokenpak

# With optional extras
pip install tokenpak[tiktoken]   # accurate token counting (recommended)
pip install tokenpak[ml]         # ML-powered compression via LLMLingua
```

### Option 2: From Source

```bash
git clone https://github.com/kaywhy331/tokenpak
cd tokenpak
pip install -e .

# With extras
pip install -e ".[tiktoken,ml]"
```

### Option 3: Docker

```bash
# Pull from registry
docker pull kaywhy331/tokenpak:latest

# Or build from source
git clone https://github.com/kaywhy331/tokenpak
cd tokenpak
docker build -t tokenpak:local .
```

Run the container:

```bash
docker run -d \
  --name tokenpak \
  --restart unless-stopped \
  -p 127.0.0.1:8766:8766 \
  -e ANTHROPIC_API_KEY="sk-ant-..." \
  -e OPENAI_API_KEY="sk-..." \
  -v tokenpak-data:/home/tokenpak/.tokenpak \
  kaywhy331/tokenpak:latest
```

> Binding to `127.0.0.1:8766` keeps the port local-only. Use a reverse proxy (nginx, Caddy) for external access.

### Verify Install

```bash
tokenpak --version
tokenpak doctor        # checks Python version, deps, config
tokenpak status        # verify proxy is reachable
```

---

## Configuration

### Config File

Default location: `~/.tokenpak/config.json`

```json
{
  "proxy": {
    "port": 8766,
    "host": "127.0.0.1",
    "passthrough_url": "https://api.openai.com"
  },
  "compression": {
    "enabled": true,
    "level": "balanced",
    "threshold_tokens": 4500
  },
  "budget": {
    "monthly_usd": 100,
    "alert_at_pct": 80
  },
  "vault": {
    "db_path": "~/.tokenpak/registry.db",
    "watch": false
  },
  "stats_footer": false,
  "debug": false
}
```

### Environment Variables

All env vars override config file values. **Env vars take priority.**

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Anthropic API key (forwarded to provider) |
| `OPENAI_API_KEY` | — | OpenAI API key |
| `GOOGLE_API_KEY` | — | Google Gemini API key |
| `TOKENPAK_PORT` | `8766` | Proxy listen port |
| `TOKENPAK_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` for all interfaces) |
| `TOKENPAK_MODE` | `hybrid` | Compression mode: `strict`, `hybrid`, `aggressive` |
| `TOKENPAK_COMPACT` | `1` | Master compression switch (`0` to disable) |
| `TOKENPAK_COMPACT_THRESHOLD_TOKENS` | `4500` | Min tokens before compression activates |
| `TOKENPAK_DB` | `.ocp/monitor.db` | SQLite telemetry database path |
| `TOKENPAK_STATS_FOOTER` | `0` | Append savings summary to responses |
| `TOKENPAK_DEBUG` | `0` | Enable debug logging |
| `TOKENPAK_METRICS_ENABLED` | `0` | Opt-in anonymous usage metrics |

### Security Best Practices for Secrets

**Never hardcode API keys in config files or code.** Use one of these approaches:

#### Option A: Environment file (recommended for systemd)

```bash
# Create protected env file
sudo mkdir -p /etc/tokenpak
sudo touch /etc/tokenpak/secrets.env
sudo chmod 600 /etc/tokenpak/secrets.env
sudo chown tokenpak:tokenpak /etc/tokenpak/secrets.env

# Add secrets
echo "ANTHROPIC_API_KEY=sk-ant-..." | sudo tee -a /etc/tokenpak/secrets.env
echo "OPENAI_API_KEY=sk-..."        | sudo tee -a /etc/tokenpak/secrets.env
```

Reference in systemd: `EnvironmentFile=/etc/tokenpak/secrets.env`

#### Option B: System keyring (desktop/dev machines)

```bash
pip install keyring
keyring set tokenpak ANTHROPIC_API_KEY
```

#### Option C: Cloud secrets manager (production)

- **AWS:** Secrets Manager or Parameter Store
- **GCP:** Secret Manager
- **Azure:** Key Vault

Inject at runtime via your deployment tooling (e.g., `aws secretsmanager get-secret-value | jq -r ...`).

#### Option D: Docker secrets

```bash
echo "sk-ant-..." | docker secret create anthropic_api_key -

docker service create \
  --secret anthropic_api_key \
  --env ANTHROPIC_API_KEY_FILE=/run/secrets/anthropic_api_key \
  tokenpak
```

---

## Running as a Service

### systemd (Linux — recommended)

Create a dedicated user:

```bash
sudo useradd --system --no-create-home --shell /bin/false tokenpak
```

Create the service file at `/etc/systemd/system/tokenpak.service`:

```ini
[Unit]
Description=TokenPak LLM Proxy
Documentation=https://github.com/kaywhy331/tokenpak
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=tokenpak
Group=tokenpak

# Load API keys from protected file
EnvironmentFile=/etc/tokenpak/secrets.env

# Compression settings
Environment=TOKENPAK_MODE=hybrid
Environment=TOKENPAK_COMPACT=1
Environment=PYTHONUNBUFFERED=1

ExecStart=/usr/local/bin/tokenpak serve --port 8766
ExecReload=/bin/kill -HUP $MAINPID

Restart=on-failure
RestartSec=5s
StartLimitInterval=60s
StartLimitBurst=3

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=tokenpak

# Security hardening
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/tokenpak

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable tokenpak
sudo systemctl start tokenpak

# Verify
sudo systemctl status tokenpak
sudo journalctl -u tokenpak -f
```

### Docker Compose

`docker-compose.yml`:

```yaml
version: "3.9"

services:
  tokenpak:
    image: kaywhy331/tokenpak:latest
    container_name: tokenpak
    restart: unless-stopped
    ports:
      - "127.0.0.1:8766:8766"
    environment:
      - TOKENPAK_MODE=hybrid
      - TOKENPAK_COMPACT=1
      - TOKENPAK_PORT=8766
    env_file:
      - .env.secrets          # ANTHROPIC_API_KEY, OPENAI_API_KEY, etc.
    volumes:
      - tokenpak-data:/home/tokenpak/.tokenpak
    healthcheck:
      test: ["CMD", "tokenpak", "status"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s

  # Optional: nginx reverse proxy for TLS termination
  nginx:
    image: nginx:alpine
    restart: unless-stopped
    ports:
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/conf.d/tokenpak.conf:ro
      - ./certs:/etc/nginx/certs:ro
    depends_on:
      - tokenpak

volumes:
  tokenpak-data:
```

`.env.secrets` (chmod 600, never commit):

```
ANTHROPIC_API_KEY=sk-ant-...
OPENAI_API_KEY=sk-...
GOOGLE_API_KEY=...
```

Start:

```bash
docker compose up -d
docker compose logs -f tokenpak
```

### Monitoring & Logs

```bash
# systemd
sudo journalctl -u tokenpak -f
sudo journalctl -u tokenpak --since "1 hour ago"

# Docker
docker logs tokenpak -f

# CLI health check
tokenpak status
tokenpak status --full

# Cost tracking
tokenpak cost --today
tokenpak cost --week
tokenpak savings --lifetime

# Dashboard
# http://localhost:8766/dashboard  (runs alongside the proxy)
```

---

## Scaling

### Single Instance (default)

TokenPak is async (uvicorn + starlette) and handles concurrent requests well on a single machine. For most teams (<50 developers, <10K req/day), a single instance is sufficient.

Tune uvicorn workers:

```bash
tokenpak serve --port 8766 --workers 4
```

Rule of thumb: `workers = (2 × CPU cores) + 1`.

### Multi-Instance (load-balanced)

For high throughput, run multiple instances behind a load balancer.

**Requirements when load-balancing:**
- Replace SQLite telemetry with a shared database (see below)
- All instances must have the same API keys
- Session stickiness is **not required** — TokenPak is stateless per-request

**nginx load balancer config:**

```nginx
upstream tokenpak {
    least_conn;
    server 10.0.1.10:8766;
    server 10.0.1.11:8766;
    server 10.0.1.12:8766;
    keepalive 32;
}

server {
    listen 443 ssl;
    server_name tokenpak.internal;

    ssl_certificate     /etc/nginx/certs/tokenpak.crt;
    ssl_certificate_key /etc/nginx/certs/tokenpak.key;

    location / {
        proxy_pass http://tokenpak;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 120s;
    }
}
```

### Database Scaling

| Setup | Database | When to use |
|---|---|---|
| Single machine | SQLite (default) | Solo dev, small team |
| Multi-instance / shared telemetry | PostgreSQL | Team > 5, load-balanced |
| Read-heavy dashboards | PostgreSQL + read replica | Enterprise |

Switch to PostgreSQL:

```bash
pip install tokenpak[postgres]

# Set connection string
export TOKENPAK_DB=postgresql://tokenpak:password@db-host:5432/tokenpak
```

Run migrations:

```bash
tokenpak db migrate
```

### Cache Scaling

| Setup | Cache | When to use |
|---|---|---|
| Single instance | In-memory (default) | Dev / small teams |
| Multi-instance | Redis | Load-balanced deployments |

Enable Redis cache:

```bash
pip install tokenpak[redis]
export TOKENPAK_CACHE_URL=redis://redis-host:6379/0
```

Redis gives multi-instance cache sharing so duplicate requests (same prompt, same model) are served from cache across all nodes.

---

## Troubleshooting

### Proxy won't start

```bash
tokenpak doctor          # auto-diagnoses common issues
tokenpak status          # check if already running on that port
lsof -i :8766            # see what's using the port
```

Common fixes:
- **Port already in use:** `tokenpak serve --port 8767` or kill the conflicting process
- **Permission denied on port <1024:** Use ports ≥1024 or set `CAP_NET_BIND_SERVICE`
- **Python version too old:** `python --version` must be 3.10+

### Requests not being compressed

```bash
# Check compression status
tokenpak status
# Look for "Compression: enabled"

# Lower threshold for testing
TOKENPAK_COMPACT_THRESHOLD_TOKENS=100 tokenpak serve
```

Compression only activates above the token threshold (default: 4,500). Short requests pass through unchanged — this is correct behavior.

### API key errors

```bash
# Verify keys are set
echo $ANTHROPIC_API_KEY | head -c 20
echo $OPENAI_API_KEY | head -c 20

# Test provider connectivity directly
curl https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" \
  -H "anthropic-version: 2023-06-01"
```

### High latency

```bash
# Enable debug mode to see timing breakdown
TOKENPAK_DEBUG=1 tokenpak serve

# Profile compression overhead
tokenpak benchmark --samples 10
```

Typical compression overhead: 5–50ms. If you're seeing >200ms, try:
- Reduce `--workers` (CPU contention)
- Set `TOKENPAK_MODE=hybrid` (avoids aggressive compression on small requests)
- Disable ML compression: `pip uninstall llmlingua`

### Debug mode

```bash
TOKENPAK_DEBUG=1 tokenpak serve 2>&1 | tee /tmp/tokenpak-debug.log
```

Debug output shows: request routing, compression ratio per request, provider response times, cache hits/misses.

### Performance tuning

```bash
# Calibrate workers for your hardware (run once)
tokenpak calibrate

# Check recommended settings
cat ~/.tokenpak/calibration.json
```

---

## Example Deployments

### Local (Single Machine)

Best for: Solo developer, personal use, testing.

```bash
# Install
pip install tokenpak[tiktoken]

# Set API keys in shell profile
echo 'export ANTHROPIC_API_KEY="sk-ant-..."' >> ~/.bashrc
echo 'export OPENAI_API_KEY="sk-..."' >> ~/.bashrc
source ~/.bashrc

# Start proxy
tokenpak serve --port 8766

# Point Claude Code at proxy
export ANTHROPIC_BASE_URL=http://localhost:8766

# View dashboard
open http://localhost:8766/dashboard
```

For persistence, install the user-level systemd service:

```bash
# (See Running as a Service → systemd, using --user variant)
systemctl --user enable tokenpak
systemctl --user start tokenpak
```

---

### AWS (EC2 + ALB)

Best for: Team use, high availability.

**Architecture:**
```
Internet → ALB (HTTPS:443) → EC2 Auto Scaling Group (tokenpak:8766)
                                         ↓
                                   RDS PostgreSQL (telemetry)
                                   ElastiCache Redis (cache)
```

**Step-by-step:**

1. **Launch EC2 instance** (t3.small minimum, t3.medium recommended)
   - AMI: Ubuntu 22.04 LTS
   - Security group: allow port 8766 from ALB security group only

2. **Install TokenPak:**
   ```bash
   sudo apt update && sudo apt install -y python3.11 python3.11-pip
   pip3 install tokenpak[tiktoken,postgres,redis]
   ```

3. **Store secrets in AWS Secrets Manager:**
   ```bash
   aws secretsmanager create-secret \
     --name tokenpak/api-keys \
     --secret-string '{"ANTHROPIC_API_KEY":"sk-ant-...","OPENAI_API_KEY":"sk-..."}'
   ```

   Retrieve at startup (in `/etc/tokenpak/secrets.env`):
   ```bash
   aws secretsmanager get-secret-value \
     --secret-id tokenpak/api-keys \
     --query SecretString --output text \
     | jq -r 'to_entries[] | "\(.key)=\(.value)"' \
     > /etc/tokenpak/secrets.env
   chmod 600 /etc/tokenpak/secrets.env
   ```

4. **Configure for PostgreSQL + Redis:**
   ```bash
   export TOKENPAK_DB=postgresql://tokenpak:pass@rds-endpoint:5432/tokenpak
   export TOKENPAK_CACHE_URL=redis://elasticache-endpoint:6379/0
   tokenpak db migrate
   ```

5. **Set up systemd service** (see Running as a Service section above)

6. **Create ALB:**
   - Target group: HTTP, port 8766, health check path `/health`
   - Listener: HTTPS:443 → target group
   - SSL cert via AWS ACM

7. **Auto Scaling Group** with the EC2 as launch template; scale on CPU > 60%.

**Estimated cost:** ~$30–60/month (t3.small × 2 + RDS db.t3.micro + ElastiCache cache.t3.micro).

---

### GCP (Cloud Run)

Best for: Serverless, pay-per-request, zero ops.

**Architecture:**
```
Clients → Cloud Run (tokenpak, auto-scales 0→N)
                 ↓
          Cloud SQL PostgreSQL + Memorystore Redis
```

**Step-by-step:**

1. **Build and push image:**
   ```bash
   git clone https://github.com/kaywhy331/tokenpak
   cd tokenpak
   gcloud builds submit --tag gcr.io/YOUR_PROJECT/tokenpak
   ```

2. **Store secrets in Secret Manager:**
   ```bash
   echo -n "sk-ant-..." | gcloud secrets create anthropic-api-key --data-file=-
   echo -n "sk-..."     | gcloud secrets create openai-api-key --data-file=-
   ```

3. **Deploy to Cloud Run:**
   ```bash
   gcloud run deploy tokenpak \
     --image gcr.io/YOUR_PROJECT/tokenpak \
     --platform managed \
     --region us-central1 \
     --port 8766 \
     --no-allow-unauthenticated \
     --set-secrets "ANTHROPIC_API_KEY=anthropic-api-key:latest,OPENAI_API_KEY=openai-api-key:latest" \
     --set-env-vars "TOKENPAK_MODE=hybrid,TOKENPAK_HOST=0.0.0.0" \
     --min-instances 1 \
     --max-instances 10 \
     --memory 512Mi \
     --cpu 1
   ```

4. **Restrict access:**
   ```bash
   # Allow only your VPC or specific service accounts
   gcloud run services add-iam-policy-binding tokenpak \
     --member="serviceAccount:your-sa@project.iam.gserviceaccount.com" \
     --role="roles/run.invoker"
   ```

5. Point clients at the Cloud Run URL with `Authorization: Bearer $(gcloud auth print-identity-token)`.

**Estimated cost:** ~$5–20/month at moderate usage (Cloud Run is billed per-request).

---

### Azure (Container Apps)

Best for: Teams already on Azure, enterprise compliance requirements.

**Architecture:**
```
Clients → Azure Container Apps (tokenpak, auto-scale)
                      ↓
             Azure Database for PostgreSQL + Azure Cache for Redis
```

**Step-by-step:**

1. **Store secrets in Key Vault:**
   ```bash
   az keyvault secret set --vault-name mykeyvault --name anthropic-api-key --value "sk-ant-..."
   az keyvault secret set --vault-name mykeyvault --name openai-api-key   --value "sk-..."
   ```

2. **Create Container Apps environment:**
   ```bash
   az containerapp env create \
     --name tokenpak-env \
     --resource-group myRG \
     --location eastus
   ```

3. **Deploy:**
   ```bash
   az containerapp create \
     --name tokenpak \
     --resource-group myRG \
     --environment tokenpak-env \
     --image kaywhy331/tokenpak:latest \
     --target-port 8766 \
     --ingress internal \
     --min-replicas 1 \
     --max-replicas 10 \
     --secrets \
       "anthropic-key=keyvaultref:https://mykeyvault.vault.azure.net/secrets/anthropic-api-key,identityref:system" \
       "openai-key=keyvaultref:https://mykeyvault.vault.azure.net/secrets/openai-api-key,identityref:system" \
     --env-vars \
       "ANTHROPIC_API_KEY=secretref:anthropic-key" \
       "OPENAI_API_KEY=secretref:openai-key" \
       "TOKENPAK_MODE=hybrid" \
       "TOKENPAK_HOST=0.0.0.0"
   ```

4. Restrict ingress to your VNet or specific IP ranges.

**Estimated cost:** ~$15–40/month (Container Apps consumption plan scales to zero when idle).

---

## Upgrading

```bash
pip install --upgrade tokenpak

# Verify
tokenpak doctor
tokenpak status
```

If running as a service:

```bash
pip install --upgrade tokenpak
sudo systemctl restart tokenpak   # or: docker compose pull && docker compose up -d
```

---

## Uninstall

```bash
# Stop service
sudo systemctl stop tokenpak
sudo systemctl disable tokenpak

# Remove package
pip uninstall tokenpak

# Remove data (optional — deletes all telemetry and vault indexes)
rm -rf ~/.tokenpak
sudo rm -rf /etc/tokenpak
```

---

## See Also

- [TROUBLESHOOTING.md](../TROUBLESHOOTING.md) — FAQ, common errors, performance tuning
- [ARCHITECTURE.md](../ARCHITECTURE.md) — internals and design decisions
- [API.md](../API.md) — proxy API reference
- [docs/guides/team-server.md](guides/team-server.md) — shared team proxy setup
