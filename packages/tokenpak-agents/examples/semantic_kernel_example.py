"""Example usage for tokenpak_agents.semantic_kernel."""

import asyncio

from tokenpak_agents.semantic_kernel import TokenPakMemory


async def main() -> None:
    memory = TokenPakMemory(budget=4000, compaction_mode="balanced", max_entries=100)

    await memory.save_information(
        collection="project",
        text="TokenPak provides token-efficient context handoffs for multi-agent systems.",
        id="doc-1",
        metadata={"source": "notes"},
    )

    hits = await memory.get_information("project", "token-efficient handoffs", limit=3)
    print(hits)
    print(memory.to_prompt("project"))


if __name__ == "__main__":
    asyncio.run(main())
