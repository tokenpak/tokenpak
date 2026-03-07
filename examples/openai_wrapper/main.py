"""
OpenAI API Wrapper with TokenPak Compression
=============================================
A drop-in wrapper around the OpenAI client that automatically compresses
messages before sending, reducing token costs transparently.

Problem: OpenAI charges per token. Verbose prompts cost more.
Solution: Intercept messages before they reach the API, compress them,
          and track savings automatically.

Expected savings: 30-60% on typical prompts.
Setup: pip install tokenpak openai
"""

import sys
import os
import hashlib
from typing import Optional, Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tokenpak import HeuristicEngine, CacheManager
from tokenpak.engines.base import CompactionHints


# ---------------------------------------------------------------------------
# TokenPak-Enhanced OpenAI Client (wrapper pattern)
# ---------------------------------------------------------------------------

class _ChatCompletions:
    """Inner proxy for client.chat.completions.create()."""

    def __init__(self, wrapper: "TokenPakOpenAI"):
        self._wrapper = wrapper

    def create(self, messages: list[dict], model: str = "gpt-4o", **kwargs) -> Any:
        """Drop-in for openai.chat.completions.create(). Compresses before calling."""
        self._wrapper._stats["calls"] += 1

        if self._wrapper.verbose:
            print(f"\n[TokenPak] Compressing {len(messages)} messages...")

        compressed = self._wrapper._prepare_messages(messages)

        if self._wrapper.verbose:
            before = sum(len(m.get("content",""))//4 for m in messages)
            after = sum(len(m.get("content",""))//4 for m in compressed)
            print(f"[TokenPak] {before} → {after} tokens "
                  f"({max(0,1-after/max(1,before)):.0%} savings)\n")

        if self._wrapper._client is None:
            # Dry-run mode
            return {
                "choices": [{"message": {"role": "assistant", "content": "[dry-run]"}}],
                "_tokenpak": {"compressed_messages": compressed},
            }

        return self._wrapper._client.chat.completions.create(
            model=model,
            messages=compressed,
            **kwargs,
        )


class _ChatNamespace:
    """Mirrors openai.OpenAI().chat interface."""

    def __init__(self, wrapper: "TokenPakOpenAI"):
        self.completions = _ChatCompletions(wrapper)


class TokenPakOpenAI:
    """
    Drop-in replacement for the OpenAI client with automatic compression.

    Usage:
        # Before:
        client = OpenAI(api_key="...")

        # After (one line change):
        client = TokenPakOpenAI(api_key="...", target_tokens=1000)

        # API is identical
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": "Hello!"}]
        )
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        target_tokens: int = 1000,
        compress_system: bool = False,
        keep_recent_turns: int = 3,
        verbose: bool = False,
    ):
        """
        Args:
            api_key: OpenAI API key (falls back to OPENAI_API_KEY env var)
            target_tokens: Max tokens per message after compression
            compress_system: Whether to compress system prompts (default: no)
            keep_recent_turns: Recent turns to preserve without compression
            verbose: Print compression stats on each call
        """
        try:
            from openai import OpenAI
            self._client = OpenAI(api_key=api_key or os.environ.get("OPENAI_API_KEY"))
        except ImportError:
            self._client = None
            print("⚠️  openai package not installed — running in dry-run mode")

        self.engine = HeuristicEngine()
        self.cache = CacheManager(default_ttl=300)
        self.target_tokens = target_tokens
        self.compress_system = compress_system
        self.keep_recent_turns = keep_recent_turns
        self.verbose = verbose

        self._stats = {"calls": 0, "tokens_saved": 0, "cache_hits": 0}

        # Mirrors openai client interface
        self.chat = _ChatNamespace(self)

    def _compress_message(self, msg: dict) -> dict:
        """Compress a single message, with caching."""
        role = msg.get("role", "user")
        content = msg.get("content", "")

        if not content or (role == "system" and not self.compress_system):
            return msg

        cache_key = hashlib.sha256(content.encode()).hexdigest()[:20]
        hit, cached = self.cache.get(cache_key)
        if hit:
            self._stats["cache_hits"] += 1
            return {**msg, "content": cached}

        hints = CompactionHints(
            target_tokens=self.target_tokens,
            keep_headers=True,
            keep_code_blocks=True,
        )
        compressed = self.engine.compact(content, hints)

        saved = max(0, len(content) // 4 - len(compressed) // 4)
        self._stats["tokens_saved"] += saved

        self.cache.set(cache_key, compressed, ttl=300)

        if self.verbose:
            print(f"  [{role}] {len(content)//4} → {len(compressed)//4} tokens "
                  f"({saved} saved)")

        return {**msg, "content": compressed}

    def _prepare_messages(self, messages: list[dict]) -> list[dict]:
        """Apply sliding-window compression to a message list."""
        system_msgs = [m for m in messages if m.get("role") == "system"]
        non_system = [m for m in messages if m.get("role") != "system"]

        keep_count = self.keep_recent_turns * 2
        recent = non_system[-keep_count:] if len(non_system) > keep_count else non_system
        older = non_system[:-keep_count] if len(non_system) > keep_count else []

        compressed_system = [self._compress_message(m) for m in system_msgs]
        compressed_older = [self._compress_message(m) for m in older]

        return compressed_system + compressed_older + recent

    @property
    def stats(self) -> dict:
        """Return cumulative compression statistics."""
        return dict(self._stats)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def run_demo():
    """Show the TokenPak OpenAI wrapper in action."""
    print("=== OpenAI Wrapper with TokenPak Compression ===\n")

    client = TokenPakOpenAI(
        target_tokens=200,
        keep_recent_turns=2,
        verbose=True,
    )

    messages = [
        {
            "role": "system",
            "content": "You are a helpful assistant specializing in software engineering.",
        },
        {
            "role": "user",
            "content": (
                "I have been trying to understand the concept of dependency injection "
                "in software design. Could you please explain the concept in detail, "
                "including what problems it solves and how it differs from traditional "
                "approaches to managing dependencies between components?"
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Dependency injection is a design pattern where a class receives its "
                "dependencies from external sources rather than creating them itself. "
                "Instead of a class instantiating its own dependencies using 'new', "
                "those dependencies are 'injected' from outside. This promotes loose "
                "coupling, making code easier to test, maintain, and extend. It also "
                "enables you to swap implementations without changing the dependent class."
            ),
        },
        {
            "role": "user",
            "content": "Can you show me a concrete Python example?",
        },
    ]

    response = client.chat.completions.create(
        messages=messages,
        model="gpt-4o",
    )

    print("Stats:", client.stats)
    print("\n✅ OpenAI wrapper demo complete!")
    print("   (Set OPENAI_API_KEY env var for live API calls)")


if __name__ == "__main__":
    run_demo()
