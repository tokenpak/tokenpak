"""
tokenpak-local

TokenPak integration for local LLMs — Ollama, LM Studio, and OpenAI-compatible endpoints.

Provides automatic context compression sized for the target model's context window,
making TokenPak especially useful where context is limited.

Quick Start (Ollama):
    from tokenpak_local import TokenPakOllama, Block

    client = TokenPakOllama()

    pack = TokenPak()
    pack.instructions = "Answer based on the context."
    pack.add(Block(type="evidence", content=doc_text))

    response = client.chat(model="llama3", tokenpak=pack)
    print(response["message"]["content"])

Quick Start (LM Studio / OpenAI-compatible):
    from tokenpak_local import TokenPakOpenAICompat, TokenPak

    client = TokenPakOpenAICompat(base_url="http://localhost:1234/v1")
    response = client.complete(model="lmstudio-community/Meta-Llama-3-8B", tokenpak=pack)

Local RAG Pipeline:
    from tokenpak_local import TokenPakOllama, auto_budget
    from tokenpak_local.utils import blocks_from_texts

    budget = auto_budget("llama3")               # 6144 tokens (75% of 8192)
    blocks = blocks_from_texts(retrieved_docs)   # convert docs → blocks
    pack = build_pack(blocks, budget=budget)
    response = TokenPakOllama().chat("llama3", tokenpak=pack)
"""

from .auto_budget import (
    DEFAULT_OUTPUT_FRACTION,
    MODEL_CONTEXT_LENGTHS,
    auto_budget,
    get_context_length,
)
from .lmstudio import TokenPakLMStudio
from .ollama import TokenPakOllama
from .openai_compat import TokenPakOpenAICompat
from .utils import (
    Block,
    TokenPak,
    blocks_from_texts,
    pack_from_blocks,
)

__version__ = "0.1.0"
__all__ = ['TokenPakOllama', 'TokenPakLMStudio', 'TokenPakOpenAICompat', 'auto_budget', 'get_context_length', 'MODEL_CONTEXT_LENGTHS', 'DEFAULT_OUTPUT_FRACTION', 'blocks_from_texts', 'pack_from_blocks', 'Block', 'TokenPak', 'examples', 'lmstudio', 'ollama', 'openai_compat', 'tests', 'utils']
