"""Obsidian vault connector with wiki-link awareness."""

from pathlib import Path
from typing import Iterator, Optional
import re

from .local import LocalConnector
from .base import ConnectorConfig, RemoteFile


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
    
    def extract_frontmatter(self, content: str) -> dict:
        """Extract YAML frontmatter from content."""
        if not content.startswith("---"):
            return {}
        
        try:
            end = content.index("---", 3)
            yaml_block = content[3:end].strip()
            
            # Simple key: value parsing (avoids yaml dependency)
            result = {}
            for line in yaml_block.split("\n"):
                if ":" in line:
                    key, val = line.split(":", 1)
                    result[key.strip()] = val.strip().strip('"').strip("'")
            return result
        except (ValueError, IndexError):
            return {}
