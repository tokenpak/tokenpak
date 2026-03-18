"""TokenPak → LiteLLM message formatter.

Converts a TokenPak (BlockRegistry or list of Block objects) into a
standard ``messages`` list suitable for ``litellm.completion()``.

The compiled pack is inserted as a ``system`` message containing the
TOKPAK wire format, followed by any existing user messages.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Lazy-import to avoid hard dependency on tokenpak internals at module load time.


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


def blocks_to_messages(
    blocks: List[Any],
    budget: int = 8000,
    compaction: str = "balanced",
    existing_messages: Optional[List[Dict]] = None,
) -> List[Dict[str, str]]:
    """Convert a list of ``Block`` objects into LiteLLM ``messages``.

    Args:
        blocks: List of ``tokenpak.registry.Block`` objects.
        budget: Maximum token budget for the compiled pack.
        compaction: Compaction strategy — ``"none"``, ``"balanced"``, or
            ``"aggressive"``.
        existing_messages: Existing messages list to prepend system block to.

    Returns:
        A ``messages`` list with the compiled TokenPak as the first
        ``system`` message, followed by ``existing_messages``.
    """
    from tokenpak.wire import pack as wire_pack

    # Apply compaction if requested and content exceeds budget
    wire_blocks = []
    total_tokens = 0

    for block in blocks:
        raw = getattr(block, "compressed_content", None) or getattr(block, "content", "")  # type: ignore
        tokens = getattr(block, "compressed_tokens", None) or _estimate_tokens(raw)  # type: ignore

        if compaction != "none" and total_tokens + tokens > budget:
            if compaction == "aggressive":
                # Hard-truncate to fit budget
                remaining = max(0, budget - total_tokens)
                if remaining < 50:
                    break
                approx_chars = remaining * 4
                raw = (raw or "")[:approx_chars] + "\n…[truncated]"
                tokens = _estimate_tokens(raw)
            elif compaction == "balanced":
                # Try engine compaction first
                try:
                    from tokenpak.engines import get_engine
                    from tokenpak.engines.base import CompactionHints

                    engine = get_engine("heuristic")
                    remaining = max(50, budget - total_tokens)
                    hints = CompactionHints(target_tokens=remaining, aggressive=False)
                    raw = engine.compact(raw, hints)  # type: ignore
                    tokens = _estimate_tokens(raw)
                except Exception:
                    # Fallback: plain truncate
                    remaining = max(0, budget - total_tokens)
                    if remaining < 50:
                        break
                    raw = (raw or "")[: remaining * 4] + "\n…"
                    tokens = _estimate_tokens(raw)

        wire_blocks.append(
            {
                "ref": getattr(block, "path", "unknown"),
                "type": getattr(block, "file_type", "text"),
                "quality": getattr(block, "quality_score", 1.0),
                "tokens": tokens,
                "content": raw,
                "slice_id": getattr(block, "slice_id", ""),
            }
        )
        total_tokens += tokens

    system_content = wire_pack(wire_blocks, budget)

    messages: List[Dict[str, str]] = [{"role": "system", "content": system_content}]
    if existing_messages:
        # Skip any existing system message — we've replaced it
        for msg in existing_messages:
            if msg.get("role") != "system":
                messages.append(msg)

    return messages


def compile_pack(
    pack: Any,
    budget: int = 8000,
    compaction: str = "balanced",
    existing_messages: Optional[List[Dict]] = None,
) -> List[Dict[str, str]]:
    """Compile a TokenPak (BlockRegistry, list of Blocks, or dict) → messages.

    Accepts:
    - ``tokenpak.registry.BlockRegistry`` instance
    - ``list`` of ``tokenpak.registry.Block`` objects
    - Raw ``dict`` in the form ``{"version": "1.0", "blocks": [...]}``

    Returns:
        Standard LiteLLM ``messages`` list.
    """
    if isinstance(pack, list):
        blocks = pack
    elif isinstance(pack, dict):
        # Raw wire-format dict — reconstruct minimal block objects
        blocks = _dict_to_blocks(pack)
    else:
        # Assume BlockRegistry — iterate its blocks
        try:
            blocks = list(pack.all_blocks()) if hasattr(pack, "all_blocks") else []
        except Exception as exc:
            raise TypeError(
                f"Cannot compile TokenPak from {type(pack).__name__}: {exc}. "
                f"Expected: dict with 'blocks' key, list of Block objects, or BlockRegistry with all_blocks() method."
            ) from exc

    # Validate budget before compacting
    if budget <= 0:
        raise ValueError(f"budget must be positive (got {budget})")
    
    if budget < 50:
        raise ValueError(
            f"budget too small ({budget} tokens). Minimum practical budget is 50 tokens. "
            f"Increase budget or disable compaction."
        )

    return blocks_to_messages(
        blocks,
        budget=budget,
        compaction=compaction,
        existing_messages=existing_messages,
    )


def _dict_to_blocks(pack_dict: Dict) -> List[Any]:
    """Convert a raw dict (e.g. from JSON) to pseudo-Block objects."""
    from types import SimpleNamespace

    raw_blocks = pack_dict.get("blocks", [])
    result = []
    for b in raw_blocks:
        content = b.get("content", "")
        ns = SimpleNamespace(
            path=b.get("ref", b.get("path", "unknown")),
            compressed_content=content,
            content=content,
            file_type=b.get("type", "text"),
            quality_score=float(b.get("quality", 1.0)),
            compressed_tokens=b.get("tokens") or _estimate_tokens(content),
            raw_tokens=b.get("raw_tokens") or _estimate_tokens(content),
            slice_id=b.get("slice_id", ""),
        )
        result.append(ns)
    return result
