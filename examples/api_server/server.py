"""
API Server Integration Example
================================
A lightweight FastAPI server that compresses incoming text before sending
to an LLM, reducing token costs automatically.

Problem: Every token sent to the LLM API costs money. Verbose inputs waste budget.
Solution: Intercept requests, compress content with TokenPak, forward to LLM.

Expected savings: 30-60% on typical prose inputs.
Setup: pip install tokenpak fastapi uvicorn httpx
"""

import sys
import os
import hashlib
import time
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from tokenpak import HeuristicEngine, CacheManager
from tokenpak.engines.base import CompactionHints


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(
    title="TokenPak Compression Proxy",
    description="Compresses LLM prompts before forwarding to reduce token costs",
    version="1.0.0",
)

engine = HeuristicEngine()
cache = CacheManager(default_ttl=300)  # Cache compressed results for 5 min

# Track stats
stats = {"requests": 0, "tokens_saved": 0, "cache_hits": 0}


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class CompressRequest(BaseModel):
    text: str
    target_tokens: Optional[int] = None
    cache: bool = True


class CompressResponse(BaseModel):
    original_text: str
    compressed_text: str
    original_tokens: int
    compressed_tokens: int
    savings_pct: float
    cache_hit: bool
    elapsed_ms: float


class ConversationCompressRequest(BaseModel):
    messages: list[dict]  # [{"role": str, "content": str}]
    keep_recent: int = 3
    target_tokens: int = 4000


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/health")
def health():
    """Health check endpoint."""
    return {"status": "ok", "stats": stats}


@app.post("/compress", response_model=CompressResponse)
def compress_text(req: CompressRequest):
    """
    Compress a single text block.

    Returns the compressed version with token counts and savings percentage.
    """
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="text cannot be empty")

    start = time.perf_counter()
    cache_hit = False

    # Check cache
    cache_key = hashlib.sha256(req.text.encode()).hexdigest()[:16]
    if req.cache:
        hit, cached = cache.get(cache_key)
        if hit:
            compressed = cached
            cache_hit = True
            stats["cache_hits"] += 1

    if not cache_hit:
        hints = None
        if req.target_tokens:
            hints = CompactionHints(target_tokens=req.target_tokens)
        compressed = engine.compact(req.text, hints)

        if req.cache:
            cache.set(cache_key, compressed, ttl=300)

    elapsed_ms = (time.perf_counter() - start) * 1000

    original_tokens = max(1, len(req.text) // 4)
    compressed_tokens = max(1, len(compressed) // 4)
    savings = max(0.0, 1 - compressed_tokens / original_tokens)

    stats["requests"] += 1
    stats["tokens_saved"] += original_tokens - compressed_tokens

    return CompressResponse(
        original_text=req.text,
        compressed_text=compressed,
        original_tokens=original_tokens,
        compressed_tokens=compressed_tokens,
        savings_pct=round(savings * 100, 1),
        cache_hit=cache_hit,
        elapsed_ms=round(elapsed_ms, 2),
    )


@app.post("/compress/conversation")
def compress_conversation(req: ConversationCompressRequest):
    """
    Compress a conversation history to fit a token budget.

    Keeps recent turns intact, compresses older turns.
    """
    messages = req.messages
    if not messages:
        raise HTTPException(status_code=400, detail="messages cannot be empty")

    total_before = sum(len(m.get("content", "")) // 4 for m in messages)

    # Keep recent turns, compress older ones
    keep_count = req.keep_recent * 2
    system_msgs = [m for m in messages if m.get("role") == "system"]
    non_system = [m for m in messages if m.get("role") != "system"]

    recent = non_system[-keep_count:] if len(non_system) > keep_count else non_system
    older = non_system[:-keep_count] if len(non_system) > keep_count else []

    compressed_older = []
    for msg in older:
        content = msg.get("content", "")
        hints = CompactionHints(target_tokens=max(50, len(content) // 8))
        compressed_content = engine.compact(content, hints)
        compressed_older.append({**msg, "content": compressed_content})

    result = system_msgs + compressed_older + recent
    total_after = sum(len(m.get("content", "")) // 4 for m in result)

    return {
        "messages": result,
        "stats": {
            "original_tokens": total_before,
            "compressed_tokens": total_after,
            "savings_pct": round(max(0, 1 - total_after / max(1, total_before)) * 100, 1),
            "turns_compressed": len(older),
            "turns_kept_intact": len(recent),
        },
    }


@app.get("/stats")
def get_stats():
    """Return compression statistics."""
    return stats


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    print("Starting TokenPak compression server on http://localhost:8000")
    print("API docs: http://localhost:8000/docs")
    uvicorn.run(app, host="0.0.0.0", port=8000)
