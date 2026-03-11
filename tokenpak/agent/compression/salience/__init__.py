"""
TokenPak — Salience Extractors
===============================

Content-aware, deterministic extractors that strip low-signal bulk from
logs, code, and documentation.  Each extractor is callable independently
or via :func:`extract` which auto-detects the content type.

Usage::

    from tokenpak.agent.compression.salience import extract, ContentType

    result = extract(text)
    # result.content_type  → detected type
    # result.extracted     → compact, high-signal string
    # result.stats         → dict of per-extractor statistics

"""

from .detect import ContentType, detect_content_type
from .log_extractor import LogExtractor
from .code_extractor import CodeExtractor
from .doc_extractor import DocExtractor
from .router import SalientResult, extract

__all__ = [
    "ContentType",
    "detect_content_type",
    "LogExtractor",
    "CodeExtractor",
    "DocExtractor",
    "SalientResult",
    "extract",
]
