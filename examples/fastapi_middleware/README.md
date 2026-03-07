# FastAPI Middleware Integration

**Problem:** LLM-facing FastAPI apps receive verbose user inputs that inflate token costs with every request.

**Solution:** `TokenPakMiddleware` intercepts JSON request bodies and compresses `prompt`, `content`, `text`, and `query` fields transparently — zero changes to route handlers.

## What This Shows

- Custom `BaseHTTPMiddleware` that compresses request bodies
- Selective field targeting (only compresses fields you specify)
- Minimum token threshold (skips tiny inputs)
- `X-TokenPak-*` response headers for observability

## Setup

```bash
pip install -r requirements.txt
python app.py
```

Visit http://localhost:8000/docs for the interactive API.

## Key Pattern

```python
from fastapi import FastAPI
from fastapi.middleware.base import BaseHTTPMiddleware

class TokenPakMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, compress_fields=None):
        super().__init__(app)
        self.engine = HeuristicEngine()
        self.compress_fields = compress_fields or ["prompt", "content"]

    async def dispatch(self, request, call_next):
        # ... compress request body ...
        return await call_next(request)

app = FastAPI()
app.add_middleware(TokenPakMiddleware, compress_fields=["prompt", "text"])
```

## Testing

```bash
curl -X POST http://localhost:8000/demo/compress \
  -H "Content-Type: application/json" \
  -d '{"text": "This is a very verbose text that contains a lot of redundant information that could be removed without losing meaning..."}'
```

## Response Headers

```
X-TokenPak-Original-Tokens: 48
X-TokenPak-Compressed-Tokens: 22
X-TokenPak-Savings-Pct: 54
```
