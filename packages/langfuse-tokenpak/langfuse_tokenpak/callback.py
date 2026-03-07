"""
callback.py — Callback handlers for LangChain and generic Python pipelines.

Provides:
- TokenPakCallbackHandler: Generic Python callback for pack compile events
- TokenPakLangChainCallback: LangChain-compatible callback handler
- TokenPakLlamaIndexCallback: LlamaIndex-compatible callback handler
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .visualization import blocks_to_metadata
from .analytics import TokenPakAnalytics


class TokenPakCallbackHandler:
    """
    Generic Python callback for TokenPak compile events.

    Register this with any pipeline that emits on_tokenpak_compile().

    Usage:
        handler = TokenPakCallbackHandler(langfuse_client)
        pipeline.register_callback(handler)
    """

    def __init__(
        self,
        langfuse: Any,
        *,
        trace_name: str = "tokenpak_compile",
        analytics: Optional[TokenPakAnalytics] = None,
    ) -> None:
        self._langfuse = langfuse
        self.trace_name = trace_name
        self.analytics = analytics or TokenPakAnalytics()

    def on_tokenpak_compile(self, pack: Any, compiled: Any) -> None:
        """
        Called when a TokenPak is compiled.

        Args:
            pack: The original TokenPak pack object.
            compiled: The compiled result (has .blocks, .total_tokens, etc.)
        """
        blocks = getattr(compiled, "blocks", [])
        total_tokens = getattr(compiled, "total_tokens", 0)
        compression_ratio = getattr(compiled, "compression_ratio", 1.0)
        budget = getattr(pack, "budget", None)

        meta = blocks_to_metadata(blocks, budget=budget)
        meta["compression_ratio"] = compression_ratio

        self.analytics.record_pack(blocks, budget=budget)

        try:
            self._langfuse.trace(
                name=self.trace_name,
                input={"type": "tokenpak_compile", "tokenpak": meta},
                metadata={"compression_ratio": compression_ratio, "total_tokens": total_tokens},
                tags=["tokenpak", "compile"],
            )
        except Exception:
            pass


class TokenPakLangChainCallback:
    """
    LangChain-compatible callback handler for TokenPak + Langfuse.

    Instruments LangChain chains to record TokenPak metadata alongside
    standard LangChain traces.

    Usage:
        from langfuse.callback import CallbackHandler
        from langfuse_tokenpak import TokenPakLangChainCallback

        callback = TokenPakLangChainCallback(
            langfuse_handler=CallbackHandler(),
            trace_blocks=True,
        )
        chain.invoke({"input": "..."}, config={"callbacks": [callback]})
    """

    def __init__(
        self,
        langfuse_handler: Optional[Any] = None,
        *,
        trace_blocks: bool = True,
        trace_compression: bool = True,
        analytics: Optional[TokenPakAnalytics] = None,
    ) -> None:
        self.langfuse_handler = langfuse_handler
        self.trace_blocks = trace_blocks
        self.trace_compression = trace_compression
        self.analytics = analytics or TokenPakAnalytics()
        self._current_pack_meta: Optional[Dict[str, Any]] = None

    def on_tokenpak_pack(self, pack: Any, **kwargs: Any) -> None:
        """Called when a TokenPak pack is compiled in a LangChain step."""
        blocks = getattr(pack, "blocks", [])
        budget = getattr(pack, "budget", None)
        meta = blocks_to_metadata(blocks, budget=budget)
        self._current_pack_meta = meta
        self.analytics.record_pack(blocks, budget=budget)

    def on_chain_start(
        self,
        serialized: Dict[str, Any],
        inputs: Dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """Intercept chain start to inject TokenPak metadata if available."""
        if self.langfuse_handler and hasattr(self.langfuse_handler, "on_chain_start"):
            if self._current_pack_meta:
                inputs = dict(inputs)
                inputs["_tokenpak"] = self._current_pack_meta
            self.langfuse_handler.on_chain_start(serialized, inputs, **kwargs)

    def on_chain_end(self, outputs: Dict[str, Any], **kwargs: Any) -> None:
        if self.langfuse_handler and hasattr(self.langfuse_handler, "on_chain_end"):
            self.langfuse_handler.on_chain_end(outputs, **kwargs)
        self._current_pack_meta = None

    def on_chain_error(self, error: Exception, **kwargs: Any) -> None:
        if self.langfuse_handler and hasattr(self.langfuse_handler, "on_chain_error"):
            self.langfuse_handler.on_chain_error(error, **kwargs)
        self._current_pack_meta = None

    def on_llm_start(
        self,
        serialized: Dict[str, Any],
        prompts: List[str],
        **kwargs: Any,
    ) -> None:
        if self.langfuse_handler and hasattr(self.langfuse_handler, "on_llm_start"):
            self.langfuse_handler.on_llm_start(serialized, prompts, **kwargs)

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        if self.langfuse_handler and hasattr(self.langfuse_handler, "on_llm_end"):
            self.langfuse_handler.on_llm_end(response, **kwargs)


class TokenPakLlamaIndexCallback:
    """
    LlamaIndex-compatible callback for TokenPak + Langfuse.

    Captures TokenPak metadata at query time and uploads it alongside
    LlamaIndex events in Langfuse.

    Usage:
        from langfuse_tokenpak import TokenPakLlamaIndexCallback

        cb = TokenPakLlamaIndexCallback(langfuse_client)
        Settings.callback_manager = CallbackManager([cb])
    """

    def __init__(
        self,
        langfuse: Any,
        *,
        event_starts_to_ignore: Optional[List[str]] = None,
        event_ends_to_ignore: Optional[List[str]] = None,
        analytics: Optional[TokenPakAnalytics] = None,
    ) -> None:
        self._langfuse = langfuse
        self.event_starts_to_ignore = event_starts_to_ignore or []
        self.event_ends_to_ignore = event_ends_to_ignore or []
        self.analytics = analytics or TokenPakAnalytics()
        self._active_packs: Dict[str, Any] = {}

    def on_event_start(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        event_id: str = "",
        **kwargs: Any,
    ) -> str:
        """Handle LlamaIndex event start — capture TokenPak metadata if present."""
        if event_type in self.event_starts_to_ignore:
            return event_id

        if payload and "tokenpak_pack" in payload:
            pack = payload["tokenpak_pack"]
            blocks = getattr(pack, "blocks", [])
            budget = getattr(pack, "budget", None)
            meta = blocks_to_metadata(blocks, budget=budget)
            self._active_packs[event_id] = meta
            self.analytics.record_pack(blocks, budget=budget)

        return event_id

    def on_event_end(
        self,
        event_type: str,
        payload: Optional[Dict[str, Any]] = None,
        event_id: str = "",
        **kwargs: Any,
    ) -> None:
        """Handle LlamaIndex event end — upload trace if TokenPak data was captured."""
        if event_type in self.event_ends_to_ignore:
            return

        meta = self._active_packs.pop(event_id, None)
        if meta is None:
            return

        try:
            self._langfuse.trace(
                name=f"llamaindex_{event_type}",
                input={"type": "tokenpak_pack", "tokenpak": meta},
                tags=["tokenpak", "llamaindex"],
            )
        except Exception:
            pass

    def start_trace(self, trace_id: Optional[str] = None) -> None:
        """LlamaIndex callback protocol: start_trace."""
        pass

    def end_trace(
        self,
        trace_id: Optional[str] = None,
        trace_map: Optional[Dict[str, Any]] = None,
    ) -> None:
        """LlamaIndex callback protocol: end_trace."""
        pass
