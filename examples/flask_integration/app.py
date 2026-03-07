"""
Flask Integration Example
==========================
TokenPak as a Flask decorator and before_request hook.

Problem: Flask LLM apps send verbose prompts, wasting token budget.
Solution: A decorator + before_request hook that compresses text fields automatically.

Expected savings: 30-60% on prose inputs.
Setup: pip install tokenpak flask
"""

import sys
import os
import json
import functools
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from flask import Flask, request, jsonify, g

from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints


app = Flask(__name__)
_engine = HeuristicEngine()


# ---------------------------------------------------------------------------
# Decorator: compress a specific argument
# ---------------------------------------------------------------------------

def compress_input(field: str = "prompt", min_tokens: int = 20):
    """
    Decorator that compresses a named field in the JSON request body.
    
    Usage:
        @app.route("/complete", methods=["POST"])
        @compress_input(field="prompt")
        def complete():
            data = request.get_json()
            # data["prompt"] is already compressed here
    """
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            data = request.get_json(silent=True) or {}
            if field in data and isinstance(data[field], str):
                original = data[field]
                orig_tokens = len(original) // 4
                if orig_tokens >= min_tokens:
                    compressed = _engine.compact(original)
                    comp_tokens = len(compressed) // 4
                    data[field] = compressed
                    # Store stats on flask.g for handlers to access
                    g.tokenpak_original_tokens = orig_tokens
                    g.tokenpak_compressed_tokens = comp_tokens
                    g.tokenpak_savings_pct = int((1 - comp_tokens / orig_tokens) * 100)
                    # Patch request JSON
                    request._cached_json = (data, data)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


# ---------------------------------------------------------------------------
# Before-request hook: compress all known fields globally
# ---------------------------------------------------------------------------

COMPRESS_FIELDS = {"prompt", "content", "text", "query", "message"}

@app.before_request
def auto_compress():
    """
    Global hook: compress any known text fields in JSON bodies.
    Works on all routes without any decorator needed.
    """
    if request.method not in ("POST", "PUT"):
        return
    if "application/json" not in request.content_type:
        return

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return

    total_original = 0
    total_compressed = 0

    for field in COMPRESS_FIELDS:
        if field in data and isinstance(data[field], str):
            orig = data[field]
            orig_t = len(orig) // 4
            if orig_t >= 30:  # skip tiny inputs
                compressed = _engine.compact(orig)
                comp_t = len(compressed) // 4
                data[field] = compressed
                total_original += orig_t
                total_compressed += comp_t

    if total_original > 0:
        g.tokenpak_original_tokens = total_original
        g.tokenpak_compressed_tokens = total_compressed
        g.tokenpak_savings_pct = int((1 - total_compressed / total_original) * 100)
        request._cached_json = (data, data)


@app.after_request
def add_compression_headers(response):
    """Add X-TokenPak-* headers to responses when compression occurred."""
    if hasattr(g, "tokenpak_savings_pct"):
        response.headers["X-TokenPak-Original-Tokens"] = str(g.tokenpak_original_tokens)
        response.headers["X-TokenPak-Compressed-Tokens"] = str(g.tokenpak_compressed_tokens)
        response.headers["X-TokenPak-Savings-Pct"] = str(g.tokenpak_savings_pct)
    return response


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.route("/v1/complete", methods=["POST"])
@compress_input(field="prompt", min_tokens=30)  # decorator approach
def complete():
    """Completion endpoint with per-route decorator."""
    data = request.get_json()
    return jsonify({
        "prompt_tokens": len(data.get("prompt", "")) // 4,
        "prompt_preview": data.get("prompt", "")[:150],
        "savings_pct": getattr(g, "tokenpak_savings_pct", 0),
    })


@app.route("/v1/chat", methods=["POST"])
def chat():
    """Chat endpoint — uses global before_request hook."""
    data = request.get_json()
    return jsonify({
        "message_tokens": len(data.get("message", "")) // 4,
        "savings_pct": getattr(g, "tokenpak_savings_pct", 0),
    })


@app.route("/demo/compress", methods=["POST"])
def demo_compress():
    """Direct compression — useful for testing."""
    data = request.get_json()
    text = data.get("text", "")
    if not text:
        return jsonify({"error": "Provide 'text' field"}), 400

    original_tokens = len(text) // 4
    compressed = _engine.compact(text)
    compressed_tokens = len(compressed) // 4

    return jsonify({
        "original_tokens": original_tokens,
        "compressed_tokens": compressed_tokens,
        "savings_pct": int((1 - compressed_tokens / original_tokens) * 100) if original_tokens > 0 else 0,
        "compressed": compressed,
    })


@app.route("/health")
def health():
    return jsonify({"status": "ok", "compression": "active"})


if __name__ == "__main__":
    print("Starting Flask + TokenPak...")
    print("Test: curl -X POST http://localhost:5000/demo/compress -H 'Content-Type: application/json' -d '{\"text\": \"verbose text here\"}'")
    app.run(debug=True, port=5000)
