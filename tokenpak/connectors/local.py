"""Local filesystem connector."""

import fnmatch
from datetime import datetime
from pathlib import Path
from typing import Iterator, Optional

from .base import Connector, RemoteFile


class LocalConnector(Connector):
    """
    Connector for local directories.

    Free tier — no authentication required.
    """

    name = "local"
    tier = "free"

    def connect(self) -> bool:
        """Verify source path exists."""
        return Path(self.config.source_path).is_dir()

    def list_files(self, since: Optional[str] = None) -> Iterator[RemoteFile]:
        """List files in the local directory."""
        root = Path(self.config.source_path).resolve()
        since_ts = None
        if since:
            since_ts = datetime.fromisoformat(since).timestamp()

        max_size = self.config.max_file_size_mb * 1024 * 1024

        for path in root.rglob("*"):
            if not path.is_file():
                continue

            # Check exclude patterns
            rel_path = str(path.relative_to(root))
            if any(fnmatch.fnmatch(rel_path, p) for p in self.config.exclude_patterns):
                continue

            # Check include patterns
            if not any(fnmatch.fnmatch(rel_path, p) for p in self.config.include_patterns):
                continue

            try:
                stat = path.stat()
            except OSError:
                continue

            # Skip large files
            if stat.st_size > max_size:
                continue

            # Skip if not modified since last sync
            if since_ts and stat.st_mtime < since_ts:
                continue

            yield RemoteFile(
                path=rel_path,
                source_id=str(path),
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                file_type=path.suffix.lower().lstrip("."),
            )

    def get_content(self, file: RemoteFile) -> bytes:
        """Read file content."""
        return Path(file.source_id).read_bytes()
