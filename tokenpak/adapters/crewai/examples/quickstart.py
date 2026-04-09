"""
CrewAI TokenPak Adapter — Quickstart

Demonstrates context compression for CrewAI multi-agent runs without requiring
a real CrewAI installation (the hook and context manager work standalone).

For full CrewAI integration:
    pip install tokenpak[crewai]
    # Then use TokenPakCrew.create(agents=..., tasks=...)
"""

from tokenpak.adapters.crewai import (
    AgentContextConfig,
    CompressionResult,
    TokenPakContext,
    TokenPakCompressionReport,
    TokenPakCrewAIHook,
)


def demo_context_manager() -> None:
    print("=" * 60)
    print("Demo 1: TokenPakContext — budget + usage tracking")
    print("=" * 60)

    ctx = TokenPakContext(total_budget=8000, per_agent_budget=2000)

    long_text = (
        "CrewAI orchestrates multi-agent tasks using role-based agents. "
        "Each agent receives a system prompt and conversation history. "
        "TokenPak compresses that context to stay within token budgets.\n"
    ) * 40

    result: CompressionResult = ctx.compress_text(long_text, budget=500)
    ctx.record_usage("researcher", result.compressed_tokens)

    print(f"Original tokens  : {result.original_tokens}")
    print(f"Compressed tokens: {result.compressed_tokens}")
    print(f"Savings          : {result.savings_tokens} ({result.savings_ratio:.1%})")
    print(f"Was compressed   : {result.was_compressed}")
    print(f"Remaining budget : {ctx.remaining_budget()}")
    print(f"Context report   : {ctx.report()}")
    print()


def demo_hook() -> None:
    print("=" * 60)
    print("Demo 2: TokenPakCrewAIHook — context assembly + compression")
    print("=" * 60)

    hook = TokenPakCrewAIHook(
        total_budget=8000,
        shared_context="TokenPak is an LLM cost-reduction proxy.",
        task_context={"research_task": "Focus on compression ratios."},
        agent_overrides={"researcher": AgentContextConfig(budget=3000)},
    )

    class FakeAgent:
        role = "researcher"

    class FakeTask:
        agent = FakeAgent()
        name = "research_task"
        context = None

    compressed = hook.build_context(FakeTask(), task_outputs=[])
    report: TokenPakCompressionReport = hook.last_report

    print(f"Compressed context: {compressed!r}")
    print(f"Report            : task={report.task_name}, agent={report.agent_name}")
    print(f"Tokens saved      : {report.savings_tokens} ({report.savings_percent:.1f}%)")
    print(f"Aggregate         : {hook.compression_report()}")
    print()


def demo_per_agent_override() -> None:
    print("=" * 60)
    print("Demo 3: Per-agent budget overrides")
    print("=" * 60)

    overrides = {
        "researcher": AgentContextConfig(budget=4000),
        "writer": AgentContextConfig(
            budget=2000,
            prefix="You are a professional writer.\n",
            suffix="\nAlways cite your sources.",
        ),
    }
    hook = TokenPakCrewAIHook(total_budget=10000, agent_overrides=overrides)

    for agent_name in ("researcher", "writer"):
        budget = hook.context_manager.allocate_budget(agent_name)
        print(f"  {agent_name}: budget={budget}")
    print()


if __name__ == "__main__":
    demo_context_manager()
    demo_hook()
    demo_per_agent_override()
    print("Quickstart complete.")
