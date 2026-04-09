"""
AutoGen TokenPak Adapter — Quickstart

Demonstrates context compression for AutoGen multi-agent conversations without
requiring a real AutoGen installation (uses mock agents).

For full AutoGen integration:
    pip install tokenpak[autogen]
    # Then hook.compress_agent(assistant) patches a real AutoGen AssistantAgent.
"""

from tokenpak.adapters.autogen import (
    AgentContextConfig,
    TokenPakAssistant,
    TokenPakCompressionReport,
    TokenPakConversationHook,
    TokenPakGroupChat,
    TokenPakMessage,
    compress_messages,
)


def demo_compress_messages() -> None:
    print("=" * 60)
    print("Demo 1: compress_messages — budget-aware message selection")
    print("=" * 60)

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Tell me about Paris. " * 80},
        {"role": "assistant", "content": "Paris is the capital of France. " * 40},
        {"role": "user", "content": "What about the Eiffel Tower?"},
    ]
    compressed = compress_messages(messages, budget=200)
    print(f"Original messages : {len(messages)}")
    print(f"Compressed        : {len(compressed)}")
    for m in compressed:
        preview = m["content"][:60].replace("\n", " ")
        print(f"  [{m['role']}] {preview!r}...")
    print()


def demo_tokenpak_message() -> None:
    print("=" * 60)
    print("Demo 2: TokenPakMessage — budget-capped content")
    print("=" * 60)

    msg = TokenPakMessage(role="user", content="Hello " * 1000, budget=50)
    print(f"Token count : {msg.token_count}")
    print(f"Content len : {len(msg.content)}")
    print(f"to_dict     : role={msg.to_dict()['role']!r}, len={len(msg.to_dict()['content'])}")
    print()


def demo_conversation_hook() -> None:
    print("=" * 60)
    print("Demo 3: TokenPakConversationHook — agent context compression")
    print("=" * 60)

    hook = TokenPakConversationHook()

    class MockAgent:
        name = "research_agent"

        def get_context(self, *args, **kwargs):
            return {
                "system_prompt": "You are a helpful assistant.\n" * 20,
                "messages": [
                    {"role": "user", "content": "Hello " * 200},
                    {"role": "assistant", "content": "Hi there! " * 100},
                    {"role": "user", "content": "Latest question?"},
                ],
                "tools": [
                    {"name": "search", "description": "Search the web. " * 10},
                ],
            }

    agent = MockAgent()
    hook.compress_agent(agent, config=AgentContextConfig(preserve_recent_messages=2))

    ctx = agent.get_context()
    report: TokenPakCompressionReport = hook.get_report("research_agent")
    print(f"Compression ratio : {report.compression_ratio:.1%}")
    print(f"Original tokens   : {report.original_tokens}")
    print(f"Compressed tokens : {report.compressed_tokens}")
    print(f"Messages kept     : {len(ctx.get('messages', []))}")
    print()


def demo_tokenpak_assistant() -> None:
    print("=" * 60)
    print("Demo 4: TokenPakAssistant — history compression wrapper")
    print("=" * 60)

    class MockRawAgent:
        name = "assistant"

    assistant = TokenPakAssistant(MockRawAgent(), budget=500)
    history = [{"role": "user", "content": "long message " * 100}] * 10
    compressed = assistant.compress_history(history)
    print(f"Original history  : {len(history)} messages")
    print(f"Compressed history: {len(compressed)} messages")
    print(f"Budget status     : {assistant.budget_status}")
    print()


def demo_groupchat() -> None:
    print("=" * 60)
    print("Demo 5: TokenPakGroupChat — group conversation compression")
    print("=" * 60)

    class MockGroupChat:
        messages = [
            {"role": "user", "content": "Question " * 50},
            {"role": "assistant", "content": "Answer " * 50},
        ] * 5

    class MockManager:
        pass

    gc = TokenPakGroupChat(MockGroupChat(), MockManager(), budget=300)
    compressed = gc.get_compressed_history()
    print(f"Total messages    : {gc.message_count}")
    print(f"After compression : {len(compressed)}")
    print(f"Budget status     : {gc.budget_status}")
    print()


if __name__ == "__main__":
    demo_compress_messages()
    demo_tokenpak_message()
    demo_conversation_hook()
    demo_tokenpak_assistant()
    demo_groupchat()
    print("Quickstart complete.")
