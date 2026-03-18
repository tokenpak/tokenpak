"""
salience.router — Content-type detection + extractor dispatch.

The public entry point is :func:`extract`, which auto-detects the content
type and routes to the appropriate extractor, returning a unified
:class:`SalientResult`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from .detect import ContentType, detect_content_type
from .log_extractor import LogExtractor, LogExtractionResult
from .code_extractor import CodeExtractor, CodeExtractionResult
from .doc_extractor import DocExtractor, DocExtractionResult


@dataclass
class SalientResult:
    """Unified output from :func:`extract`."""

    content_type: ContentType
    extracted: str
    lines_in: int
    lines_out: int
    stats: Dict[str, Any] = field(default_factory=dict)
    passthrough: bool = False   # True if type=UNKNOWN; raw text preserved

    @property
    def reduction_pct(self) -> float:
        if self.lines_in == 0:
            return 0.0
        return round((1 - self.lines_out / self.lines_in) * 100, 1)


def extract(
    text: str,
    *,
    content_type: Optional[ContentType] = None,
    log_extractor: Optional[LogExtractor] = None,
    code_extractor: Optional[CodeExtractor] = None,
    doc_extractor: Optional[DocExtractor] = None,
) -> SalientResult:
    """
    Detect content type and extract salient content.

    Parameters
    ----------
    text : str
        Raw content to process.
    content_type : ContentType, optional
        Override auto-detection.  Pass ``ContentType.LOG``, ``CODE``, or
        ``DOC`` to skip detection.
    log_extractor, code_extractor, doc_extractor : optional
        Pre-configured extractor instances.  Defaults are constructed when
        not provided.

    Returns
    -------
    SalientResult
        Unified result with ``.extracted`` (compact text) and ``.stats``.
    """
    ct = content_type if content_type is not None else detect_content_type(text)

    if ct == ContentType.LOG:
        ext = log_extractor or LogExtractor()
        r: LogExtractionResult = ext.extract(text)
        return SalientResult(
            content_type=ct,
            extracted=r.extracted,
            lines_in=r.lines_in,
            lines_out=r.lines_out,
            stats={
                "error_count": r.error_count,
                "warn_count": r.warn_count,
                "unique_stack_sigs": r.unique_stack_sigs,
                "timestamp_first": r.timestamp_first,
                "timestamp_last": r.timestamp_last,
                "reduction_pct": r.reduction_pct,
            },
        )

    if ct == ContentType.CODE:
        ext2 = code_extractor or CodeExtractor()
        r2: CodeExtractionResult = ext2.extract(text)
        return SalientResult(
            content_type=ct,
            extracted=r2.extracted,
            lines_in=r2.lines_in,
            lines_out=r2.lines_out,
            stats={
                "imports_found": r2.imports_found,
                "functions_found": r2.functions_found,
                "changed_functions": r2.changed_functions,
                "test_targets": r2.test_targets,
                "is_diff": r2.is_diff,
                "reduction_pct": r2.reduction_pct,
            },
        )

    if ct == ContentType.DOC:
        ext3 = doc_extractor or DocExtractor()
        r3: DocExtractionResult = ext3.extract(text)
        return SalientResult(
            content_type=ct,
            extracted=r3.extracted,
            lines_in=r3.lines_in,
            lines_out=r3.lines_out,
            stats={
                "headings": r3.headings,
                "annotation_count": r3.annotation_count,
                "decision_count": r3.decision_count,
                "reduction_pct": r3.reduction_pct,
            },
        )

    # UNKNOWN — pass through unchanged
    lines = text.splitlines()
    return SalientResult(
        content_type=ct,
        extracted=text,
        lines_in=len(lines),
        lines_out=len(lines),
        passthrough=True,
        stats={"reason": "content type unknown; passthrough"},
    )
