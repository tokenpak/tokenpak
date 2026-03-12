"""Obsidian vault connector with wiki-link awareness."""

import re
from pathlib import Path
from typing import Any, Iterator, Optional

from tokenpak.validation.frontmatter import FrontmatterDiagnostics, parse_frontmatter

from .base import ConnectorConfig, RemoteFile
from .local import LocalConnector


class ObsidianConnector(LocalConnector):
    """
    Connector for Obsidian vaults.

    Free tier — extends local connector with:
    - Wiki-link parsing and resolution
    - Frontmatter extraction
    - Attachment detection
    - Daily notes structure awareness
    """

    name = "obsidian"
    tier = "free"

    # Default Obsidian patterns
    DEFAULT_EXCLUDES = [
        ".obsidian/*",
        ".trash/*",
        "*.canvas",
    ]

    def __init__(self, config: ConnectorConfig):
        # Add Obsidian-specific excludes
        if not config.exclude_patterns:
            config.exclude_patterns = []
        config.exclude_patterns.extend(self.DEFAULT_EXCLUDES)

        # Focus on markdown by default
        if config.include_patterns == ["**/*"]:
            config.include_patterns = ["**/*.md"]

        super().__init__(config)
        self._link_cache: dict = {}
        self.last_frontmatter_diagnostics = FrontmatterDiagnostics()

    def list_files(self, since: Optional[str] = None) -> Iterator[RemoteFile]:
        """List files, enriching with Obsidian metadata."""
        for file in super().list_files(since):
            # Add Obsidian-specific metadata
            file.file_type = self._detect_obsidian_type(file.path)
            yield file

    def _detect_obsidian_type(self, path: str) -> str:
        """Detect Obsidian note type from path and content."""
        p = Path(path)
        name = p.stem

        # Daily notes (common patterns)
        if re.match(r"^\d{4}-\d{2}-\d{2}$", name):
            return "daily-note"

        # Periodic notes
        if re.match(r"^\d{4}-W\d{2}$", name):
            return "weekly-note"
        if re.match(r"^\d{4}-\d{2}$", name):
            return "monthly-note"

        # Templates
        if "template" in path.lower():
            return "template"

        # Attachments
        if p.suffix.lower() in [".png", ".jpg", ".jpeg", ".gif", ".pdf", ".mp3", ".mp4"]:
            return "attachment"

        return "note"

    def extract_links(self, content: str) -> list:
        """Extract wiki-links from content."""
        # [[link]] and [[link|alias]] patterns
        wiki_links = re.findall(r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", content)

        # ![[embed]] patterns
        embeds = re.findall(r"!\[\[([^\]|]+)(?:\|[^\]]+)?\]\]", content)

        return wiki_links + embeds

    def extract_frontmatter(self, content: str, strict: bool = False) -> dict[str, Any]:
        """Extract YAML frontmatter from content.

        In lenient mode (default), malformed/duplicate frontmatter is warned and
        normalized where possible. In strict mode, malformed/duplicate data raises.
        """
        self.last_frontmatter_diagnostics = FrontmatterDiagnostics(mode="strict" if strict else "lenient")
        if not content.startswith("---"):
            return {}

        try:
            end = content.index("\n---", 3)
            yaml_block = content[3:end].strip()
            parsed, diagnostics = parse_frontmatter(yaml_block, strict=strict)
            self.last_frontmatter_diagnostics = diagnostics
            return parsed
        except (ValueError, IndexError):
            if strict:
                raise
            return {}
