"""
FastAPI Middleware Integration
==============================
TokenPak as FastAPI middleware — automatic request body compression.

Problem: LLM-facing FastAPI apps receive verbose user inputs that inflate token costs.
Solution: Middleware intercepts requests and compresses prompt fields transparently.

Expected savings: varies by input; measure in your own workflow.
Setup: pip install tokenpak fastapi uvicorn
"""

import sys
import os
import time
import json
from typing import Callable

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from fastapi import FastAPI, Request, Response
from fastapi.middleware.base import BaseHTTPMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints


# ---------------------------------------------------------------------------
# Compression Middleware
# ---------------------------------------------------------------------------

class TokenPakMiddleware(BaseHTTPMiddleware):
    """
    Middleware that compresses the 'prompt' or 'messages' fields in JSON request bodies.
    Attaches compression stats to response headers for observability.
    """

    def __init__(self, app, compress_fields: list[str] = None, min_tokens: int = 50):
        super().__init__(app)
        self.engine = HeuristicEngine()
        self.compress_fields = compress_fields or ["prompt", "content", "text", "query"]
        self.min_tokens = min_tokens  # skip compression for short inputs

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Only process JSON POST/PUT requests
        if request.method not in ("POST", "PUT") or "application/json" not in request.headers.get("content-type", ""):
            return await call_next(request)

        try:
            body_bytes = await request.body()
            body = json.loads(body_bytes)
        except Exception:
            return await call_next(request)

        original_tokens = 0
        compressed_tokens = 0

        # Compress targeted string fields
        body, original_tokens, compressed_tokens = self._compress_fields(body)

        # Rebuild request with compressed body
        compressed_bytes = json.dumps(body).encode()
        async def receive():
            return {"type": "http.request", "body": compressed_bytes}
        request = Request(request.scope, receive)

        response = await call_next(request)

        # Attach stats headers
        if original_tokens > 0:
            savings_pct = int((1 - compressed_tokens / original_tokens) * 100)
            response.headers["X-TokenPak-Original-Tokens"] = str(original_tokens)
            response.headers["X-TokenPak-Compressed-Tokens"] = str(compressed_tokens)
            response.headers["X-TokenPak-Savings-Pct"] = str(savings_pct)

        return response

    def _compress_fields(self, obj, depth=0) -> tuple:
        """Recursively compress string fields in JSON body."""
        original_tokens = 0
        compressed_tokens = 0

        if isinstance(obj, dict):
            for key, value in obj.items():
                if key in self.compress_fields and isinstance(value, str):
                    orig_t = len(value) // 4
                    if orig_t >= self.min_tokens:
                        compressed = self.engine.compact(value)
                        comp_t = len(compressed) // 4
                        obj[key] = compressed
                        original_tokens += orig_t
                        compressed_tokens += comp_t
                    else:
                        original_tokens += orig_t
                        compressed_tokens += orig_t
                elif isinstance(value, (dict, list)) and depth < 3:
                    value, ot, ct = self._compress_fields(value, depth + 1)
                    original_tokens += ot
                    compressed_tokens += ct
        elif isinstance(obj, list):
            for i, item in enumerate(obj):
                item, ot, ct = self._compress_fields(item, depth + 1)
                obj[i] = item
                original_tokens += ot
                compressed_tokens += ct

        return obj, original_tokens, compressed_tokens


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(title="TokenPak FastAPI Middleware Demo", version="1.0.0")

# Attach middleware — runs on every request
app.add_middleware(TokenPakMiddleware, compress_fields=["prompt", "content", "text", "query"])


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class CompletionRequest(BaseModel):
    prompt: str
    model: str = "gpt-4o"
    max_tokens: int = 1000


class ChatRequest(BaseModel):
    messages: list[dict]
    model: str = "gpt-4o"


class SearchRequest(BaseModel):
    query: str
    top_k: int = 10


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.post("/v1/completions")
async def create_completion(request: CompletionRequest):
    """
    Completion endpoint — prompt is compressed by middleware before reaching here.
    Check X-TokenPak-* headers on response to see savings.
    """
    return {
        "received_prompt_tokens": len(request.prompt) // 4,
        "prompt_preview": request.prompt[:200] + "..." if len(request.prompt) > 200 else request.prompt,
        "model": request.model,
        "note": "Prompt was compressed by TokenPak middleware. Check X-TokenPak-* headers.",
    }


@app.post("/v1/search")
async def search(request: SearchRequest):
    """Search endpoint — query is compressed before reaching here."""
    return {
        "received_query_tokens": len(request.query) // 4,
        "query_preview": request.query[:100],
        "top_k": request.top_k,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "middleware": "TokenPakMiddleware active"}


# ---------------------------------------------------------------------------
# Demo: standalone compression endpoint (no middleware needed)
# ---------------------------------------------------------------------------

_demo_engine = HeuristicEngine()

class DemoRequest(BaseModel):
    text: str

@app.post("/demo/compress")
async def demo_compress(request: DemoRequest):
    """Direct compression endpoint for testing."""
    original = request.text
    compressed = _demo_engine.compact(original)
    orig_tokens = len(original) // 4
    comp_tokens = len(compressed) // 4
    return {
        "original_tokens": orig_tokens,
        "compressed_tokens": comp_tokens,
        "savings_pct": round((1 - comp_tokens / orig_tokens) * 100) if orig_tokens > 0 else 0,
        "compressed": compressed,
    }


if __name__ == "__main__":
    import uvicorn
    print("Starting FastAPI + TokenPak middleware...")
    print("API docs: http://localhost:8000/docs")
    print("Try: POST /demo/compress with {'text': 'your verbose text here'}")
    uvicorn.run(app, host="0.0.0.0", port=8000)
