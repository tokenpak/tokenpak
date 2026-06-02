"""
Claude (Anthropic) API Integration
=====================================
Compress messages before sending to Anthropic's Claude API.

Problem: Claude's API charges per token. Long conversation histories
         with verbose messages burn budget unnecessarily.

Solution: TokenPak wrapper for the Anthropic client that compresses
          older messages while preserving recent context.

Expected savings: varies by input; measure in your own workflow.
Setup: pip install tokenpak anthropic
"""

import sys
import os
import hashlib
from typing import Optional, Any

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tokenpak import HeuristicEngine, CacheManager
from tokenpak.engines.base import CompactionHints


def estimate_tokens(text: str) -> int:
    """Rough Claude token estimate (~3.5 chars/token for Claude models)."""
    return max(1, len(text) // 3)


class TokenPakAnthropicMessages:
    """
    Compression wrapper for Anthropic messages API.

    Compresses conversation history before sending to Claude,
    reducing token costs while preserving recent context.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        target_tokens_per_msg: int = 500,
        keep_recent_turns: int = 3,
        verbose: bool = False,
    ):
        """
        Args:
            api_key: Anthropic API key (or ANTHROPIC_API_KEY env var)
            target_tokens_per_msg: Target token count for compressed messages
            keep_recent_turns: Recent user/assistant pairs to preserve uncompressed
            verbose: Log compression stats
        """
        try:
            import anthropic
            self._client = anthropic.Anthropic(
                api_key=api_key or os.environ.get("ANTHROPIC_API_KEY")
            )
        except ImportError:
            self._client = None
            print("⚠️  anthropic package not installed — running in dry-run mode")

        self.engine = HeuristicEngine()
        self.cache = CacheManager(default_ttl=300)
        self.target_tokens = target_tokens_per_msg
        self.keep_recent_turns = keep_recent_turns
        self.verbose = verbose

        self._stats = {"calls": 0, "tokens_saved": 0, "cache_hits": 0}

    def _compress(self, content: str) -> str:
        """Compress text with caching."""
        cache_key = hashlib.sha256(content.encode()).hexdigest()[:20]
        hit, cached = self.cache.get(cache_key)

        if hit:
            self._stats["cache_hits"] += 1
            return cached

        hints = CompactionHints(
            target_tokens=self.target_tokens,
            keep_headers=True,
            keep_code_blocks=True,
        )
        compressed = self.engine.compact(content, hints)

        saved = max(0, estimate_tokens(content) - estimate_tokens(compressed))
        self._stats["tokens_saved"] += saved

        self.cache.set(cache_key, compressed, ttl=300)
        return compressed

    def _prepare_messages(self, messages: list[dict]) -> list[dict]:
        """
        Compress older turns, preserve recent context.

        Note: Anthropic's API uses role "user"/"assistant" without "system"
        in the messages list (system is a separate top-level parameter).
        """
        keep_count = self.keep_recent_turns * 2
        recent = messages[-keep_count:] if len(messages) > keep_count else messages
        older = messages[:-keep_count] if len(messages) > keep_count else []

        result = []
        for msg in older:
            content = msg.get("content", "")
            if isinstance(content, str):
                compressed = self._compress(content)
                result.append({**msg, "content": compressed})
            else:
                # Content can be a list of blocks in Claude API — preserve as-is
                result.append(msg)

        result.extend(recent)

        if self.verbose:
            before = sum(estimate_tokens(m.get("content","") if isinstance(m.get("content"), str) else "") for m in messages)
            after = sum(estimate_tokens(m.get("content","") if isinstance(m.get("content"), str) else "") for m in result)
            print(f"[TokenPak] {before} → {after} tokens ({max(0,1-after/max(1,before)):.0%} savings)")

        return result

    def create(
        self,
        messages: list[dict],
        system: Optional[str] = None,
        model: str = "claude-opus-4-5",
        max_tokens: int = 1024,
        **kwargs,
    ) -> Any:
        """
        Drop-in for anthropic.messages.create().

        Compresses messages automatically before forwarding.
        """
        self._stats["calls"] += 1

        compressed_messages = self._prepare_messages(messages)

        if self.verbose:
            print(f"[TokenPak] Sending {len(compressed_messages)} messages to {model}")

        if self._client is None:
            # Dry-run: return mock response
            return {
                "content": [{"text": "[dry-run response]"}],
                "_tokenpak": {
                    "original_messages": messages,
                    "compressed_messages": compressed_messages,
                },
            }

        return self._client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system or "",
            messages=compressed_messages,
            **kwargs,
        )

    @property
    def stats(self) -> dict:
        return dict(self._stats)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def run_demo():
    """Show the Claude integration wrapper in action."""
    print("=== Claude API Integration with TokenPak ===\n")

    client = TokenPakAnthropicMessages(
        target_tokens_per_msg=150,
        keep_recent_turns=2,
        verbose=True,
    )

    # Simulate a long conversation about Python
    messages = [
        {
            "role": "user",
            "content": (
                "I'm trying to learn Python and I was wondering if you could help "
                "me understand the difference between lists, tuples, and sets in Python. "
                "I find it confusing when to use which data structure and I would love "
                "a thorough explanation with examples."
            ),
        },
        {
            "role": "assistant",
            "content": (
                "Lists are mutable ordered sequences: [1, 2, 3]. "
                "Tuples are immutable ordered sequences: (1, 2, 3). "
                "Sets are mutable unordered collections with unique elements: {1, 2, 3}. "
                "Use lists when order matters and you need to modify the collection. "
                "Use tuples for fixed data (like coordinates). "
                "Use sets when you need unique values or fast membership tests."
            ),
        },
        {
            "role": "user",
            "content": "That makes sense! Can you explain comprehensions?",
        },
        {
            "role": "assistant",
            "content": (
                "Comprehensions are concise ways to create collections. "
                "List comprehension: [x*2 for x in range(10)]. "
                "Dict comprehension: {k: v for k, v in pairs}. "
                "Set comprehension: {x for x in items if x > 0}. "
                "They're often faster and more readable than for-loops."
            ),
        },
        # Recent turns — preserved intact
        {
            "role": "user",
            "content": "Now can you explain generators and when to use them over lists?",
        },
    ]

    system = "You are an expert Python instructor who teaches with clear, concise examples."

    # This calls the Anthropic API if the key is set
    response = client.create(
        messages=messages,
        system=system,
        model="claude-haiku-4-5",
        max_tokens=512,
    )

    print(f"\nStats: {client.stats}")
    if isinstance(response, dict) and "_tokenpak" in response:
        print("\n[Dry-run] Compressed messages ready to send:")
        for msg in response["_tokenpak"]["compressed_messages"]:
            print(f"  [{msg['role']}] {estimate_tokens(msg.get('content',''))}"
                  f" tokens: {str(msg.get('content',''))[:60]}...")

    print("\n✅ Claude integration demo complete!")
    print("   (Set ANTHROPIC_API_KEY env var for live API calls)")


if __name__ == "__main__":
    run_demo()
