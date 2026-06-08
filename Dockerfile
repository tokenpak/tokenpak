# TokenPak Proxy — Production Docker Image
# Base: Python 3.11 slim (secure, minimal)
# Size target: <500MB

FROM python:3.11-slim as builder

WORKDIR /build

# Copy package files first (Docker layer caching)
COPY pyproject.toml README.md LICENSE ./
COPY tokenpak/ tokenpak/

# Install in a virtual environment
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip setuptools && \
    /opt/venv/bin/pip install --no-cache-dir ".[serve]"

# ============================================
# Final stage (multi-stage build for size)
# ============================================

FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Create non-root user (security best practice)
RUN useradd -m -u 1000 tokenpak

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Copy application code
COPY --chown=tokenpak:tokenpak . .

# Set environment to use venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TOKENPAK_PORT=8766

# Expose default port
EXPOSE 8766

# Switch to non-root user
USER tokenpak

# Health check — uses Python (curl not available in slim image)
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:${TOKENPAK_PORT}/health', timeout=5)" || exit 1

# Start proxy — reads port from TOKENPAK_PORT env var
CMD ["python", "-m", "tokenpak.cli", "serve"]
