"""
visualization.py — Block visualization helpers for Langfuse traces.

Provides functions to render TokenPak block breakdowns as structured
data and ASCII summaries suitable for embedding in trace metadata.
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional


# Block type icons for visual identification in dashboards
BLOCK_TYPE_ICONS = {
    "instructions": "📋",
    "knowledge": "📚",
    "evidence": "🔍",
    "conversation": "💬",
    "memory": "🧠",
    "tool_output": "🔧",
    "system": "⚙️",
    "user": "👤",
    "assistant": "🤖",
}

PRIORITY_LABELS = {
    "critical": "🔴",
    "high": "🟠",
    "medium": "🟡",
    "low": "🟢",
    "optional": "⚪",
}


def block_to_dict(block: Any) -> Dict[str, Any]:
    """
    Convert a TokenPak block to a serializable dict for trace metadata.

    Works with both real Block objects and plain dicts.
    """
    if isinstance(block, dict):
        return {
            "id": block.get("id", "unknown"),
            "type": block.get("type", "unknown"),
            "tokens": block.get("tokens", 0),
            "priority": block.get("priority", "medium"),
            "compacted": block.get("compacted", False),
            "source": block.get("source"),
            "icon": BLOCK_TYPE_ICONS.get(block.get("type", ""), "📄"),
            "priority_label": PRIORITY_LABELS.get(block.get("priority", "medium"), "🟡"),
        }

    # Real Block object
    d = {
        "id": getattr(block, "id", "unknown"),
        "type": getattr(block, "type", "unknown"),
        "tokens": getattr(block, "tokens", 0),
        "priority": getattr(block, "priority", "medium"),
        "compacted": getattr(block, "compacted", False),
        "source": getattr(block, "source", None),
    }
    d["icon"] = BLOCK_TYPE_ICONS.get(d["type"], "📄")
    d["priority_label"] = PRIORITY_LABELS.get(d["priority"], "🟡")
    return d


def blocks_to_metadata(blocks: List[Any], budget: Optional[int] = None) -> Dict[str, Any]:
    """
    Convert a list of blocks into Langfuse-ready trace metadata.

    Returns a structured dict with:
    - block breakdown with icons and stats
    - aggregated totals
    - compression info
    """
    block_dicts = [block_to_dict(b) for b in blocks]
    total_tokens = sum(b["tokens"] for b in block_dicts)

    # Type distribution
    type_counts: Dict[str, int] = {}
    type_tokens: Dict[str, int] = {}
    for b in block_dicts:
        btype = b["type"]
        type_counts[btype] = type_counts.get(btype, 0) + 1
        type_tokens[btype] = type_tokens.get(btype, 0) + b["tokens"]

    type_distribution = {}
    for btype, tokens in type_tokens.items():
        pct = round(tokens / total_tokens * 100, 1) if total_tokens > 0 else 0
        type_distribution[btype] = {
            "count": type_counts[btype],
            "tokens": tokens,
            "percent": pct,
            "icon": BLOCK_TYPE_ICONS.get(btype, "📄"),
        }

    compacted_count = sum(1 for b in block_dicts if b["compacted"])

    result: Dict[str, Any] = {
        "version": "1.0",
        "blocks": block_dicts,
        "block_count": len(block_dicts),
        "total_tokens": total_tokens,
        "compacted_blocks": compacted_count,
        "type_distribution": type_distribution,
    }

    if budget is not None:
        utilization = round(total_tokens / budget * 100, 1) if budget > 0 else 0
        result["budget"] = budget
        result["utilization_pct"] = utilization
        result["tokens_remaining"] = max(0, budget - total_tokens)

    return result


def ascii_block_summary(blocks: List[Any], budget: Optional[int] = None) -> str:
    """
    Render an ASCII block summary for embedding in text traces.

    Example output:
        TokenPak Pack (3 blocks, 880 tokens)
        ├── 📋 instructions  [critical] 150 tok
        ├── 📚 knowledge     [high]     420 tok  [compacted]
        └── 🔍 evidence      [medium]   310 tok  source:pinecone
    """
    block_dicts = [block_to_dict(b) for b in blocks]
    total = sum(b["tokens"] for b in block_dicts)
    budget_str = f"/{budget}" if budget is not None else ""

    lines = [f"TokenPak Pack ({len(block_dicts)} blocks, {total}{budget_str} tokens)"]

    for i, b in enumerate(block_dicts):
        is_last = i == len(block_dicts) - 1
        prefix = "└──" if is_last else "├──"
        icon = b["icon"]
        btype = b["type"].ljust(14)
        priority = f"[{b['priority']}]".ljust(10)
        tok_str = f"{b['tokens']} tok"
        extras = []
        if b["compacted"]:
            extras.append("[compacted]")
        if b.get("source"):
            extras.append(f"src:{b['source']}")
        extra_str = "  " + "  ".join(extras) if extras else ""
        lines.append(f"{prefix} {icon} {btype} {priority} {tok_str}{extra_str}")

    return "\n".join(lines)
