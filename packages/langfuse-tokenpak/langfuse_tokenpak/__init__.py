"""
langfuse-tokenpak

TokenPak integration for Langfuse — observability and tracing for context packs.

Quick Start:
    from langfuse import Langfuse
    from langfuse_tokenpak import TokenPakTracer

    langfuse = Langfuse()
    tracer = TokenPakTracer(langfuse)

    with tracer.trace_pack(pack, name="rag_query") as span:
        response = llm.complete(pack.to_prompt())
        tracer.record_output(span, response)

Callbacks:
    from langfuse_tokenpak import TokenPakLangChainCallback, TokenPakLlamaIndexCallback
    from langfuse_tokenpak import TokenPakCallbackHandler

Analytics:
    from langfuse_tokenpak import TokenPakAnalytics

Visualization:
    from langfuse_tokenpak.visualization import ascii_block_summary, blocks_to_metadata
"""

from .tracer import TokenPakTracer
from .callback import (
    TokenPakCallbackHandler,
    TokenPakLangChainCallback,
    TokenPakLlamaIndexCallback,
)
from .analytics import TokenPakAnalytics
from .visualization import blocks_to_metadata, ascii_block_summary

__version__ = "0.1.0"
__all__ = [
    "TokenPakTracer",
    "TokenPakCallbackHandler",
    "TokenPakLangChainCallback",
    "TokenPakLlamaIndexCallback",
    "TokenPakAnalytics",
    "blocks_to_metadata",
    "ascii_block_summary",
]
