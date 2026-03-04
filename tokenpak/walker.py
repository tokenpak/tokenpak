"""Directory walker with file type detection."""

import os
from pathlib import Path
from typing import List, Tuple

# Supported file extensions → type mapping
FILE_TYPES = {
    ".md": "text", ".txt": "text", ".html": "text", ".htm": "text",
    ".rst": "text", ".adoc": "text",
    ".py": "code", ".js": "code", ".ts": "code", ".jsx": "code",
    ".tsx": "code", ".go": "code", ".rs": "code", ".rb": "code",
    ".java": "code", ".c": "code", ".cpp": "code", ".h": "code",
    ".sh": "code", ".bash": "code", ".sql": "code", ".css": "code",
    ".json": "data", ".csv": "data", ".tsv": "data",
    ".yaml": "data", ".yml": "data", ".toml": "data",
    ".env": "data", ".cfg": "data", ".ini": "data",
    ".pdf": "pdf",
    ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".gif": "image", ".webp": "image", ".svg": "image",
    ".mp3": "audio", ".wav": "audio", ".m4a": "audio",
    ".flac": "audio", ".ogg": "audio",
    ".mp4": "video", ".mkv": "video", ".avi": "video",
    ".mov": "video", ".webm": "video",
}

# Basename-based mappings for dotfiles with no "suffix" via pathlib.
FILE_NAME_TYPES = {
    ".env": "data",
}

# Directories to always skip
SKIP_DIRS = {
    "node_modules", ".git", "__pycache__", ".venv", "venv",
    ".env", ".tox", ".mypy_cache", ".pytest_cache", "dist",
    "build", ".next", ".nuxt", "target", ".cargo",
}

# Max file size to process (10MB)
MAX_FILE_SIZE = 10 * 1024 * 1024


def walk_directory(root: str, max_size: int = MAX_FILE_SIZE) -> List[Tuple[str, str, int]]:
    """
    Recursively walk a directory and return processable files.
    
    Returns:
        List of (absolute_path, file_type, size_bytes) tuples.
    """
    root = os.path.abspath(root)
    results = []

    for dirpath, dirnames, filenames in os.walk(root):
        # Prune skip directories (in-place modification)
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]

        for filename in filenames:
            filepath = os.path.join(dirpath, filename)
            file_type = detect_file_type(filepath)
            if file_type is None:
                continue

            try:
                size = os.path.getsize(filepath)
            except OSError:
                continue

            if size == 0 or size > max_size:
                continue

            results.append((filepath, file_type, size))

    return sorted(results, key=lambda x: x[0])


def detect_file_type(path: str) -> str | None:
    """Detect file type from extension."""
    p = Path(path)
    ext = p.suffix.lower()
    if ext in FILE_TYPES:
        return FILE_TYPES[ext]
    return FILE_NAME_TYPES.get(p.name.lower())
