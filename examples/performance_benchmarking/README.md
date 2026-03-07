# Performance Benchmarking Example

**Problem:** You need to know if TokenPak is worth it for your specific data and cost model.

**Solution:** A benchmark suite that measures compression speed, token savings by content type, and projects actual dollar savings.

## What This Shows

- Token savings across 4 content types (prose, docs, chat, code)
- Latency percentiles (mean, p95, p99) and throughput (req/s)
- Cost projection for GPT-4o, Claude 3.5 Sonnet, GPT-4o-mini
- Scaling behavior as input size grows

## Setup

```bash
pip install -r requirements.txt
python main.py
```

## Expected Output

```
📊 Token Savings by Content Type
  prose              ~110     ~55       50%
  technical_docs     ~120     ~65       46%
  chat_history       ~180     ~95       47%
  code_comments       ~90     ~50       44%

⚡ Compression Speed (100 iterations)
  Mean latency:   0.85ms
  Throughput:     1,176 req/s

💰 Cost Projection (100M tokens/month)
  gpt-4o         $250.00   $135.00   $115.00   $1,380.00/yr
```

## Adapting for Your Data

Replace `SAMPLES` dict with your actual data types. Run `project_cost_savings()` with your real monthly token volume.

```python
my_savings = measure_token_savings(engine, my_real_text)
cost = project_cost_savings(my_savings["savings_pct"], monthly_tokens=50_000_000)
```
