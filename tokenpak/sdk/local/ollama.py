"""
ollama.py — TokenPak wrapper for the Ollama Python client.

Provides TokenPakOllama: a thin wrapper around ollama.Client that accepts
a TokenPak pack, auto-sizes the budget to the model's context window, and
compiles the pack before calling Ollama.

Requires: pip install tokenpak-local[ollama]
"""

from __future__ import annotations

from typing import Any, Dict, Iterator, List, Optional, Union

from .auto_budget import auto_budget, get_context_length
from .utils import TokenPak

# ---------------------------------------------------------------------------
# Optional import — ollama SDK
# ---------------------------------------------------------------------------

try:
    import ollama as _ollama_sdk  # type: ignore[import]

    _OLLAMA_AVAILABLE = True
except ImportError:
    _OLLAMA_AVAILABLE = False
    _ollama_sdk = None  # type: ignore[assignment]


# ---------------------------------------------------------------------------
# TokenPakOllama
# ---------------------------------------------------------------------------


class TokenPakOllama:
    """
    Ollama client wrapper with automatic TokenPak context compression.

    Supports both chat (chat completions) and generate (raw text generation)
    endpoints. When a TokenPak pack is provided, the budget is automatically
    set based on the model's known context window before compilation.

    Usage:
        from tokenpak_local import TokenPakOllama, TokenPak, Block

        client = TokenPakOllama()  # connects to http://localhost:11434

        pack = TokenPak()
        pack.instructions = "Answer based on the evidence below."
        pack.add(Block(type="evidence", content="..."))

        response = client.chat(model="llama3", tokenpak=pack)
        print(response["message"]["content"])

    Streaming:
        for chunk in client.chat(model="llama3", tokenpak=pack, stream=True):
            print(chunk["message"]["content"], end="", flush=True)

    With extra messages (appended after TokenPak system message):
        response = client.chat(
            model="llama3",
            tokenpak=pack,
            messages=[{"role": "user", "content": "What is context compression?"}]
        )
    """

    def __init__(
        self,
        host: str = "http://localhost:11434",
        output_fraction: float = 0.25,
        auto_detect_context: bool = True,
        **client_kwargs: Any,
    ):
        """
        Args:
            host:                 Ollama server URL.
            output_fraction:      Fraction of context reserved for output.
            auto_detect_context:  If True, query Ollama for model context length.
                                  Falls back to registry if query fails.
            **client_kwargs:      Extra kwargs forwarded to ollama.Client().
        """
        if not _OLLAMA_AVAILABLE:
            raise ImportError("ollama package is required: pip install tokenpak-local[ollama]")
        self._host = host
        self._output_fraction = output_fraction
        self._auto_detect = auto_detect_context
        self._client = _ollama_sdk.Client(host=host, **client_kwargs)
        self._context_cache: Dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def chat(
        self,
        model: str,
        tokenpak: Optional[TokenPak] = None,
        messages: Optional[List[Dict[str, Any]]] = None,
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[Dict[str, Any], Iterator[Dict[str, Any]]]:
        """
        Send a chat request to Ollama, optionally with a TokenPak pack.

        Args:
            model:     Ollama model name (e.g. "llama3", "mistral:7b").
            tokenpak:  TokenPak pack. Budget is auto-set from model context.
            messages:  Additional chat messages appended after the system message.
                       If tokenpak is None, messages is used as-is.
            stream:    If True, returns an iterator of response chunks.
            **kwargs:  Forwarded to ollama.Client.chat().

        Returns:
            Chat response dict (or iterator if stream=True).
        """
        compiled_messages = self._compile_messages(model, tokenpak, messages)
        return self._client.chat(
            model=model,
            messages=compiled_messages,
            stream=stream,
            **kwargs,
        )

    def generate(
        self,
        model: str,
        tokenpak: Optional[TokenPak] = None,
        prompt: str = "",
        stream: bool = False,
        **kwargs: Any,
    ) -> Union[Dict[str, Any], Iterator[Dict[str, Any]]]:
        """
        Send a generate request to Ollama, optionally prepending TokenPak context.

        The TokenPak system message is prepended to the prompt string.

        Args:
            model:     Ollama model name.
            tokenpak:  TokenPak pack. Budget auto-set from model context.
            prompt:    User prompt appended after the TokenPak context.
            stream:    If True, returns an iterator of response chunks.
            **kwargs:  Forwarded to ollama.Client.generate().
        """
        full_prompt = self._compile_prompt(model, tokenpak, prompt)
        return self._client.generate(
            model=model,
            prompt=full_prompt,
            stream=stream,
            **kwargs,
        )

    def budget_for(self, model: str) -> int:
        """Return the computed input budget for a model."""
        ctx = self._get_context_length(model)
        return auto_budget(model, output_fraction=self._output_fraction, context_length=ctx)

    def budget_info(self, model: str) -> Dict[str, Any]:
        """Return full budget breakdown for a model (useful for debugging)."""
        ctx = self._get_context_length(model)
        return {
            "model": model,
            "context_length": ctx,
            "output_fraction": self._output_fraction,
            "input_budget": self.budget_for(model),
            "output_reserved": ctx - self.budget_for(model),
            "source": "ollama" if self._auto_detect else "registry",
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_context_length(self, model: str) -> int:
        """Get context length: try Ollama API first, then fall back to registry."""
        if model in self._context_cache:
            return self._context_cache[model]

        ctx: Optional[int] = None

        if self._auto_detect:
            try:
                info = self._client.show(model)
                # Ollama returns model info in different shapes depending on version
                if isinstance(info, dict):
                    ctx = info.get("context_length") or info.get("parameters", {}).get("num_ctx")
                else:
                    # Object-style response (newer ollama SDK)
                    ctx = getattr(info, "context_length", None)
                    if ctx is None:
                        params = getattr(info, "parameters", None) or {}
                        if isinstance(params, dict):
                            ctx = params.get("num_ctx")
                        elif isinstance(params, str):
                            # Parse "num_ctx 4096" style strings
                            for line in params.splitlines():
                                if line.strip().startswith("num_ctx"):
                                    try:
                                        ctx = int(line.split()[-1])
                                    except (ValueError, IndexError):
                                        pass
            except Exception:
                pass  # Fall through to registry

        if ctx is None:
            ctx = get_context_length(model)

        self._context_cache[model] = ctx
        return ctx

    def _set_budget(self, model: str, pack: TokenPak) -> None:
        """Set pack budget from model context (if not already set)."""
        # Only override if budget is None or 0
        existing = getattr(pack, "budget", None)
        if not existing:
            budget = self.budget_for(model)
            pack.budget = budget

    def _compile_messages(
        self,
        model: str,
        tokenpak: Optional[TokenPak],
        extra_messages: Optional[List[Dict[str, Any]]],
    ) -> List[Dict[str, Any]]:
        """Build the final messages list from TokenPak + extra messages."""
        messages: List[Dict[str, Any]] = []

        if tokenpak is not None:
            self._set_budget(model, tokenpak)
            try:
                compiled = tokenpak.compile()
                tp_messages = compiled.to_messages()
            except AttributeError:
                # Shim TokenPak (no compile())
                tp_messages = tokenpak.to_messages()
            messages.extend(tp_messages)

        if extra_messages:
            messages.extend(extra_messages)

        if not messages:
            messages = [{"role": "user", "content": ""}]

        return messages

    def _compile_prompt(
        self,
        model: str,
        tokenpak: Optional[TokenPak],
        user_prompt: str,
    ) -> str:
        """Build prompt string from TokenPak system message + user prompt."""
        parts: List[str] = []

        if tokenpak is not None:
            self._set_budget(model, tokenpak)
            try:
                compiled = tokenpak.compile()
                tp_messages = compiled.to_messages()
            except AttributeError:
                tp_messages = tokenpak.to_messages()
            # Extract system content
            for msg in tp_messages:
                if msg.get("role") == "system":
                    parts.append(msg["content"])

        if user_prompt:
            parts.append(user_prompt)

        return "\n\n".join(parts)
