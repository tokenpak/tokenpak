# TokenPak Proxy — Production Docker Image
# Base: Python 3.11 slim (secure, minimal)
# Size target: <500MB

FROM python:3.11-slim as builder

WORKDIR /build

# Copy requirements first (Docker layer caching)
COPY requirements.txt .

# Install dependencies in a virtual environment
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip setuptools && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

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

# Health check (Docker native health check)
# Uses /ready — returns 200 only when fully initialised and accepting requests.
# Allows 60s start-period so CI images don't fail before server finishes boot.
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:${TOKENPAK_PORT}/ready || exit 1

# Start proxy (use proxy.py as entry point)
# Note: assumes proxy.py has a main() that starts the server
CMD ["python", "-m", "tokenpak.cli", "proxy", "--port", "8766"]
