#!/usr/bin/env python3
"""
basic_usage.py — tokenpak-local demonstration examples.

All examples use mocked clients — no live Ollama or LM Studio required.
Run with: python examples/basic_usage.py
"""

from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Helpers: mock out the heavy deps
# ---------------------------------------------------------------------------

def _mock_ollama_sdk():
    sdk = MagicMock()
    client = MagicMock()
    client.show.return_value = {}
    client.chat.return_value = {"message": {"content": "Paris, France."}}
    client.generate.return_value = {"response": "The Eiffel Tower is in Paris."}
    sdk.Client.return_value = client
    return sdk, client


def _mock_openai_sdk():
    sdk = MagicMock()
    client = MagicMock()
    choice = MagicMock()
    choice.message.content = "Paris, France."
    sdk.OpenAI.return_value = client
    client.chat.completions.create.return_value.choices = [choice]
    client.models.list.return_value.data = []
    return sdk, client


# ---------------------------------------------------------------------------
# Section 1: auto_budget
# ---------------------------------------------------------------------------

def demo_auto_budget():
    print("=" * 60)
    print("1. AUTO-BUDGET")
    print("=" * 60)
    from tokenpak_local.auto_budget import (
        auto_budget, get_context_length, budget_info, MODEL_CONTEXT_LENGTHS
    )

    models = ["llama3", "phi3", "llama3.1:8b", "mistral", "qwen2.5:7b", "unknown-model"]
    print(f"{'Model':<25} {'Context':>10} {'Budget (75%)':>14}")
    print("-" * 52)
    for m in models:
        ctx = get_context_length(m)
        budget = auto_budget(m)
        print(f"{m:<25} {ctx:>10,} {budget:>14,}")

    print()
    info = budget_info("llama3", output_fraction=0.25)
    print(f"Budget info for llama3: {info}")
    print()


# ---------------------------------------------------------------------------
# Section 2: utils — Block and TokenPak
# ---------------------------------------------------------------------------

def demo_utils():
    print("=" * 60)
    print("2. BLOCK & TOKENPAK UTILS")
    print("=" * 60)
    from tokenpak_local.utils import Block, TokenPak, blocks_from_texts, pack_from_blocks

    # Build blocks from docs
    docs = [
        "The Eiffel Tower is located in Paris, France.",
        "It was built by Gustave Eiffel for the 1889 World's Fair.",
        "The tower stands 330 meters tall.",
    ]
    blocks = blocks_from_texts(docs, block_type="evidence")
    print(f"Created {len(blocks)} blocks from texts:")
    for b in blocks:
        print(f"  [{b.type}] tokens={b.tokens} | {b.content[:50]}...")

    # Build pack
    pack = pack_from_blocks(blocks, instructions="Answer based on the evidence.", budget=6144)
    messages = pack.to_messages()
    print(f"\nPack messages: {len(messages)} message(s)")
    print(f"System message preview:\n{messages[0]['content'][:200]}\n")


# ---------------------------------------------------------------------------
# Section 3: TokenPakOllama
# ---------------------------------------------------------------------------

def demo_ollama():
    print("=" * 60)
    print("3. TOKENPAK OLLAMA CLIENT")
    print("=" * 60)
    mock_sdk, mock_client = _mock_ollama_sdk()

    with patch.dict("sys.modules", {"ollama": mock_sdk}):
        import importlib
        import tokenpak_local.ollama as mod
        importlib.reload(mod)
        mod._OLLAMA_AVAILABLE = True
        mod._ollama_sdk = mock_sdk

        from tokenpak_local.utils import Block, TokenPak

        client = mod.TokenPakOllama()
        client._client = mock_client

        # Show budget for various models
        print("Auto-detected budgets:")
        for model in ["llama3", "phi3", "mistral", "llama3.1:8b"]:
            info = client.budget_info(model)
            print(f"  {model}: context={info['context_length']:,}  budget={info['input_budget']:,}")

        # Build a pack and chat
        pack = TokenPak(instructions="Answer based on the evidence below.")
        pack.add(Block(type="evidence", content="The Eiffel Tower is in Paris, France."))
        pack.add(Block(type="evidence", content="It was built by Gustave Eiffel."))

        response = client.chat(model="llama3", tokenpak=pack)
        print(f"\nChat response: {response['message']['content']}")
        print(f"Pack budget auto-set to: {pack.budget:,}")

        # Generate
        gen_resp = client.generate(
            model="llama3",
            tokenpak=pack,
            prompt="Where is the Eiffel Tower?"
        )
        print(f"Generate response: {gen_resp['response']}")
        print()


