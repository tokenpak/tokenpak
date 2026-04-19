# Recipe: Cost Monitoring & Observability

**What this solves:** Export TokenPak usage and cost metrics to Prometheus or Grafana for real-time dashboards and alerts on spending trends.

## Prerequisites
- TokenPak installed
- Prometheus or Grafana running (local or cloud)
- API keys for providers
- Understanding of metrics (requests, tokens, cost)

## Config Snippet

```yaml
# config.yaml
metrics:
  enabled: true

  # Export to Prometheus
  prometheus:
    enabled: true
    port: 8001  # Metrics endpoint: http://localhost:8001/metrics
    push_interval_seconds: 60  # Push to Prometheus every 60s

  # Dimensions to track
  track_by:
    - user_id
    - model
    - provider
    - endpoint
    - status_code

  # Custom metrics
  custom_metrics:
    - name: tokenpak_cost_by_user_daily
      type: gauge
      dimension: user_id
      period: 1d

    - name: tokenpak_tokens_per_request
      type: histogram
      buckets: [100, 250, 500, 1000, 2000, 5000, 10000]

    - name: tokenpak_latency_by_provider
      type: histogram
      dimension: provider
      buckets: [10, 50, 100, 250, 500, 1000, 2000]

providers:
  openai:
    type: openai
    api_key: ${OPENAI_API_KEY}

  anthropic:
    type: anthropic
    api_key: ${ANTHROPIC_API_KEY}

models:
  gpt-4: { provider: openai, cost_per_1k_input: 3, cost_per_1k_output: 6 }
  gpt-3.5-turbo: { provider: openai, cost_per_1k_input: 0.5, cost_per_1k_output: 1.5 }
  claude-opus: { provider: anthropic, cost_per_1k_input: 15, cost_per_1k_output: 75 }
```

**Prometheus scrape config (prometheus.yml):**
```yaml
global:
  scrape_interval: 15s

scrape_configs:
  - job_name: tokenpak
    static_configs:
      - targets: ['localhost:8001']
```

**Grafana dashboard example:**
```json
{
  "dashboard": {
    "title": "TokenPak Cost Monitoring",
    "panels": [
      {
        "title": "Daily Cost by User",
        "targets": [
          {
            "expr": "tokenpak_cost_by_user_daily",
            "legendFormat": "{{ user_id }}"
          }
        ],
        "type": "graph"
      },
      {
        "title": "Total Spend (24h)",
        "targets": [
          {
            "expr": "sum(increase(tokenpak_cost_usd[24h]))"
          }
        ],
        "type": "stat"
      },
      {
        "title": "Requests by Model",
        "targets": [
          {
            "expr": "sum(rate(tokenpak_requests_total[5m])) by (model)"
          }
        ],
        "type": "piechart"
      }
    ]
  }
}
```

## Test & Verify

**Step 1:** Start Prometheus and Grafana locally:
```bash
# Start Prometheus (example, adjust path)
prometheus --config.file=prometheus.yml &

# Start Grafana (Docker example)
docker run -p 3000:3000 grafana/grafana &
```

**Step 2:** Start TokenPak proxy:
```bash
tokenpak proxy --config config.yaml
# Metrics available at: http://localhost:8001/metrics
```

**Step 3:** Make some requests to generate metrics:
```bash
for i in {1..10}; do
  curl -X POST http://localhost:8000/v1/messages \
    -H "Authorization: Bearer user-$((i % 3))" \
    -d '{"model": "gpt-4", "messages": [{"role": "user", "content": "Hello"}]}' \
    -s > /dev/null &
done
wait
```

**Step 4:** Verify metrics are being exported:
```bash
curl -s http://localhost:8001/metrics | grep tokenpak
# Expected output:
# tokenpak_requests_total{model="gpt-4",provider="openai",status="200"} 10
# tokenpak_cost_usd_total{model="gpt-4",provider="openai"} 0.45
# tokenpak_tokens_sent_total{model="gpt-4"} 245
# tokenpak_tokens_received_total{model="gpt-4"} 1203
# tokenpak_latency_seconds_bucket{le="0.05",provider="openai"} 2
# tokenpak_latency_seconds_bucket{le="0.1",provider="openai"} 8
# tokenpak_latency_seconds_bucket{le="+Inf",provider="openai"} 10
```

**Step 5:** View in Grafana dashboard:
- Log in to Grafana (http://localhost:3000, default creds admin/admin)
- Add Prometheus data source (http://localhost:9090)
- Create a panel with query: `tokenpak_cost_by_user_daily`
- Expected: Gauge showing per-user cost

**Step 6:** Set up cost alerting rule:
```yaml
# alert-rules.yaml (Prometheus)
groups:
  - name: tokenpak_alerts
    rules:
      - alert: HighDailyCost
        expr: sum(increase(tokenpak_cost_usd[24h])) > 50
        for: 5m
        annotations:
          summary: "Daily TokenPak cost exceeded $50"
          description: "Current 24h spend: {{ $value }}"

      - alert: UnusualLatency
        expr: tokenpak_latency_seconds > 5
        for: 2m
        annotations:
          summary: "TokenPak requests slower than 5s (unusual)"
          description: "Provider: {{ $labels.provider }}, latency: {{ $value }}s"
```

## What Just Happened

TokenPak continuously exported metrics in Prometheus text format:

1. **Each request** increments counters: `tokenpak_requests_total`
2. **Cost calculated** based on tokens and model pricing, exported as `tokenpak_cost_usd_total`
3. **Latencies recorded** in histogram buckets for percentile analysis
4. **Prometheus scrapes** metrics every 15 seconds (configurable)
5. **Grafana queries** Prometheus and renders dashboards
6. **Alerts fire** when thresholds (e.g., daily cost > $50) are exceeded

You have a real-time view of what you're spending and where — no manual billing exports, no surprise invoices.

## Common Pitfalls

**Pitfall 1: Metrics are high-cardinality**
- ❌ Wrong: Track every user_id separately (1000s of series = Prometheus overload)
- ✅ Right: Aggregate by tier: `track_by: [user_tier, model]` (10s of series)

**Pitfall 2: Scrape interval too frequent**
- ❌ Wrong: `scrape_interval: 1s` (high overhead, no additional insight)
- ✅ Right: `scrape_interval: 15s` - 30s (captures trends, manageable load)

**Pitfall 3: Alert thresholds are arbitrary**
- ❌ Wrong: Alert at $100/day (but you budgeted $500)
- ✅ Right: Alert at 75% of your daily budget: `> (daily_budget * 0.75)`

**Pitfall 4: Missing cost dimension**
- ❌ Wrong: Only track request counts (requests ≠ cost)
- ✅ Right: Always export cost alongside requests: `tokenpak_cost_usd_total`

**Pitfall 5: No long-term retention**
- ❌ Wrong: Prometheus default 15d retention (trends disappear)
- ✅ Right: Push metrics to long-term storage: `external_write` → S3, InfluxDB, or Datadog

**Pitfall 6: Forgetting user dimension**
- ❌ Wrong: Only per-model cost (don't know if one user is abusing service)
- ✅ Right: Always include `user_id` or `user_tier` dimension for billing audits
