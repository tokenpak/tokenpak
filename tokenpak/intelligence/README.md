# TokenPak Intelligence Server

Standalone FastAPI microservice — NOT part of the main proxy pipeline.

## Installation

Requires the `[server]` extra:

```bash
pip install tokenpak[server]
```

The core `pip install tokenpak` does **not** pull FastAPI or pydantic.
Attempting to `import tokenpak.intelligence` without the extra raises an `ImportError`
with a helpful message.

## Launch

```bash
uvicorn tokenpak.intelligence.server:app --host 0.0.0.0 --port 8080
```

## What this is

A separate HTTP service for advanced analytics and experiment management.
Designed to run alongside the main proxy on its own port.

## Features

- X1. A/B Testing (Enterprise) — `ab_router.py` + `ab_optimizer.py`
- T5. Cost Intelligence (Pro) — `cost_intelligence.py` + `cost_router.py`
- Deep Health Check — `deep_health.py`
- License Validation endpoint — `license_endpoint.py`
- Tier-based rate limiting — `auth.py` (free=20/min, pro=100, team=500, enterprise=unlimited)

## Why zero imports from proxy

This is a standalone service, not imported by the proxy. It is started separately
(e.g. `uvicorn tokenpak.intelligence.server:app`) and communicates via HTTP.

## Status

Wired as `[server]` optional extra. Use `pip install tokenpak[server]` to enable.

*Audited: 2026-03-25 by Trix | Optional extra wired: 2026-03-25 by Trix*
