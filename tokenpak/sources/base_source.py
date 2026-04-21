"""SourceAdapter interface for TokenPak Phase 3.3.

On-demand ingestion: fetch a single source → Block with Provenance.
Separate from the sync-oriented Connector (base.py) which is Pro-tier.
"""

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class Provenance:
    """Records the origin of a content block."""

    source_type: str  # filesystem | url | notion | git | confluence | sql | s3
    source_id: str  # Unique identifier (path, URL, page-id, query-hash, etc.)
    source_version: str  # Change-detection token (hash, etag, commit-sha, updated_at)
    fetched_at: str  # ISO-8601 UTC timestamp when content was fetched
    title: str = ""  # Human-readable title if available


class SourceAdapter(ABC):
    """
    Abstract base for on-demand source adapters.

    Each subclass fetches content from one source_type and returns a
    (content, Provenance) pair. The caller is responsible for wrapping
    into a Block and persisting to the registry.
    """

    source_type: str = "unknown"

    @abstractmethod
    def ingest(self, source_id: str, **kwargs) -> tuple:
        """
        Fetch content from the source.

        Args:
            source_id: Source-specific identifier (URL, page_id, file path, etc.)
            **kwargs:  Adapter-specific options (api_token, commit_sha, etc.)

        Returns:
            Tuple of (content: str, provenance: Provenance)

        Raises:
            SourceFetchError on non-recoverable failures.
        """
        ...

    @abstractmethod
    def has_changed(self, source_id: str, cached_version: str, **kwargs) -> bool:
        """
        Check whether the source has changed since cached_version.

        Args:
            source_id:      Source-specific identifier.
            cached_version: Previously stored source_version.
            **kwargs:       Adapter-specific options.

        Returns:
            True if changed (re-ingest needed), False if up to date.
        """
        ...

    # ------------------------------------------------------------------
    # Shared helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _sha256(text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()


class SourceFetchError(Exception):
    """Raised when a source adapter cannot retrieve content."""
