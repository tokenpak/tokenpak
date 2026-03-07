"""Tests for tokenpak_agents.semantic_kernel.TokenPakMemory."""

import pytest

from tokenpak_agents.semantic_kernel import TokenPakMemory


@pytest.mark.asyncio
async def test_save_information_increases_count():
    memory = TokenPakMemory()
    await memory.save_information("docs", "alpha beta", "1")
    assert memory.entry_count == 1


@pytest.mark.asyncio
async def test_save_information_replaces_same_id_in_collection():
    memory = TokenPakMemory()
    await memory.save_information("docs", "alpha", "1")
    await memory.save_information("docs", "beta", "1")
    assert memory.entry_count == 1
    result = await memory.get_information("docs", "beta", limit=1)
    assert result[0]["text"] == "beta"


@pytest.mark.asyncio
async def test_get_information_orders_by_relevance():
    memory = TokenPakMemory()
    await memory.save_information("docs", "alpha beta gamma", "1")
    await memory.save_information("docs", "alpha", "2")
    await memory.save_information("docs", "zeta", "3")
    results = await memory.get_information("docs", "alpha beta", limit=3)
    assert results[0]["id"] == "1"


@pytest.mark.asyncio
async def test_get_information_applies_limit():
    memory = TokenPakMemory()
    for i in range(5):
        await memory.save_information("docs", f"item {i}", str(i))
    results = await memory.get_information("docs", "item", limit=2)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_get_information_respects_min_relevance_score():
    memory = TokenPakMemory()
    await memory.save_information("docs", "alpha", "1")
    await memory.save_information("docs", "beta", "2")
    results = await memory.get_information("docs", "alpha", min_relevance_score=0.8)
    assert len(results) == 1
    assert results[0]["id"] == "1"


def test_to_prompt_budget_capped_text_export():
    memory = TokenPakMemory(budget=5)
    import asyncio

    asyncio.run(memory.save_information("docs", "x" * 200, "1"))
    prompt = memory.to_prompt()
    assert len(prompt) <= 20


def test_to_prompt_collection_filter():
    memory = TokenPakMemory(budget=200)
    import asyncio

    asyncio.run(memory.save_information("a", "alpha", "1"))
    asyncio.run(memory.save_information("b", "beta", "1"))
    prompt = memory.to_prompt(collection="a")
    assert "alpha" in prompt
    assert "beta" not in prompt


def test_collections_returns_sorted_non_empty_names():
    memory = TokenPakMemory()
    import asyncio

    asyncio.run(memory.save_information("z", "zeta", "1"))
    asyncio.run(memory.save_information("a", "alpha", "1"))
    assert memory.collections() == ["a", "z"]


def test_clear_single_collection_only():
    memory = TokenPakMemory()
    import asyncio

    asyncio.run(memory.save_information("a", "alpha", "1"))
    asyncio.run(memory.save_information("b", "beta", "1"))
    memory.clear(collection="a")
    assert memory.collections() == ["b"]
    assert memory.entry_count == 1


def test_clear_all_removes_everything():
    memory = TokenPakMemory()
    import asyncio

    asyncio.run(memory.save_information("a", "alpha", "1"))
    memory.clear()
    assert memory.entry_count == 0
    assert memory.collections() == []


@pytest.mark.asyncio
async def test_evict_oldest_when_over_max_entries():
    memory = TokenPakMemory(max_entries=2)
    await memory.save_information("docs", "first", "1")
    await memory.save_information("docs", "second", "2")
    await memory.save_information("docs", "third", "3")
    assert memory.entry_count == 2
    results = await memory.get_information("docs", "first second third", limit=10)
    ids = {entry["id"] for entry in results}
    assert "1" not in ids
    assert ids == {"2", "3"}


@pytest.mark.asyncio
async def test_get_information_empty_query_scores_non_empty_text():
    memory = TokenPakMemory()
    await memory.save_information("docs", "alpha", "1")
    results = await memory.get_information("docs", "", limit=5)
    assert len(results) == 1
    assert results[0]["relevance_score"] == 1.0
