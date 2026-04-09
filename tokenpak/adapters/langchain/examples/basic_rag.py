"""Basic RAG example for langchain-tokenpak."""

from langchain_tokenpak import TokenPakMemory, TokenPakContextManager


def example():
    cm = TokenPakContextManager(total_budget=8000)
    cm.allocate("retrieval", 4000)
    cm.allocate("history", 2000)
    print("Context budget:", cm.status())

    memory = TokenPakMemory(budget=2000)
    memory.add_message("human", "What is the capital of France?")
    memory.add_message("ai", "Paris is the capital of France.")
    print("Memory usage:", memory.token_usage)


if __name__ == "__main__":
    example()