# ---------------------------------------------------------------------------
# Section 4: TokenPakLMStudio
# ---------------------------------------------------------------------------

def demo_lmstudio():
    print("=" * 60)
    print("4. TOKENPAK LM STUDIO CLIENT")
    print("=" * 60)
    mock_sdk, mock_client = _mock_openai_sdk()

    with patch.dict("sys.modules", {"openai": mock_sdk}):
        import importlib

        import tokenpak_local.openai_compat as compat_mod
        importlib.reload(compat_mod)
        compat_mod._OPENAI_AVAILABLE = True
        compat_mod.OpenAI = mock_sdk.OpenAI

        import tokenpak_local.lmstudio as mod
        importlib.reload(mod)
        mod._OPENAI_AVAILABLE = True
        mod.OpenAI = mock_sdk.OpenAI

        from tokenpak_local.utils import Block, TokenPak

        client = mod.TokenPakLMStudio()
        client._client = mock_client

        print(f"Server URL: {client.server_url}")

        pack = TokenPak(instructions="You are a helpful assistant.")
        pack.add(Block(type="evidence", content="The Eiffel Tower is in Paris."))

        response = client.complete(
            model="meta-llama-3-8b-instruct",
            tokenpak=pack,
            user_message="Where is the Eiffel Tower?"
        )
        print(f"Response: {response.choices[0].message.content}")
        print(f"Budget: {pack.budget:,}")
        print()


# ---------------------------------------------------------------------------
# Section 5: Full RAG pipeline (mocked)
# ---------------------------------------------------------------------------

def demo_rag_pipeline():
    print("=" * 60)
    print("5. FULL LOCAL RAG PIPELINE (MOCKED)")
    print("=" * 60)
    mock_sdk, mock_client = _mock_ollama_sdk()
    mock_client.chat.return_value = {
        "message": {"content": "TokenPak is a context compression protocol for LLMs."}
    }

    with patch.dict("sys.modules", {"ollama": mock_sdk}):
        import importlib
        import tokenpak_local.ollama as mod
        importlib.reload(mod)
        mod._OLLAMA_AVAILABLE = True
        mod._ollama_sdk = mock_sdk

        from tokenpak_local.utils import blocks_from_texts, pack_from_blocks
        from tokenpak_local.auto_budget import auto_budget

        # Simulated retrieval results
        query = "What is TokenPak?"
        retrieved_docs = [
            "TokenPak is an open protocol for compressing context sent to LLMs.",
            "It uses structured blocks to prioritize important information.",
            "TokenPak reduces token usage in typical RAG workloads.",
            "The protocol is model-agnostic and works with any LLM API.",
        ]

        # Build TokenPak
        budget = auto_budget("llama3")
        blocks = blocks_from_texts(retrieved_docs, block_type="evidence")
        pack = pack_from_blocks(
            blocks,
            instructions="Answer the question based on the evidence below.",
            budget=budget,
        )

        print(f"Query: {query}")
        print(f"Retrieved: {len(retrieved_docs)} docs → {len(blocks)} blocks")
        print(f"Total tokens: {pack.total_tokens} | Budget: {budget:,}")

        # Local inference
        client = mod.TokenPakOllama()
        client._client = mock_client

        response = client.chat(
            model="llama3",
            tokenpak=pack,
            messages=[{"role": "user", "content": query}],
        )
        print(f"Answer: {response['message']['content']}")
        print()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("\ntokenpak-local — Basic Usage Examples")
    print("=" * 60)
    print()

    demo_auto_budget()
    demo_utils()
    demo_ollama()
    demo_lmstudio()
    demo_rag_pipeline()

    print("All examples completed successfully.")
