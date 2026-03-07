"""
Vector Store Context Compression
==================================
Compress retrieved document chunks before embedding or LLM injection.

Problem: RAG pipelines retrieve verbose chunks that inflate context windows.
Solution: Compress each chunk before injecting into the prompt, preserving semantics.

Expected savings: 35-55% on typical retrieved documents.
Setup: pip install tokenpak
"""

import sys
import os
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tokenpak import HeuristicEngine
from tokenpak.engines.base import CompactionHints


engine = HeuristicEngine()


# Simulated vector store chunks (what you'd get from FAISS/Pinecone/Weaviate)
RETRIEVED_CHUNKS = [
    {
        "id": "doc-001-chunk-3",
        "score": 0.94,
        "text": """
        The authentication system in this application is implemented using JSON Web Tokens (JWT).
        When a user successfully logs in with their credentials, the server generates a signed JWT
        that contains the user's ID, roles, and an expiration timestamp. This token is returned to
        the client and should be stored securely. For subsequent requests, the client includes this
        token in the Authorization header using the Bearer scheme. The server validates the token's
        signature and expiry on each request. It is important to note that tokens should not be stored
        in localStorage due to XSS vulnerabilities. HttpOnly cookies are the recommended storage mechanism.
        """,
    },
    {
        "id": "doc-002-chunk-1",
        "score": 0.91,
        "text": """
        Rate limiting is enforced at the API gateway level to protect backend services from overload.
        The current configuration allows 1000 requests per hour per API key for standard tier users,
        and 10000 requests per hour for enterprise tier users. When a client exceeds this limit, the
        API returns a 429 Too Many Requests response with a Retry-After header indicating when the
        client may retry. It is strongly recommended that all API clients implement exponential backoff
        with jitter to avoid thundering herd scenarios when rate limits are hit. The rate limit window
        resets on a rolling basis, not at a fixed time boundary.
        """,
    },
    {
        "id": "doc-003-chunk-7",
        "score": 0.88,
        "text": """
        Database connection pooling is handled by SQLAlchemy's built-in pool manager. The pool is
        configured with a minimum of 5 connections and a maximum of 20 connections per service instance.
        Connections that have been idle for more than 30 minutes are automatically recycled to prevent
        stale connection issues. In high-traffic scenarios, if all pool connections are in use, new
        requests will wait up to 30 seconds before raising a PoolTimeout exception. Connection health
        checks run every 60 seconds to detect and remove broken connections from the pool.
        """,
    },
]


def compress_rag_context(chunks: list[dict], token_budget: int = 500) -> dict:
    """
    Compress retrieved chunks to fit within a token budget.
    
    Strategy:
    1. Sort by relevance score (highest first)
    2. Compress each chunk
    3. Add chunks until budget is exhausted
    """
    print(f"  Token budget: {token_budget} tokens")
    print(f"  Retrieved chunks: {len(chunks)}\n")

    compressed_chunks = []
    total_original_tokens = 0
    total_compressed_tokens = 0
    tokens_used = 0

    for chunk in sorted(chunks, key=lambda c: c["score"], reverse=True):
        original_tokens = len(chunk["text"]) // 4
        compressed_text = engine.compact(chunk["text"].strip())
        compressed_tokens = len(compressed_text) // 4

        total_original_tokens += original_tokens
        total_compressed_tokens += compressed_tokens

        if tokens_used + compressed_tokens <= token_budget:
            compressed_chunks.append({
                "id": chunk["id"],
                "score": chunk["score"],
                "text": compressed_text,
                "tokens": compressed_tokens,
            })
            tokens_used += compressed_tokens
            status = "✅ included"
        else:
            status = "⚠️  skipped (budget)"

        print(f"  [{chunk['id']}] score={chunk['score']:.2f} | {original_tokens}→{compressed_tokens} tokens | {status}")

    savings_pct = (1 - total_compressed_tokens / total_original_tokens) * 100
    print(f"\n  Total: {total_original_tokens}→{total_compressed_tokens} tokens ({savings_pct:.0f}% saved)")
    print(f"  Context used: {tokens_used}/{token_budget} tokens")
    print(f"  Chunks included: {len(compressed_chunks)}/{len(chunks)}\n")

    return {
        "chunks": compressed_chunks,
        "tokens_used": tokens_used,
        "token_budget": token_budget,
        "original_tokens": total_original_tokens,
        "compressed_tokens": total_compressed_tokens,
        "savings_pct": savings_pct,
    }


def build_rag_prompt(query: str, context_result: dict) -> str:
    """Build the final RAG prompt from compressed context."""
    context_blocks = "\n\n".join(
        f"[Source: {c['id']} | Relevance: {c['score']:.2f}]\n{c['text']}"
        for c in context_result["chunks"]
    )

    prompt = f"""Answer the following question using only the provided context.

Context ({context_result['tokens_used']} tokens):
{context_blocks}

Question: {query}

Answer:"""
    return prompt


def main():
    print("=== Vector Store Context Compression ===\n")

    query = "How should I handle authentication tokens and rate limits?"

    print("📥 Compressing retrieved chunks...")
    result = compress_rag_context(RETRIEVED_CHUNKS, token_budget=400)

    print("📝 Building RAG prompt...\n")
    prompt = build_rag_prompt(query, result)
    prompt_tokens = len(prompt) // 4

    print(f"  Final prompt: ~{prompt_tokens} tokens")
    print(f"  Without compression would have been: ~{(result['original_tokens'] + len(query)//4 + 50)} tokens")
    print(f"\n--- Prompt Preview (first 500 chars) ---")
    print(prompt[:500] + "...")


if __name__ == "__main__":
    main()
