# Proxy Drift Detector

**Script:** `~/vault/06_RUNTIME/scripts/check-proxy-drift.sh`  
**Created:** 2026-03-29 (TPK-DRIFT-DETECT-001)

## Purpose

Detects when multiple divergent copies of `proxy.py` exist in the TokenPak repo. This was the root cause of significant technical debt — 3 copies with different hashes accumulated over time.

## Usage

```bash
# Check default repo location
bash ~/vault/06_RUNTIME/scripts/check-proxy-drift.sh

# Check a specific path
bash ~/vault/06_RUNTIME/scripts/check-proxy-drift.sh /path/to/tokenpak

# Use in CI (exits 1 on drift)
bash ~/vault/06_RUNTIME/scripts/check-proxy-drift.sh || exit 1
```

## Exit Codes

| Code | Meaning |
|------|---------|
| 0 | All proxy files identical, or only one copy found |
| 1 | Drift detected — multiple files with different hashes |

## What It Ignores

- `venv/` and `.venv/` directories
- `site-packages/` (third-party urllib3 proxy.py etc.)
- `__pycache__/`

## Current State (2026-03-29)

Running the script currently shows **4 divergent copies** — this is expected during the restructure transition:

| File | Lines | Status |
|------|-------|--------|
| `packages/proxy.py` | 5,595L | Legacy copy — to be removed in Phase 6 |
| `tokenpak/tokenpak/agent/proxy/proxy.py` | 62L | Stub/adapter — not a full copy |
| `tokenpak/tokenpak/integrations/litellm/proxy.py` | 156L | LiteLLM integration shim |
| `tokenpak/tokenpak/runtime/proxy.py` | 1,858L | Runtime entrypoint (reduced) |

Once Phase 4–6 cleanup completes and `packages/` is removed, only the restructured `proxy/server.py` should exist, and this check should pass.

## Pre-commit Hook (optional)

```bash
# .git/hooks/pre-commit
#!/bin/bash
bash ~/vault/06_RUNTIME/scripts/check-proxy-drift.sh && exit 0
echo "Commit blocked: proxy.py drift detected. Fix before committing."
exit 1
```
