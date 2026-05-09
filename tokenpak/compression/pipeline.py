"""
CompressionPipeline — orchestrator for the TokenPak compression pipeline.

Chains segmentization → dedup → recipe assembly → directive application
in a single pass. Each stage is optional and toggled via constructor flags.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from tokenpak.proxy.request import ProxyRequest

from .alias_compressor import AliasCompressor, AliasResult
from .dedup import dedup_messages
from .directives import DirectiveApplier
from .instruction_table import InstructionTable
from .segmentizer import Segment, segmentize


@dataclass
class PipelineResult:
    """Output of a CompressionPipeline.run() call."""

    messages: List[Dict[str, Any]]
    segments: List[Segment]
    tokens_raw: int
    tokens_after: int
    duration_ms: float
    stages_run: List[str] = field(default_factory=list)
    instruction_replacements: Dict[str, int] = field(default_factory=dict)
    instruction_savings: Dict[str, int] = field(default_factory=dict)

    @property
    def tokens_saved(self) -> int:
        return max(0, self.tokens_raw - self.tokens_after)

    @property
    def savings_pct(self) -> float:
        if self.tokens_raw == 0:
            return 0.0
        return round(self.tokens_saved / self.tokens_raw * 100, 2)


class CompressionPipeline:
    """
    Orchestrates the TokenPak compression pipeline.

    Stages (all optional, enabled by default):
      1. dedup    — remove duplicate / near-duplicate message turns
      2. segment  — classify messages into typed Segment objects
      3. directives — apply directive rules (extensible via DirectiveApplier)

    Custom compression hooks can be added via :meth:`add_hook`.

    Parameters
    ----------
    enable_dedup : bool
        Whether to run the dedup stage.
    enable_segmentation : bool
        Whether to run the segmentizer stage.
    enable_directives : bool
        Whether to run the directive-application stage.
    trace_id : str
        Optional trace ID forwarded to segmentize().
    """

    def __init__(
        self,
        enable_dedup: bool = True,
        enable_alias: bool = True,
        enable_segmentation: bool = True,
        enable_directives: bool = True,
        enable_instruction_table: bool = True,
        instruction_table_path: str | None = None,
        context_budget_tight: bool = True,
        trace_id: str = "",
        alias_min_occurrences: int = 3,
        alias_min_length: int = 20,
    ) -> None:
        self.enable_dedup = enable_dedup
        self.enable_alias = enable_alias
        self.enable_segmentation = enable_segmentation
        self._alias_compressor = AliasCompressor(
            min_occurrences=alias_min_occurrences,
            min_entity_length=alias_min_length,
        )
        self.enable_directives = enable_directives
        self.enable_instruction_table = enable_instruction_table
        self.context_budget_tight = context_budget_tight
        self.trace_id = trace_id
        self._hooks: List[Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]]] = []
        self._directive_applier = DirectiveApplier()
        self._instruction_table = InstructionTable(path=instruction_table_path)

    def add_hook(
        self,
        fn: Callable[[List[Dict[str, Any]]], List[Dict[str, Any]]],
    ) -> None:
        """Register a custom compression hook (called after built-in stages)."""
        self._hooks.append(fn)

    def run(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        dry_run: bool = False,
    ) -> PipelineResult:
        """
        Run the full compression pipeline on *messages*.

        Parameters
        ----------
        messages:
            List of message dicts (must have "role" key).
        tools:
            Optional tool-definition list (forwarded to segmentizer).

        Returns
        -------
        PipelineResult
        """
        t0 = time.time()
        stages: List[str] = []

        tokens_raw = _estimate_tokens(messages)
        out = list(messages)  # work on a copy

        # Stage 1: dedup
        if self.enable_dedup:
            out = dedup_messages(out)
            stages.append("dedup")

        # Stage 1b: alias compression
        alias_result: "AliasResult | None" = None
        if self.enable_alias:
            alias_result = self._alias_compressor.compress(out)
            out = alias_result.messages
            stages.append("alias")

        instruction_replacements: Dict[str, int] = {}
        instruction_savings: Dict[str, int] = {}

        # Stage 2: instruction table lookup compression
        if self.enable_instruction_table:
            out, instruction_stats = self._instruction_table.compress_messages(
                out,
                context_budget_tight=self.context_budget_tight,
                persist=not dry_run,
            )
            instruction_replacements = instruction_stats.replacements_by_id
            instruction_savings = instruction_stats.tokens_saved_by_id
            stages.append("instruction_table")

        # Stage 3: custom hooks
        for hook in self._hooks:
            try:
                out = hook(out)
                stages.append(hook.__name__ if hasattr(hook, "__name__") else "hook")
            except Exception as exc:
                print(f"  ⚠ compression hook error: {exc}")

        # Stage 4: segmentize
        segments: List[Segment] = []
        if self.enable_segmentation:
            segments = segmentize(out, tools=tools, trace_id=self.trace_id)
            stages.append("segmentize")

        # Stage 5: directives
        if self.enable_directives:
            out = self._directive_applier.apply(out)
            stages.append("directives")

        tokens_after = _estimate_tokens(out)
        duration_ms = (time.time() - t0) * 1000

        return PipelineResult(
            messages=out,
            segments=segments,
            tokens_raw=tokens_raw,
            tokens_after=tokens_after,
            duration_ms=round(duration_ms, 2),
            stages_run=stages,
            instruction_replacements=instruction_replacements,
            instruction_savings=instruction_savings,
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _estimate_tokens(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += len(content) // 4
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and "text" in part:
                    total += len(part["text"]) // 4
    return total


# ---------------------------------------------------------------------------
# Compaction helpers — text + request body (A2b transfer from monolith)
# ---------------------------------------------------------------------------

_COMPACT_CACHE: Dict[str, str] = {}
_COMPACT_CACHE_ORDER: List[str] = []


def _shadow_validate(original: str, compressed: str) -> bool:
    """Returns True if compressed text passes coherence check, False = use original."""
    from tokenpak.proxy.config import SHADOW_ENABLED  # lazy import

    if not SHADOW_ENABLED:
        return True
    if not compressed or not original:
        return True
    try:
        from tokenpak.proxy.shadow_reader import ShadowReader

        reader = ShadowReader()
        result = reader.validate(original=original, compressed=compressed)
        return result.passed
    except Exception:
        return True  # fail-open: if shadow reader errors, allow compressed version


def compact_text(text: str) -> str:
    """Compact a text string: normalise whitespace, truncate at sentence boundary, apply shadow check."""
    from tokenpak.proxy.config import (  # lazy import to avoid circular dep
        COMPACT_CACHE_SIZE,
        COMPACT_MAX_CHARS,
        COMPILATION_MODE,
        SHADOW_ENABLED,
    )

    if not text:
        return text
    key = str(hash(text))
    if key in _COMPACT_CACHE:
        return _COMPACT_CACHE[key]
    t = " ".join(text.split())
    m = re.search(r"[.!?](?:\s|$)", t)
    if m:
        t = t[: m.end()].strip()
    if len(t) > COMPACT_MAX_CHARS:
        t = t[:COMPACT_MAX_CHARS].rsplit(" ", 1)[0] + "…"
    # Shadow reader guard: if compressed text fails coherence check, return original
    if SHADOW_ENABLED and COMPILATION_MODE == "aggressive" and not _shadow_validate(text, t):
        t = text  # fall back to original — coherence check failed
    _COMPACT_CACHE[key] = t
    _COMPACT_CACHE_ORDER.append(key)
    if len(_COMPACT_CACHE_ORDER) > COMPACT_CACHE_SIZE:
        old = _COMPACT_CACHE_ORDER.pop(0)
        _COMPACT_CACHE.pop(old, None)
    return t


def compact_request_body(
    body_bytes: bytes, adapter=None, *, request: "Optional[ProxyRequest]" = None
) -> Tuple[bytes, int, int, int]:
    """
    Style-contract-aware compaction.
    Returns (new_body_bytes, sent_tokens, original_tokens, protected_token_count).
    """
    if request is not None:
        body_bytes = request.body
    from tokenpak.proxy.adapters.utils import (
        _detect_adapter,
        extract_request_tokens,
    )
    from tokenpak.proxy.config import (  # lazy import
        COMPACT_MAX_TOKENS,
        COMPACT_THRESHOLD_TOKENS,
        COMPILATION_MODE,
    )
    from tokenpak.proxy.request_pipeline import can_compress, classify_message_risk  # lazy import
    from tokenpak.proxy.token_cache import count_tokens  # lazy import

    active_adapter = adapter or _detect_adapter("", {}, body_bytes)
    if active_adapter.source_format == "passthrough":
        model, tokens = extract_request_tokens(body_bytes, adapter=active_adapter)
        _ = model
        return body_bytes, tokens, tokens, 0

    try:
        canonical = active_adapter.normalize(body_bytes)
    except Exception:
        return body_bytes, 0, 0, 0

    _, original_tokens = extract_request_tokens(body_bytes, adapter=active_adapter)
    if original_tokens < COMPACT_THRESHOLD_TOKENS:
        return body_bytes, original_tokens, original_tokens, 0
    if COMPACT_MAX_TOKENS > 0 and original_tokens > COMPACT_MAX_TOKENS:
        # Skip compression for large payloads — latency cost exceeds token savings
        return body_bytes, original_tokens, original_tokens, 0

    mode = COMPILATION_MODE
    if mode == "strict":
        return body_bytes, original_tokens, original_tokens, original_tokens

    protected_tokens = 0

    if isinstance(canonical.system, str):
        protected_tokens += count_tokens(canonical.system)
    elif isinstance(canonical.system, list):
        for part in canonical.system:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                protected_tokens += count_tokens(part["text"])

    messages = canonical.messages
    keep_from = max(0, len(messages) - 2)
    last_user_idx = -1
    for i, msg in enumerate(messages):
        if isinstance(msg, dict) and msg.get("role") == "user":
            last_user_idx = i

    for i, msg in enumerate(messages):
        if not isinstance(msg, dict):
            continue
        if i >= keep_from:
            risk = classify_message_risk(msg)
            if risk == "protected":
                content = msg.get("content", "")
                if isinstance(content, str):
                    protected_tokens += count_tokens(content)
                elif isinstance(content, list):
                    for p in content:
                        if isinstance(p, dict) and "text" in p:
                            protected_tokens += count_tokens(p["text"])
            continue
        if msg.get("role") == "user" and i == last_user_idx:
            continue

        risk = classify_message_risk(msg)
        if not can_compress(risk, mode):
            content = msg.get("content", "")
            if isinstance(content, str):
                protected_tokens += count_tokens(content)
            elif isinstance(content, list):
                for p in content:
                    if isinstance(p, dict) and "text" in p:
                        protected_tokens += count_tokens(p["text"])
            continue

        content = msg.get("content")
        if isinstance(content, str):
            msg["content"] = compact_text(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    part["text"] = compact_text(part["text"])

    try:
        new_body = active_adapter.denormalize(canonical)
    except Exception:
        return body_bytes, original_tokens, original_tokens, protected_tokens
    _, sent_tokens = extract_request_tokens(new_body, adapter=active_adapter)
    return new_body, sent_tokens, original_tokens, protected_tokens
