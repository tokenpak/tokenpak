"""Basic usage for crewai-tokenpak."""

from crewai_tokenpak import TokenPakContext


def example():
    ctx = TokenPakContext(total_budget=8000)
    ctx.register_agent("researcher", budget=4000)
    ctx.add_context("researcher", "France is a country in Western Europe.")
    print("Context:", ctx.get_context("researcher"))
    print("Usage:", ctx.get_usage("researcher"))


if __name__ == "__main__":
    example()
