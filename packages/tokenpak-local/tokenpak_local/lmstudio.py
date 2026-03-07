"""
lmstudio.py — TokenPak integration for LM Studio.

LM Studio exposes an OpenAI-compatible API at http://localhost:1234/v1.
This module is a thin specialization of TokenPakOpenAICompat with LM Studio
defaults and context window detection.

Requires: pip install tokenpak-local[openai]
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .openai_compat import TokenPakOpenAICompat
from .utils import TokenPak


class TokenPakLMStudio(TokenPakOpenAICompat):
    """
    TokenPak integration for LM Studio.

    LM Studio runs a local OpenAI-compatible server; this class sets the
    correct defaults and provides LM Studio-specific helpers.

    Usage:
        from tokenpak_local import TokenPakLMStudio, TokenPak, Block

        client = TokenPakLMStudio()  # auto-connects to localhost:1234

        pack = TokenPak()
        pack.instructions = "Answer concisely based on the context."
        pack.add(Block(type="evidence", content="The Eiffel Tower is in Paris."))

        response = client.complete(
            model="lmstudio-community/Meta-Llama-3-8B-Instruct-GGUF",
            tokenpak=pack,
            user_message="Where is the Eiffel Tower?",
        )
        print(response.choices[0].message.content)

    Context window detection:
        LM Studio loads models with specific context lengths visible in the
        app. Pass `context_length` to override:

        client = TokenPakLMStudio(context_length=8192)

    Streaming:
        for chunk in client.complete(model=..., tokenpak=pack, stream=True, user_message="..."):
            delta = chunk.choices[0].delta.content
            if delta:
                print(delta, end="", flush=True)
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 1234,
        output_fraction: float = 0.25,
        context_length: Optional[int] = None,
        **client_kwargs: Any,
    ):
        """
        Args:
            host:            LM Studio host (default: localhost).
            port:            LM Studio port (default: 1234).
            output_fraction: Fraction of context reserved for output.
            context_length:  Override detected context length.
            **client_kwargs: Extra kwargs for openai.OpenAI().
        """
        base_url = f"http://{host}:{port}/v1"
        super().__init__(
            base_url=base_url,
            api_key="lm-studio",
            output_fraction=output_fraction,
            context_length=context_length,
            **client_kwargs,
        )
        self._host = host
        self._port = port

    @property
    def server_url(self) -> str:
        return f"http://{self._host}:{self._port}"

    def list_models(self) -> List[str]:
        """
        List models currently loaded in LM Studio.

        Returns:
            List of model IDs available in the LM Studio server.
        """
        models = self._client.models.list()
        return [m.id for m in models.data]
