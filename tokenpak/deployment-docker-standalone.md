# Deployment: Docker Standalone

Run TokenPak as a single Docker container. Best for local development or simple single-server setups.

## Prerequisites

- Docker 20.10+
- API key for your LLM provider

## Build the Image

```bash
git clone https://github.com/tokenpak/tokenpak
cd tokenpak
docker build -t tokenpak:latest .
```

Or pull from registry (when published):

```bash
docker pull ghcr.io/tokenpak/tokenpak:latest
```

## Run

```bash
docker run -d \
  --name tokenpak \
  -p 8766:8766 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  --restart unless-stopped \
  tokenpak:latest
```

## Verify

```bash
curl http://localhost:8766/health
# {"status":"ok",...}

curl http://localhost:8766/stats
# {"session":{"requests":0,...}}
```

## View Logs

```bash
docker logs -f tokenpak
```

## Stop / Remove

```bash
docker stop tokenpak
docker rm tokenpak
```

## Persistent Storage

To persist vault index and telemetry across container restarts:

```bash
docker run -d \
  --name tokenpak \
  -p 8766:8766 \
  -e ANTHROPIC_API_KEY=sk-ant-... \
  -v tokenpak_data:/root/.tokenpak \
  --restart unless-stopped \
  tokenpak:latest
```

## Deployment Files

See `deployments/docker-standalone/` for the full README.
