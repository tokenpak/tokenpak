# SPDX-License-Identifier: Apache-2.0
"""TokenPak wire format generator."""

import hashlib
from typing import Dict, List


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

        # Build header line
        header = (
            f"[REF: {ref}] "
            f"[TYPE: {block.get('type', 'unknown')}] "
            f"[QUALITY: {float(block.get('quality', 1.0)):.2f}] "
            f"[TOKENS: {int(block.get('tokens', 0))}] "
            f"[SLICE: {slice_id}]"
        )

        # Append provenance if present (from SourceAdapter)
        prov = block.get("provenance")
        if prov is not None:
            src_type = getattr(prov, "source_type", None) or prov.get("source_type", "")
            src_id = getattr(prov, "source_id", None) or prov.get("source_id", "")
            src_ver = getattr(prov, "source_version", None) or prov.get("source_version", "")
            if src_type and src_id:
                header += f" [SOURCE: {src_type}:{src_id}]"
            if src_ver:
                header += f" [VERSION: {src_ver[:16]}]"

        lines.extend(["---", header, content])

    lines.append("---")
    return "\n".join(lines)
