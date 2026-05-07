"""tokenpak.vault.chunk_shapes — re-export shim for chunk shaping (note: source is chunk_shaping)."""
from tokenpak.vault.chunk_shaping import (
    CHUNK_SHAPES,
    _shape_code_contiguous,
    _shape_decision_summary,
    _shape_fact_chunk,
    _shape_section_header,
    apply_shape,
    get_shape_for_intent,
    reshape_chunks,
)

__all__ = [
    "CHUNK_SHAPES",
    "apply_shape",
    "get_shape_for_intent",
    "reshape_chunks",
    "_shape_code_contiguous",
    "_shape_fact_chunk",
    "_shape_decision_summary",
    "_shape_section_header",
]
