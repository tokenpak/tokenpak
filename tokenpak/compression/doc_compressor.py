"""doc_compressor.py — document-level compression entry point (C4)."""

from __future__ import annotations

from .salience.doc_extractor import DocExtractor, DocExtractionResult


class DocCompressor:
    """
    Document-level compression entry point (C4).

    Wraps :class:`~tokenpak.compression.salience.doc_extractor.DocExtractor`
    to extract high-signal content from markdown/RST documents.

    Parameters
    ----------
    annotation_context : int
        Lines of context to retain after each TODO/FIXME/NOTE/etc.
    include_rst_headings : bool
        Also detect RST-style underline headings.
    """

    def __init__(
        self,
        annotation_context: int = 2,
        include_rst_headings: bool = True,
    ) -> None:
        self._extractor = DocExtractor(
            annotation_context=annotation_context,
            include_rst_headings=include_rst_headings,
        )

    def compress(self, markdown: str) -> str:
        """
        Compress *markdown* and return a compact, high-signal string.

        Parameters
        ----------
        markdown : str
            The document text to compress.

        Returns
        -------
        str
            Compressed output containing headings, annotations, and
            decision items. Never empty for non-empty input.
        """
        result: DocExtractionResult = self._extractor.extract(markdown)
        return result.extracted


def compress_document(content: str, **kwargs) -> str:
    """Module-level helper — compress *content* via :class:`DocCompressor`."""
    return DocCompressor(**kwargs).compress(content)
