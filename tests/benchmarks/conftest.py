"""tests/benchmarks/conftest.py — shared fixtures for compile-time benchmarks."""

from __future__ import annotations

import pytest
from tokenpak.compression.pack import ContextPack, PackBlock

# ---------------------------------------------------------------------------
# Shared content fixtures (deterministic, reproducible across runs)
# ---------------------------------------------------------------------------

SHORT_DOC = "word " * 100  # ~100 tokens
MEDIUM_DOC = "documentation " * 350  # ~350 tokens each
LARGE_DOC = "content " * 1200  # ~1200 tokens each
SYSTEM_PROMPT = "You are a helpful assistant. " * 10
SEARCH_RESULT = "evidence result item " * 40
CONVERSATION_MSG = "user: message content\nassistant: response here\n" * 10


# ---------------------------------------------------------------------------
# Pack factories — small / medium / large
# ---------------------------------------------------------------------------


def make_small_pack() -> ContextPack:
    """~500 tokens total, 2-3 blocks, no compaction needed."""
    pack = ContextPack(budget=4000)
    pack.add(
        PackBlock(
            id="instructions",
            type="instructions",
            content="You are a helpful assistant.",
            priority="critical",
        )
    )
    pack.add(
        PackBlock(
            id="knowledge",
            type="knowledge",
            content=SHORT_DOC,
            priority="high",
        )
    )
    return pack


def make_medium_pack() -> ContextPack:
    """~5,000 tokens total, 10 blocks, compaction required (→ 4,000)."""
    pack = ContextPack(budget=4000)
    pack.add(
        PackBlock(
            id="instructions",
            type="instructions",
            content=SYSTEM_PROMPT,
            priority="critical",
        )
    )
    for i in range(5):
        pack.add(
            PackBlock(
                id=f"doc_{i}",
                type="knowledge",
                content=MEDIUM_DOC,
                priority="high",
                max_tokens=300,
            )
        )
    for i in range(3):
        pack.add(
            PackBlock(
                id=f"evidence_{i}",
                type="evidence",
                content=SEARCH_RESULT,
                priority="medium",
                quality=0.7 + i * 0.05,
            )
        )
    pack.add(
        PackBlock(
            id="conversation",
            type="conversation",
            content=CONVERSATION_MSG,
            priority="low",
            max_tokens=650,
        )
    )
    return pack


def make_large_pack() -> ContextPack:
    """~50,000 tokens total, 50 blocks, heavy compaction (→ 8,000, ~84% reduction)."""
    pack = ContextPack(budget=8000)
    pack.add(
        PackBlock(
            id="instructions",
            type="instructions",
            content=SYSTEM_PROMPT,
            priority="critical",
        )
    )
    for i in range(20):
        pack.add(
            PackBlock(
                id=f"doc_{i}",
                type="knowledge",
                content=LARGE_DOC,
                priority="high",
                max_tokens=200,
            )
        )
    for i in range(20):
        # Mix of quality levels to exercise quality filtering
        quality = 0.3 + (i % 8) * 0.1
        pack.add(
            PackBlock(
                id=f"evidence_{i}",
                type="evidence",
                content=SEARCH_RESULT,
                priority="medium",
                quality=quality,
            )
        )
    for i in range(5):
        pack.add(
            PackBlock(
                id=f"ctx_{i}",
                type="conversation",
                content=CONVERSATION_MSG * 3,
                priority="low",
            )
        )
    return pack


# ---------------------------------------------------------------------------
# pytest fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def small_pack() -> ContextPack:
    return make_small_pack()


@pytest.fixture
def medium_pack() -> ContextPack:
    return make_medium_pack()


@pytest.fixture
def large_pack() -> ContextPack:
    return make_large_pack()
