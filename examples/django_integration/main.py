"""
Django / FastAPI Integration Template
======================================
A middleware pattern for automatically compressing LLM prompts in web apps.

Problem: Your Django or FastAPI app sends user input directly to LLM APIs,
wasting tokens on verbose or repetitive content.

Solution: Add a TokenPak compression layer as middleware or a service class.
This template shows both Django middleware and a reusable service pattern.

Setup: pip install tokenpak django  (or fastapi)
"""

import sys
import os
import hashlib
from typing import Optional

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tokenpak import HeuristicEngine, CacheManager
from tokenpak.engines.base import CompactionHints


# ---------------------------------------------------------------------------
# Reusable Compression Service (works with any web framework)
# ---------------------------------------------------------------------------

class LLMCompressionService:
    """
    Drop-in compression service for LLM-integrated web apps.

    Usage:
        service = LLMCompressionService(target_tokens=800)
        prompt = service.prepare_prompt(user_input, system_context)
        response = openai.chat.completions.create(messages=prompt)
    """

    def __init__(
        self,
        target_tokens: int = 1000,
        cache_ttl: int = 300,
        keep_recent_turns: int = 3,
    ):
        self.engine = HeuristicEngine()
        self.cache = CacheManager(default_ttl=cache_ttl)
        self.target_tokens = target_tokens
        self.keep_recent_turns = keep_recent_turns
        self._total_saved = 0

    def compress(self, text: str, cache: bool = True) -> tuple[str, bool]:
        """
        Compress text with optional caching.

        Returns:
            (compressed_text, was_cache_hit)
        """
        cache_key = hashlib.sha256(text.encode()).hexdigest()[:20]

        if cache:
            hit, cached = self.cache.get(cache_key)
            if hit:
                return cached, True

        hints = CompactionHints(
            target_tokens=self.target_tokens,
            keep_headers=True,
            keep_code_blocks=True,
        )
        compressed = self.engine.compact(text, hints)

        saved = max(0, len(text) // 4 - len(compressed) // 4)
        self._total_saved += saved

        if cache:
            self.cache.set(cache_key, compressed, ttl=300)

        return compressed, False

    def prepare_messages(
        self,
        messages: list[dict],
        system_prompt: Optional[str] = None,
    ) -> list[dict]:
        """
        Prepare a message list for the LLM with compression applied.

        - System prompt is never compressed
        - Last N turns are kept intact
        - Older turns are compressed
        """
        result = []

        if system_prompt:
            result.append({"role": "system", "content": system_prompt})

        # Split recent vs older messages
        keep_count = self.keep_recent_turns * 2
        recent = messages[-keep_count:] if len(messages) > keep_count else messages
        older = messages[:-keep_count] if len(messages) > keep_count else []

        # Compress older messages
        for msg in older:
            content = msg.get("content", "")
            compressed, _ = self.compress(content)
            result.append({**msg, "content": compressed})

        result.extend(recent)
        return result

    @property
    def total_tokens_saved(self) -> int:
        """Cumulative tokens saved across all compress() calls."""
        return self._total_saved


# ---------------------------------------------------------------------------
# Django Middleware (pseudo-code — shows the integration pattern)
# ---------------------------------------------------------------------------

class TokenPakMiddleware:
    """
    Django middleware that compresses LLM prompts before they leave your app.

    Add to settings.py:
        MIDDLEWARE = [
            ...
            'myapp.middleware.TokenPakMiddleware',
        ]
    """

    def __init__(self, get_response):
        self.get_response = get_response
        self.service = LLMCompressionService(target_tokens=800)

    def __call__(self, request):
        # Compress any LLM-bound request body
        if (
            request.path.startswith("/api/chat")
            and request.method == "POST"
        ):
            import json
            try:
                body = json.loads(request.body)
                if "messages" in body:
                    body["messages"] = self.service.prepare_messages(body["messages"])
                    # Patch request body (Django doesn't support this directly —
                    # in practice, use a view decorator or service layer)
            except (json.JSONDecodeError, KeyError):
                pass

        return self.get_response(request)


# ---------------------------------------------------------------------------
# FastAPI Dependency (clean integration with FastAPI's dependency injection)
# ---------------------------------------------------------------------------

def get_compression_service() -> LLMCompressionService:
    """
    FastAPI dependency for injecting the compression service.

    Usage in a route:
        @app.post("/chat")
        def chat(req: ChatRequest, svc: LLMCompressionService = Depends(get_compression_service)):
            messages = svc.prepare_messages(req.messages, req.system_prompt)
            ...
    """
    return LLMCompressionService(target_tokens=1000)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def run_demo():
    """Show the compression service in action."""
    print("=== Django/FastAPI Integration Demo ===\n")

    service = LLMCompressionService(target_tokens=500)

    # Simulate a chat with system prompt
    system_prompt = "You are a helpful customer support agent for an e-commerce platform."

    messages = [
        {
            "role": "user",
            "content": (
                "I have a question about my order. I placed an order approximately "
                "three days ago and I have not yet received any shipping confirmation "
                "email. The order number is 12345. Could you please help me understand "
                "what the current status of my order might be and when I can expect "
                "to receive my items? I would greatly appreciate your assistance."
            ),
        },
        {
            "role": "assistant",
            "content": (
                "I understand your concern about your order #12345. Let me look into this "
                "for you right away. Orders typically ship within 2-3 business days, and "
                "you should receive a shipping confirmation email once your order has been "
                "dispatched. I can see your order is currently being processed in our "
                "warehouse. You should receive tracking information within the next 24 hours."
            ),
        },
        {"role": "user", "content": "Thank you! Can I change my shipping address?"},
    ]

    prepared = service.prepare_messages(messages, system_prompt)

    print(f"Original messages:   {len(messages)}")
    print(f"Prepared messages:   {len(prepared)}")
    print(f"Tokens saved so far: {service.total_tokens_saved}\n")

    for msg in prepared:
        role = msg["role"]
        tokens = len(msg["content"]) // 4
        print(f"  [{role}] ~{tokens} tokens: {msg['content'][:60]}...")

    # Second call — same content should hit cache
    compressed1, hit1 = service.compress(messages[0]["content"])
    compressed2, hit2 = service.compress(messages[0]["content"])
    print(f"\nFirst compress:  cache_hit={hit1}")
    print(f"Second compress: cache_hit={hit2} ✅ (instant!)")


if __name__ == "__main__":
    run_demo()
    print("\n✅ Django/FastAPI integration demo complete!")
