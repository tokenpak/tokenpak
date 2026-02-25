"""TokenPak wire format generator."""

import hashlib
from typing import List, Dict


def make_slice_id(content: str, ref: str) -> str:
    """Generate a short unique slice_id from content + ref."""
    digest = hashlib.sha256(f"{ref}:{content}".encode()).hexdigest()[:8]
    return f"s_{digest}"


def pack(blocks: List[Dict], budget: int, metadata: Dict | None = None) -> str:
    """
    Produce TOKPAK wire format.

    Expected block keys: ref, type, quality, tokens, content
    Each block gets a unique slice_id for citation tracking.
    """
    metadata = metadata or {}
    used = sum(int(b.get("tokens", 0)) for b in blocks)

    lines = [
        "TOKPAK:1",
        f"BUDGET: {{max:{budget}, used:{used}}}",
        f"BLOCKS: {len(blocks)}",
    ]

    if metadata:
        meta_parts = [f"{k}={v}" for k, v in metadata.items()]
        lines.append("META: " + ", ".join(meta_parts))

    for block in blocks:
        content = block.get("content", "").strip()
        ref = block.get("ref", "unknown")
        slice_id = block.get("slice_id") or make_slice_id(content, ref)
        lines.extend([
            "---",
            (
                f"[REF: {ref}] "
                f"[TYPE: {block.get('type','unknown')}] "
                f"[QUALITY: {float(block.get('quality',1.0)):.2f}] "
                f"[TOKENS: {int(block.get('tokens',0))}] "
                f"[SLICE: {slice_id}]"
            ),
            content,
        ])

    lines.append("---")
    return "\n".join(lines)
