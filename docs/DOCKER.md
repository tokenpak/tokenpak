# TokenPak Docker Deployment Guide

Complete guide for deploying TokenPak proxy using Docker and Docker Compose.

## Quick Start

### Build the Image

```bash
docker build -t tokenpak .
```

### Run a Single Container

```bash
# Basic (uses defaults)
docker run -p 8766:8766 tokenpak

# With config volume
docker run -p 8766:8766 \
  -v $(pwd)/config/tokenpack.config.json:/app/tokenpack.config.json:ro \
  -v tokenpak-logs:/logs \
  tokenpak

# With environment variables
docker run -p 8766:8766 \
  -e TOKENPAK_LOG_LEVEL=debug \
  -e TOKENPAK_ENABLE_METRICS=true \
  tokenpak
```

### Docker Compose (Recommended)

```bash
# Copy environment file
cp .env.example .env

# Copy config file
cp config/tokenpack.config.json.example config/tokenpack.config.json

# Start services
docker-compose up -d

# Check status
docker-compose ps

# View logs
docker-compose logs -f tokenpak

# Stop services
docker-compose down
```

## Build Instructions

### Standard Build

```bash
docker build -t tokenpak:latest .
docker build -t tokenpak:v1.0.0 .  # With version tag
```

### Build with Custom Base Image

```bash
docker build --build-arg BASE_IMAGE=python:3.12-slim -t tokenpak .
```

### Check Image Size

```bash
docker images tokenpak
# Expected: <500MB
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `TOKENPAK_PORT` | 8766 | Port to listen on |
| `TOKENPAK_LOG_LEVEL` | info | Log level: debug, info, warn, error |
| `TOKENPAK_ENABLE_METRICS` | true | Enable metrics collection |

### Config Volume

Mount configuration at container startup:

```bash
# Using docker run
docker run -v $(pwd)/config/tokenpack.config.json:/app/tokenpack.config.json:ro tokenpak

# Using docker-compose (automatic)
docker-compose up
```

### .env File

```bash
# .env
TOKENPAK_PORT=8766
TOKENPAK_LOG_LEVEL=info
TOKENPAK_ENABLE_METRICS=true
```

## Volume Management

### Logs Volume

Persistent log storage (created by docker-compose):

```bash
# View logs from host
docker-compose exec tokenpak tail -f /logs/proxy-2026-03-10.log

# Mount custom path
volumes:
  - /var/log/tokenpak:/logs
```

### Cache Volume

Optional cache persistence:

```yaml
volumes:
  tokenpak-cache:
    driver: local
```

### Config Volume (Development)

For live config updates during development:

```bash
docker run -v $(pwd)/config:/app/config:ro tokenpak
```

## Running Behind Nginx

### Nginx Reverse Proxy Setup

```nginx
upstream tokenpak {
    server localhost:8766;
}

server {
    listen 80;
    server_name api.example.com;

    location / {
        proxy_pass http://tokenpak;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Timeout settings
        proxy_connect_timeout 30s;
        proxy_send_timeout 30s;
        proxy_read_timeout 30s;
    }
}
```

### Docker Compose with Nginx

```yaml
services:
  nginx:
    image: nginx:alpine
    ports:
      - "80:80"
      - "443:443"
    volumes:
      - ./nginx.conf:/etc/nginx/nginx.conf:ro
      - ./ssl:/etc/nginx/ssl:ro
    depends_on:
      - tokenpak
    networks:
      - tokenpak-network

  tokenpak:
    # ... existing config
    networks:
      - tokenpak-network
```

## Health Checks

### Docker Health Check

Built-in health check via `/health` endpoint:

```bash
# Check health status
docker inspect tokenpak | grep -A 10 "Health"

# Manual health check
curl http://localhost:8766/health
```

### Health Check Response

```json
{
  "status": "healthy",
  "version": "1.0.0",
  "uptime_seconds": 3600,
  "request_count": 15000,
  "last_request": "2026-03-10T06:30:00Z"
}
```

### Kubernetes Liveness Probe

```yaml
livenessProbe:
  httpGet:
    path: /health
    port: 8766
  initialDelaySeconds: 40
  periodSeconds: 30
  timeoutSeconds: 10
  failureThreshold: 3
```

## Multi-Container Setup (With Redis)

### Enable Redis Cache

```bash
# Option 1: .env file
COMPOSE_PROFILES=with-cache

# Option 2: Command line
docker-compose --profile with-cache up
```

### Docker Compose (with Redis)

Redis service is optional but recommended for production:

```bash
# Start TokenPak + Redis
docker-compose --profile with-cache up -d

# View both services
docker-compose ps
# tokenpak    Up (healthy)
# redis       Up

