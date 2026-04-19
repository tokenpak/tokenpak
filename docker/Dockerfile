# Build stage
FROM python:3.11-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --upgrade pip && \
    pip install -r requirements.txt

# Runtime stage
FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /build /app
COPY . .
EXPOSE 8766
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s CMD curl -f http://localhost:8766/health || exit 1
STOPSIGNAL SIGTERM
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "tokenpak.proxy.server"]
