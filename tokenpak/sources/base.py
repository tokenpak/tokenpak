"""Base connector interface for data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field


@dataclass
class ConnectorConfig:
    """Configuration for a connector."""

    name: str
    source_path: str  # Local path, URL, or identifier
    auth_token: str | None = None
    sync_interval_minutes: int = 5
    include_patterns: list[str] = field(default_factory=lambda: ["**/*"])
    exclude_patterns: list[str] = field(default_factory=list)
    max_file_size_mb: int = 10


@dataclass
class RemoteFile:
    """Metadata for a file from a remote source."""

    path: str  # Relative path within the source
    source_id: str  # Unique identifier (file ID, URL, etc.)
    size_bytes: int
    modified_at: str  # ISO timestamp
    content_hash: str | None = None
    file_type: str | None = None


class Connector(ABC):
    """
    Base class for data source connectors.

    Connectors handle:
    - Authentication/authorization
    - File listing and delta detection
    - Content retrieval
    - Sync state management
    """

    name: str = "base"
    tier: str = "free"  # free, pro, enterprise

    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config
        self._sync_state: dict[str, object] = {}

    @abstractmethod
    def connect(self) -> bool:
        """
        Establish connection to the data source.
        Returns True if successful.
        """
        pass

    @abstractmethod
    def list_files(self, since: str | None = None) -> Iterator[RemoteFile]:
        """
        List files from the source.

        Args:
            since: Optional ISO timestamp for delta sync

        Yields:
            RemoteFile metadata for each file
        """
        pass

    @abstractmethod
    def get_content(self, file: RemoteFile) -> bytes:
        """
        Retrieve file content.

        Args:
            file: RemoteFile metadata

        Returns:
            File content as bytes
        """
        pass

    def disconnect(self) -> None:
        """Close connection to the data source."""
        pass

    def get_sync_state(self) -> dict[str, object]:
        """Get current sync state for resumable syncs."""
        return self._sync_state

    def set_sync_state(self, state: dict[str, object]) -> None:
        """Restore sync state."""
        self._sync_state = state