# Connect TokenPak to Redis
# Requires: TOKENPAK_CACHE_TYPE=redis
#          TOKENPAK_REDIS_HOST=redis
#          TOKENPAK_REDIS_PORT=6379
```

## Troubleshooting

### Container Won't Start

```bash
# Check logs
docker-compose logs tokenpak

# Common issues:
# - Port already in use: change TOKENPAK_PORT in .env
# - Config file missing: cp config/tokenpack.config.json.example config/tokenpack.config.json
# - Permission denied: check volume mount permissions
```

### Health Check Failing

```bash
# Debug health endpoint
docker exec tokenpak curl -v http://localhost:8766/health

# Check logs for errors
docker-compose logs tokenpak | grep -i error

# Increase health check start period if startup is slow
healthcheck:
  start_period: 60s  # Increase from 40s
```

### High Memory Usage

```bash
# Check resource usage
docker stats tokenpak

# Limit memory
docker run -m 512m tokenpak

# Or in docker-compose
deploy:
  resources:
    limits:
      memory: 512M
```

### Logs Not Persisting

```bash
# Check volume mount
docker inspect tokenpak | grep -A 20 Mounts

# Verify logs directory exists
docker exec tokenpak ls -la /logs

# Create directory if missing
docker exec tokenpak mkdir -p /logs
```

## Cloud Deployment

### GCP Cloud Run

```bash
# Build and push to GCP Registry
docker build -t gcr.io/PROJECT_ID/tokenpak .
docker push gcr.io/PROJECT_ID/tokenpak

# Deploy to Cloud Run
gcloud run deploy tokenpak \
  --image gcr.io/PROJECT_ID/tokenpak \
  --port 8766 \
  --memory 512Mi \
  --timeout 30 \
  --allow-unauthenticated
```

### AWS ECS

```bash
# Push to ECR
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin 123456789.dkr.ecr.us-east-1.amazonaws.com

docker build -t tokenpak .
docker tag tokenpak:latest 123456789.dkr.ecr.us-east-1.amazonaws.com/tokenpak:latest
docker push 123456789.dkr.ecr.us-east-1.amazonaws.com/tokenpak:latest

# Create ECS task definition + service
# (See AWS documentation for details)
```

### Azure Container Instances

```bash
# Push to Azure Container Registry
az acr build --registry myregistry --image tokenpak:latest .

# Deploy
az container create \
  --resource-group mygroup \
  --name tokenpak \
  --image myregistry.azurecr.io/tokenpak:latest \
  --ports 8766 \
  --memory 0.5
```

## Production Checklist

- [ ] Dockerfile built successfully (<500MB)
- [ ] Health check responding (curl /health)
- [ ] Logging enabled (`TOKENPAK_LOG_LEVEL=info`)
- [ ] Metrics enabled (`TOKENPAK_ENABLE_METRICS=true`)
- [ ] Config volume mounted (read-only)
- [ ] Logs volume mounted (persistent)
- [ ] Non-root user running (tokenpak:tokenpak)
- [ ] Resource limits set (CPU, memory)
- [ ] Reverse proxy configured (Nginx/HAProxy)
- [ ] TLS/SSL enabled (for production)
- [ ] Log aggregation configured (e.g., ELK, Datadog)
- [ ] Monitoring alerts set up
- [ ] Backup strategy for logs + cache

## Performance Tuning

### Resource Allocation

```yaml
deploy:
  resources:
    limits:
      cpus: '2'
      memory: 1G
    reservations:
      cpus: '1'
      memory: 512M
```

### Log Rotation

```yaml
logging:
  driver: "json-file"
  options:
    max-size: "10m"    # Rotate at 10MB
    max-file: "3"      # Keep 3 files
```

### Connection Pool

```bash
docker run \
  -e TOKENPAK_CACHE_POOL_SIZE=10 \
  -e TOKENPAK_DB_POOL_SIZE=5 \
  tokenpak
```

## Security Best Practices

- ✅ Non-root user (UID 1000)
- ✅ No secrets in Dockerfile
- ✅ Environment variables for configuration
- ✅ Read-only filesystem for code
- ✅ Resource limits enforced
- ✅ Health checks enabled
- ✅ Logging enabled for audit trail
- ✅ TLS in front (Nginx proxy)

## Monitoring

### Prometheus Metrics

```yaml
volumes:
  - /etc/prometheus/prometheus.yml:/etc/prometheus/prometheus.yml:ro

# In prometheus.yml
scrape_configs:
  - job_name: 'tokenpak'
    static_configs:
      - targets: ['localhost:8766']
```

### Container Logs

```bash
# View logs
docker-compose logs -f tokenpak

# Export logs
docker-compose logs tokenpak > logs.txt

# Filter by timestamp
docker-compose logs --since 2026-03-10 tokenpak
```

---

For more information, see:
- DEPLOYMENT.md — System setup guide
- LOGGING.md — Logging configuration
- [Docker documentation](https://docs.docker.com/)
