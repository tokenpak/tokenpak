# TokenPak Demo Data

Quickly showcase TokenPak with realistic demo data — no need to generate real traffic.

## Usage

### Seed demo data
```bash
python3 demo.py --seed
```

Populates the database with:
- **500 requests** over 24 hours
- **70% cache hit rate** (realistic with repeated queries)
- **Multiple models** (Haiku, Sonnet, GPT-4)
- **Cost breakdown**, latency, cache efficiency
- **Labeled as "Demo Data"** for clarity

### Custom seed size
```bash
python3 demo.py --seed 1000 12  # 1000 requests over 12 hours
```

### Check demo data
```bash
python3 demo.py --check
```

Shows:
- Total requests
- Cache hit rate and percentage
- Time range
- Cost breakdown (misses vs. hits)
- Estimated savings

### Clear demo data
```bash
python3 demo.py --clear
```

Removes all demo data — safe to run before a live demo.

## Dashboard Integration

Once seeded, the TokenPak dashboard automatically displays:
- **Request timeline** (scatter plot over 24h)
- **Cache hit rate** (≈70%)
- **Cost breakdown** by model
- **Latency comparison** (cache hits ~10x faster)
- **Savings estimate** (realistic for production scenarios)

## Data Isolation

- Demo data is **labeled** internally (`agent_id: "demo-data"`)
- Real traffic **never mixes** with demo data
- Cleanup is **safe** — only removes records marked as demo
- **Idempotent** — seed multiple times without duplicates

## Acceptance Criteria ✅

- [x] `python3 demo.py --seed` populates with 500 requests
- [x] 70% cache hit rate (verified in unit tests)
- [x] Multiple models represented
- [x] Dashboard shows all key metrics
- [x] `python3 demo.py --clear` removes all demo data
- [x] No cross-contamination with real data
- [x] Idempotent (safe to run multiple times)
- [x] ≥9 passing unit tests

## Testing

Run unit tests:
```bash
python3 -m pytest test_demo.py -v
```

All 9 tests pass ✅
