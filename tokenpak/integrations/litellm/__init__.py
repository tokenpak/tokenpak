"""TokenPak × LiteLLM integration.

Provides:
- ``TokenPakMiddleware``   — Router middleware that auto-compiles TokenPak packs
- ``compile_pack``         — Low-level helper: TokenPack → LiteLLM messages
- ``patch_completion``     — Monkey-patch litellm.completion to accept ``tokenpak=``
- ``ProxyHandler``         — ASGI handler for ``/tokenpak`` proxy endpoint

Quick start::

    from litellm import Router
    from tokenpak.integrations.litellm import TokenPakMiddleware

    router = Router(
        model_list=[...],
        middleware=[TokenPakMiddleware(compaction="balanced", budget=8000)],
    )

    from tokenpak import BlockRegistry
    pack = BlockRegistry()
    # ... populate pack ...

    response = await router.acompletion(model="gpt-4", tokenpak=pack)

"""

from .formatter import blocks_to_messages, compile_pack
from .middleware import TokenPakMiddleware
from .parser import parse_tokenpak_request
from .proxy import ProxyHandler

__all__ = [
    "TokenPakMiddleware",
    "compile_pack",
    "blocks_to_messages",
    "parse_tokenpak_request",
    "ProxyHandler",
]
