"""
tracer.py — TokenPakTracer: high-level Langfuse tracing for TokenPak packs.

Provides a context manager that captures pack compilation metadata and
uploads it as a structured Langfuse trace with block-level visibility.

Usage (Langfuse v2+):
    from langfuse import Langfuse
    from langfuse_tokenpak import TokenPakTracer

    langfuse = Langfuse()
    tracer = TokenPakTracer(langfuse)

    with tracer.trace_pack(pack, name="rag_query") as span:
        response = llm.complete(pack.to_prompt())
        tracer.record_output(span, response)
"""

from __future__ import annotations

import contextlib
from typing import Any, Dict, Generator, List, Optional

from .visualization import blocks_to_metadata, ascii_block_summary
from .analytics import TokenPakAnalytics


class TokenPakTracer:
    """
    High-level tracer that records TokenPak pack metadata into Langfuse.

    Works with any object that has a `.trace()` or `.span()` method matching
    the Langfuse v2 API (i.e., returns a trace/span with `.update()`).

    If Langfuse is not installed, the tracer degrades gracefully (no-op).
    """

    def __init__(
        self,
        langfuse: Any,
        *,
        trace_blocks: bool = True,
        trace_compression: bool = True,
        trace_ascii_summary: bool = False,
        analytics: Optional[TokenPakAnalytics] = None,
    ) -> None:
        """
        Initialize the tracer.

        Args:
            langfuse: A Langfuse client instance (langfuse.Langfuse).
            trace_blocks: Include per-block breakdown in trace metadata.
            trace_compression: Include compression stats in metadata.
            trace_ascii_summary: Add ASCII block diagram to trace input.
            analytics: Optional shared analytics instance for aggregation.
        """
        self._langfuse = langfuse
        self.trace_blocks = trace_blocks
        self.trace_compression = trace_compression
        self.trace_ascii_summary = trace_ascii_summary
        self.analytics = analytics or TokenPakAnalytics()

    def _extract_blocks(self, pack: Any) -> List[Any]:
        """
        Extract blocks from a pack object or list.

        Supports:
        - Objects with .blocks attribute
        - Objects with .get_blocks() method
        - Plain lists
        """
        if isinstance(pack, list):
            return pack
        if hasattr(pack, "blocks"):
            return list(pack.blocks)
        if hasattr(pack, "get_blocks"):
            return list(pack.get_blocks())
        return []

    def _extract_budget(self, pack: Any) -> Optional[int]:
        """Extract budget from pack if available."""
        if hasattr(pack, "budget"):
            return pack.budget
        if hasattr(pack, "token_budget"):
            return pack.token_budget
        return None

    def _build_input_metadata(
        self,
        blocks: List[Any],
        budget: Optional[int],
        raw_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Build the trace input metadata dict."""
        meta = blocks_to_metadata(blocks, budget=budget)
        if not self.trace_blocks:
            meta.pop("blocks", None)
        if not self.trace_compression:
            meta.pop("compacted_blocks", None)
        if raw_tokens is not None:
            saved = raw_tokens - meta.get("total_tokens", 0)
            ratio = round(meta.get("total_tokens", 0) / raw_tokens, 3) if raw_tokens else 1.0
            meta["raw_tokens"] = raw_tokens
            meta["tokens_saved"] = max(0, saved)
            meta["compression_ratio"] = ratio
        return {"type": "tokenpak_pack", "tokenpak": meta}

    @contextlib.contextmanager
    def trace_pack(
        self,
        pack: Any,
        *,
        name: str = "tokenpak_pack",
        user_id: Optional[str] = None,
        session_id: Optional[str] = None,
        raw_tokens: Optional[int] = None,
        tags: Optional[List[str]] = None,
    ) -> Generator[Any, None, None]:
        """
        Context manager that creates a Langfuse trace for a TokenPak pack.

        Records block breakdown, token stats, and compression metadata.

        Args:
            pack: A TokenPak pack object (or list of blocks).
            name: Trace name in Langfuse.
            user_id: Optional Langfuse user_id for the trace.
            session_id: Optional Langfuse session_id for the trace.
            raw_tokens: Original token count before compression.
            tags: Optional list of tags for the trace.

        Yields:
            The Langfuse trace object (for further .update() calls).
        """
        blocks = self._extract_blocks(pack)
        budget = self._extract_budget(pack)

        input_meta = self._build_input_metadata(blocks, budget, raw_tokens)

        if self.trace_ascii_summary:
            input_meta["ascii_summary"] = ascii_block_summary(blocks, budget)

        # Record analytics
        self.analytics.record_pack(blocks, budget=budget, raw_tokens=raw_tokens)

        trace = None
        try:
            trace_kwargs: Dict[str, Any] = {
                "name": name,
                "input": input_meta,
                "metadata": {"tokenpak_version": "1.0"},
                "tags": tags or ["tokenpak"],
            }
            if user_id:
                trace_kwargs["user_id"] = user_id
            if session_id:
                trace_kwargs["session_id"] = session_id

            trace = self._langfuse.trace(**trace_kwargs)
        except Exception:
            pass  # Langfuse unavailable — degrade gracefully

        yield trace

    def record_output(
        self,
        trace: Any,
        output: Any,
        *,
        usage: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Record the LLM output on an existing trace.

        Args:
            trace: Langfuse trace object from trace_pack().
            output: LLM response (string or object with .text/.content).
            usage: Optional token usage dict (prompt_tokens, completion_tokens).
        """
        if trace is None:
            return

        text = output
        if hasattr(output, "text"):
            text = output.text
        elif hasattr(output, "content"):
            text = output.content
        elif hasattr(output, "choices"):
            # OpenAI-style
            try:
                text = output.choices[0].message.content
            except Exception:
                text = str(output)

        try:
            update_kwargs: Dict[str, Any] = {"output": text}
            if usage:
                update_kwargs["usage"] = usage
            trace.update(**update_kwargs)
        except Exception:
            pass

    def flush(self) -> None:
        """Flush pending Langfuse events."""
        try:
            self._langfuse.flush()
        except Exception:
            pass

    def get_analytics(self) -> Dict[str, Any]:
        """Return current analytics report."""
        return self.analytics.get_report()
