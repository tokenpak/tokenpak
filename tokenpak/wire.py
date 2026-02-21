"""TokenPak wire format generator."""

from typing import List, Dict


def pack(blocks: List[Dict], budget: int, metadata: Dict | None = None) -> str:
    """
    Produce TOKPAK wire format.

    Expected block keys: ref, type, quality, tokens, content
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
        lines.extend([
            "---",
            (
                f"[REF: {block.get('ref','unknown')}] "
                f"[TYPE: {block.get('type','unknown')}] "
                f"[QUALITY: {float(block.get('quality',1.0)):.2f}] "
                f"[TOKENS: {int(block.get('tokens',0))}]"
            ),
            block.get("content", "").strip(),
        ])

    lines.append("---")
    return "\n".join(lines)
