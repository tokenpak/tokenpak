"""
openai_compat.py — TokenPak wrapper for OpenAI-compatible endpoints.

Works with any server that speaks the OpenAI chat completions API, including:
  - LM Studio (http://localhost:1234/v1)
  - Ollama OpenAI endpoint (http://localhost:11434/v1)
  - LocalAI, llama.cpp server, vLLM, TabbyAPI, etc.

Requires: pip install tokenpak-local[openai]
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Union

from .auto_budget import auto_budget, get_context_length
from .utils import TokenPak

# ---------------------------------------------------------------------------
# Optional import — openai SDK
# ---------------------------------------------------------------------------

try:
    from openai import OpenAI  # type: ignore[import]
    _OPENAI_AVAILABLE = True
except ImportError:
    _OPENAI_AVAILABLE = False
    OpenAI = None  # type: ignore[misc,assignment]


class TokenPakOpenAICompat:
    """
    TokenPak wrapper for any OpenAI-compatible local LLM server.

    Automatically compiles a TokenPak pack and sets an appropriate token
    budget before sending to the local endpoint.

    Usage (LM Studio):
        from tokenpak_local import TokenPakOpenAICompat, TokenPak, Block

        client = TokenPakOpenAICompat(base_url="http://localhost:1234/v1")

        pack = TokenPak()
        pack.instructions = "You are a helpful assistant."
        pack.add(Block(type="evidence", content="Earth orbits the Sun."))

        response = client.complete(
            model="lmstudio-community/Meta-Llama-3-8B",
            tokenpak=pack,
            user_message="What does Earth orbit?",
        )
        print(response.choices[0].message.content)

    Usage (Ollama OpenAI mode):
        client = TokenPakOpenAICompat(base_url="http://localhost:11434/v1", api_key="ollama")
        response = client.complete(model="llama3", tokenpak=pack, user_message="...")

    Streaming:
        for chunk in client.complete(..., stream=True):
            print(chunk.choices[0].delta.content or "", end="", flush=True)
    """

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        api_key: str = "lm-studio",
        output_fraction: float = 0.25,
        context_length: Optional[int] = None,
        **client_kwargs: Any,
    ):
        """
        Args:
            base_url:        OpenAI-compatible API base URL.
            api_key:         API key (most local servers accept any non-empty string).
            output_fraction: Fraction of context reserved for output tokens.
            context_length:  Fixed context length override. If None, uses model registry.
            **client_kwargs: Extra kwargs for openai.OpenAI().
        """
        if not _OPENAI_AVAILABLE:
            raise ImportError(
                "openai package is required: pip install tokenpak-local[openai]"
            )
        self._base_url = base_url
        self._output_fraction = output_fraction
        self._context_length_override = context_length
        self._client = OpenAI(
            base_url=base_url,
            api_key=api_key,
            **client_kwargs,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def complete(
        self,
        model: str,
        tokenpak: Optional[TokenPak] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        user_message: Optional[str] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Any:
        """
        Send a chat completion request with optional TokenPak pack.

        Args:
            model:        Model name as registered in the local server.
            tokenpak:     TokenPak pack to compile and inject as system message.
            messages:     Additional messages (inserted after system message).
            user_message: Convenience shorthand: adds {"role": "user", "content": ...}.
            stream:       If True, returns a streaming iterator.
            **kwargs:     Forwarded to openai.ChatCompletion.create().

        Returns:
            ChatCompletion object (or iterator if stream=True).
        """
        all_messages = self._build_messages(model, tokenpak, messages, user_message)
        return self._client.chat.completions.create(
            model=model,
            messages=all_messages,
            stream=stream,
            **kwargs,
        )

    def budget_for(self, model: str) -> int:
        """Return the computed input budget for a model."""
        ctx = (
            self._context_length_override
            if self._context_length_override
            else get_context_length(model)
        )
        return auto_budget(model, output_fraction=self._output_fraction, context_length=ctx)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_messages(
        self,
        model: str,
        tokenpak: Optional[TokenPak],
        extra_messages: Optional[List[Dict[str, Any]]],
        user_message: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Compile all message sources into a flat list."""
        messages: List[Dict[str, Any]] = []

        if tokenpak is not None:
            # Auto-set budget if not already set
            if not getattr(tokenpak, "budget", None):
                tokenpak.budget = self.budget_for(model)
            try:
                compiled = tokenpak.compile()
                messages.extend(compiled.to_messages())
            except AttributeError:
                messages.extend(tokenpak.to_messages())

        if extra_messages:
            messages.extend(extra_messages)

        if user_message:
            messages.append({"role": "user", "content": user_message})

        if not messages:
            messages = [{"role": "user", "content": ""}]

        return messages
