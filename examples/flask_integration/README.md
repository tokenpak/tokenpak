# Flask Integration Example

**Problem:** Flask LLM apps send verbose prompts to expensive APIs.

**Solution:** Two patterns: a per-route `@compress_input` decorator and a global `before_request` hook.

## What This Shows

- `@compress_input(field="prompt")` decorator for targeted compression
- `before_request` hook for app-wide automatic compression
- `after_request` hook adding `X-TokenPak-*` headers
- Zero changes to existing route logic

## Setup

```bash
pip install -r requirements.txt
python app.py
```

## Patterns

### Option 1: Per-Route Decorator

```python
@app.route("/complete", methods=["POST"])
@compress_input(field="prompt", min_tokens=30)
def complete():
    data = request.get_json()
    # data["prompt"] is already compressed
    savings = getattr(g, "tokenpak_savings_pct", 0)
```

### Option 2: Global Hook (zero decorator needed)

```python
@app.before_request
def auto_compress():
    data = request.get_json(silent=True)
    for field in {"prompt", "content", "text"}:
        if field in data:
            data[field] = engine.compact(data[field])
```

## Test

```bash
curl -X POST http://localhost:5000/demo/compress \
  -H "Content-Type: application/json" \
  -d '{"text": "In conclusion, the aforementioned evidence suggests, in the overwhelming majority of studied cases, that the proposed solution leads to improvements..."}'
```
