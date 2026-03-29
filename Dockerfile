# TokenPak Proxy — Official Production Docker Image
# Multi-stage build: minimal final image, non-root user, health check
# Target size: <200MB

# ============================================================
# Stage 1: Builder — install dependencies into a venv
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /build

# Install build deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (layer caching — only re-runs on requirement changes)
COPY requirements.txt .

# Install into isolated venv
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip setuptools wheel && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ============================================================
# Stage 2: Runtime — minimal image, no build tools
# ============================================================
FROM python:3.11-slim

WORKDIR /app

# Install curl for the HEALTHCHECK command (minimal, no extras)
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user (security: never run as root)
RUN useradd -m -u 1000 tokenpak

# Copy venv from builder (no pip needed at runtime)
COPY --from=builder /opt/venv /opt/venv

# Copy application source (owned by tokenpak user)
COPY --chown=tokenpak:tokenpak . .

# Environment variables
# TOKENPAK_PORT     — proxy listen port (default: 8766)
# TOKENPAK_LOG_LEVEL — log verbosity: debug | info | warning | error (default: info)
# TOKENPAK_CONFIG   — path to config JSON (optional, uses defaults if unset)
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TOKENPAK_PORT=8766 \
    TOKENPAK_LOG_LEVEL=info

# Expose default proxy port
EXPOSE 8766

# Health check — polls the /health endpoint every 30s
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${TOKENPAK_PORT}/health || exit 1

# Switch to non-root user before starting
USER tokenpak

# Start the TokenPak proxy
CMD ["python", "-m", "tokenpak.cli", "proxy", "--port", "8766"]
