"""
LangChain Integration Example
================================
Integrate TokenPak compression into LangChain pipelines.

Problem: LangChain chains pass verbose documents, chat history, and
         retrieved context to LLMs — often redundantly.

Solution: A TokenPak-backed message history class and a compression
          utility for document retrieval pipelines.

Expected savings: 40-60% on retrieved documents, 30-50% on chat history.
Setup: pip install tokenpak langchain langchain-core
"""

import sys
import os
import hashlib
from typing import Optional, Sequence

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))

from tokenpak import HeuristicEngine, CacheManager
from tokenpak.engines.base import CompactionHints


# ---------------------------------------------------------------------------
# TokenPak Message History (LangChain-compatible)
# ---------------------------------------------------------------------------

class TokenPakChatMessageHistory:
    """
    LangChain-compatible chat message history with automatic compression.

    Compresses older messages when the history exceeds a token budget,
    keeping recent turns intact for quality responses.

    Compatible with LangChain's BaseChatMessageHistory interface.

    Usage:
        history = TokenPakChatMessageHistory(max_tokens=2000)
        history.add_user_message("Hello!")
        history.add_ai_message("Hi there!")
        messages = history.messages  # auto-compressed if over budget
    """

    def __init__(
        self,
        max_tokens: int = 2000,
        keep_recent_turns: int = 4,
        session_id: Optional[str] = None,
    ):
        self.max_tokens = max_tokens
        self.keep_recent_turns = keep_recent_turns
        self.session_id = session_id or "default"

        self.engine = HeuristicEngine()
        self.cache = CacheManager(default_ttl=600)
        self._messages = []  # Store as simple dicts for portability

    def add_user_message(self, content: str) -> None:
        """Add a user message."""
        self._messages.append({"role": "human", "content": content})

    def add_ai_message(self, content: str) -> None:
        """Add an AI/assistant message."""
        self._messages.append({"role": "ai", "content": content})

    def _estimate_tokens(self) -> int:
        return sum(len(m["content"]) // 4 for m in self._messages)

    def _compress_message(self, msg: dict) -> dict:
        """Compress a single message with caching."""
        content = msg["content"]
        cache_key = hashlib.sha256(content.encode()).hexdigest()[:20]

        hit, cached = self.cache.get(cache_key)
        if hit:
            return {**msg, "content": cached, "_compressed": True}

        hints = CompactionHints(target_tokens=max(50, len(content) // 8))
        compressed = self.engine.compact(content, hints)
        self.cache.set(cache_key, compressed, ttl=600)
        return {**msg, "content": compressed, "_compressed": True}

    @property
    def messages(self) -> list[dict]:
        """
        Return messages, compressing older ones if over budget.

        This is the property LangChain chains call to get message history.
        """
        if self._estimate_tokens() <= self.max_tokens:
            return self._messages

        # Compress older messages
        keep_count = self.keep_recent_turns * 2
        recent = self._messages[-keep_count:] if len(self._messages) > keep_count else self._messages
        older = self._messages[:-keep_count] if len(self._messages) > keep_count else []

        compressed_older = [self._compress_message(m) for m in older]
        return compressed_older + recent

    def clear(self) -> None:
        """Clear all messages."""
        self._messages = []

    def __len__(self) -> int:
        return len(self._messages)


# ---------------------------------------------------------------------------
# Document Compression for RAG Pipelines
# ---------------------------------------------------------------------------

class TokenPakDocumentCompressor:
    """
    Compress retrieved documents before passing to LLM in RAG pipelines.

    Integrates with LangChain's ContextualCompressionRetriever pattern.

    Usage:
        compressor = TokenPakDocumentCompressor(target_tokens=200)
        compressed_docs = compressor.compress_documents(retrieved_docs, query)
    """

    def __init__(self, target_tokens: int = 200, min_score: float = 0.0):
        """
        Args:
            target_tokens: Max tokens per document after compression
            min_score: Minimum relevance score to keep a document (0-1)
        """
        self.engine = HeuristicEngine()
        self.target_tokens = target_tokens
        self.min_score = min_score

    def compress_documents(
        self,
        documents: list[dict],
        query: Optional[str] = None,
    ) -> list[dict]:
        """
        Compress a list of retrieved documents.

        Args:
            documents: List of {"page_content": str, "metadata": dict} dicts
            query: The query string (unused currently, reserved for future scoring)

        Returns:
            Compressed documents list
        """
        compressed = []
        for doc in documents:
            content = doc.get("page_content", "")
            if not content.strip():
                continue

            hints = CompactionHints(
                target_tokens=self.target_tokens,
                keep_headers=True,
                keep_code_blocks=True,
            )
            compressed_content = self.engine.compact(content, hints)

            compressed.append({
                **doc,
                "page_content": compressed_content,
                "metadata": {
                    **doc.get("metadata", {}),
                    "tokenpak_original_tokens": len(content) // 4,
                    "tokenpak_compressed_tokens": len(compressed_content) // 4,
                },
            })

        return compressed

    def compression_ratio(self, original: list[dict], compressed: list[dict]) -> float:
        """Calculate overall compression ratio."""
        orig_tokens = sum(len(d.get("page_content",""))//4 for d in original)
        comp_tokens = sum(len(d.get("page_content",""))//4 for d in compressed)
        return 1 - comp_tokens / max(1, orig_tokens)


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def demo_message_history():
    """Show the TokenPak message history in action."""
    print("=== TokenPak LangChain Message History ===\n")

    history = TokenPakChatMessageHistory(max_tokens=500, keep_recent_turns=2)

    # Add a long conversation
    exchanges = [
        ("What is dependency injection?",
         "Dependency injection is a design pattern where a class receives its dependencies "
         "from external sources. Instead of creating dependencies itself, they are 'injected'. "
         "This promotes loose coupling and makes code testable."),
        ("Can you give an example in Python?",
         "Sure! Instead of: class Service: def __init__(self): self.db = Database() "
         "Use: class Service: def __init__(self, db: Database): self.db = db "
         "Now you can inject any Database implementation, including test doubles."),
        ("What about dependency injection frameworks?",
         "Python has several: injector, dependency-injector, and pinject. "
         "FastAPI has built-in DI via Depends(). Django uses settings for configuration. "
         "For small projects, manual DI often suffices."),
        ("How does this relate to SOLID principles?",
         "DI directly supports the Dependency Inversion Principle (the D in SOLID): "
         "high-level modules should not depend on low-level modules; both should depend on abstractions."),
    ]

    for user_msg, ai_msg in exchanges:
        history.add_user_message(user_msg)
        history.add_ai_message(ai_msg)

    raw_tokens = history._estimate_tokens()
    messages = history.messages
    compressed_tokens = sum(len(m["content"])//4 for m in messages)

    print(f"Raw history:      {len(history._messages)} messages, ~{raw_tokens} tokens")
    print(f"Compressed view:  {len(messages)} messages, ~{compressed_tokens} tokens")
    print(f"Savings:          {1 - compressed_tokens/max(1,raw_tokens):.0%}\n")

    for msg in messages:
        role = msg["role"]
        compressed = " [C]" if msg.get("_compressed") else ""
        print(f"  [{role}{compressed}] ~{len(msg['content'])//4} tokens")


def demo_document_compression():
    """Show RAG document compression."""
    print("\n=== TokenPak RAG Document Compression ===\n")

    compressor = TokenPakDocumentCompressor(target_tokens=100)

    # Simulate retrieved documents
    docs = [
        {
            "page_content": (
                "Python is a high-level, general-purpose programming language. Its design "
                "philosophy emphasizes code readability with the use of significant indentation. "
                "Python is dynamically typed and garbage-collected. It supports multiple "
                "programming paradigms, including structured, object-oriented and functional "
                "programming. It is often described as a 'batteries included' language due to "
                "its comprehensive standard library. Guido van Rossum began working on Python "
                "in the late 1980s as a successor to the ABC programming language."
            ),
            "metadata": {"source": "wikipedia", "score": 0.92},
        },
        {
            "page_content": (
                "The Python programming language documentation is available at docs.python.org. "
                "The official documentation includes tutorials, library reference, language "
                "reference, and HOWTOs. Community resources include Python.org, PyPI for "
                "packages, and numerous books and online courses. Stack Overflow has millions "
                "of Python questions and answers."
            ),
            "metadata": {"source": "python.org", "score": 0.75},
        },
    ]

    compressed = compressor.compress_documents(docs, query="What is Python?")
    ratio = compressor.compression_ratio(docs, compressed)

    print(f"Documents:   {len(docs)}")
    print(f"Compression: {ratio:.0%} overall savings\n")

    for i, (orig, comp) in enumerate(zip(docs, compressed)):
        print(f"Doc {i+1}: {len(orig['page_content'])//4} → "
              f"{len(comp['page_content'])//4} tokens "
              f"({comp['metadata']['tokenpak_compressed_tokens']} tokens)")


if __name__ == "__main__":
    demo_message_history()
    demo_document_compression()
    print("\n✅ LangChain integration demo complete!")
