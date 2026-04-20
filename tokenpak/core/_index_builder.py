# SPDX-License-Identifier: Apache-2.0
"""tokenpak/core.py — Vault index builder (proxy-compatible format).

Provides index_directory() for the rebuild-vault-index.sh script.
Output format: ~/vault/.tokenpak/index.json + blocks/*.txt
Compatible with the proxy server's VaultIndex reader.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

# Precomputation pipeline (lazy import to avoid circular deps)
try:
    from .precompute import recompute_all as _recompute_all

    _PRECOMPUTE_AVAILABLE = True
except ImportError:
    _PRECOMPUTE_AVAILABLE = False
    _recompute_all = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# File classification
# ---------------------------------------------------------------------------

_CODE_EXTS = {
    ".py",
    ".sh",
    ".bash",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".go",
    ".rs",
    ".rb",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".sql",
    ".css",
    ".php",
    ".cs",
    ".swift",
    ".kt",
}
_CONFIG_EXTS = {
    ".yaml",
    ".yml",
    ".json",
    ".toml",
    ".cfg",
    ".ini",
    ".xml",
    ".env",
}
_TEXT_EXTS = {
    ".md",
    ".txt",
    ".html",
    ".htm",
    ".rst",
    ".adoc",
    ".org",
}
_LEGAL_NAMES = {"license", "copying", "licence", "notice", "patents"}

# Dirs to always skip
_SKIP_DIRS = {
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "target",
    ".cargo",
}

# Path patterns that indicate protected/sensitive content
_PROTECTED_PATTERNS = [
    r"^00_kevin/",
    r"^03_agent_packs/",
    r"^agents/",
    r"/credentials/",
    r"/secrets/",
    r"/private/",
]

_MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB


def _classify(rel_path: str) -> tuple[str, bool]:
    """Return (risk_class, must_keep) for a relative vault path."""
    p = Path(rel_path)
    ext = p.suffix.lower()
    name_lower = p.name.lower()
    path_lower = rel_path.lower()

    # Legal files
    stem_lower = p.stem.lower()
    if stem_lower in _LEGAL_NAMES or name_lower in _LEGAL_NAMES:
        return "legal", False

    # Protected patterns (must_keep=True, no compression)
    for pat in _PROTECTED_PATTERNS:
        if re.search(pat, path_lower):
            return "protected", True

    if ext in _CODE_EXTS:
        return "code", False
    if ext in _CONFIG_EXTS:
        return "config", False

    return "narrative", False


def _make_block_id(rel_path: str) -> str:
    """Convert relative path to block_id (lowercase, / → .)."""
    return rel_path.lower().replace("/", ".").replace("\\", ".")


def _load_ignore_patterns(vault_dir: Path) -> list[str]:
    """Load .tokenpakignore patterns."""
    ignore_file = vault_dir / ".tokenpakignore"
    patterns = []
    if ignore_file.exists():
        for line in ignore_file.read_text(errors="replace").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                patterns.append(line)
    return patterns


def _is_ignored(rel_path: str, patterns: list[str]) -> bool:
    """Check if a relative path matches any ignore pattern."""
    for pat in patterns:
        # Match against full path or just filename
        if fnmatch.fnmatch(rel_path, pat):
            return True
        if fnmatch.fnmatch(Path(rel_path).name, pat):
            return True
        # Check if any path component matches
        parts = rel_path.replace("\\", "/").split("/")
        for part in parts:
            if fnmatch.fnmatch(part, pat):
                return True
    return False


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token."""
    return max(1, len(text) // 4)


# ---------------------------------------------------------------------------
# Registry (return value of index_directory)
# ---------------------------------------------------------------------------


class IndexRegistry:
    """Return value of index_directory(). Has .blocks and .tokenpak_dir."""

    def __init__(self, vault_dir: Path, blocks: dict):
        self.vault_dir = vault_dir
        self.tokenpak_dir = vault_dir / ".tokenpak"
        self.blocks = blocks  # block_id -> block metadata dict


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def index_directory(
    vault: Path | str,
    verbose: bool = False,
    on_progress: Optional[Callable[[str], None]] = None,
) -> IndexRegistry:
    """Walk vault directory, build proxy-compatible index.json + blocks/*.txt.

    Args:
        vault: Path to vault root directory
        verbose: Print each indexed file
        on_progress: Optional callback(rel_path) for each file indexed

    Returns:
        IndexRegistry with .blocks and .tokenpak_dir
    """
    vault = Path(vault).expanduser().resolve()
    tokenpak_dir = vault / ".tokenpak"
    blocks_dir = tokenpak_dir / "blocks"
    blocks_dir.mkdir(parents=True, exist_ok=True)

    ignore_patterns = _load_ignore_patterns(vault)

    stats = {"scanned": 0, "indexed": 0, "updated": 0, "skipped": 0, "errors": 0}
    new_blocks: dict[str, dict] = {}

    # Load existing index for incremental update
    index_file = tokenpak_dir / "index.json"
    old_blocks: dict[str, dict] = {}
    if index_file.exists():
        try:
            old_data = json.loads(index_file.read_text())
            old_blocks = old_data.get("blocks", {})
        except Exception:
            pass

    for dirpath, dirnames, filenames in os.walk(vault):
        # Prune skip dirs
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _SKIP_DIRS and not (d.startswith(".") and d != ".tokenpakignore")
        ]
        # Skip .tokenpak itself
        dirnames[:] = [d for d in dirnames if d != ".tokenpak"]

        for filename in filenames:
            filepath = Path(dirpath) / filename
            try:
                rel_path = str(filepath.relative_to(vault))
            except ValueError:
                continue

            stats["scanned"] += 1

            # Check ignored
            if _is_ignored(rel_path, ignore_patterns):
                stats["skipped"] += 1
                continue

            # Check extension
            ext = filepath.suffix.lower()
            supported_exts = _CODE_EXTS | _CONFIG_EXTS | _TEXT_EXTS
            name_lower = filepath.name.lower()
            if ext not in supported_exts and name_lower not in {".gitignore", ".tokenpakignore"}:
                stats["skipped"] += 1
                continue

            # Check size
            try:
                size = filepath.stat().st_size
            except OSError:
                stats["skipped"] += 1
                continue

            if size == 0 or size > _MAX_FILE_SIZE:
                stats["skipped"] += 1
                continue

            # Read content
            try:
                content = filepath.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                stats["errors"] += 1
                continue

            content_hash = hashlib.sha256(content.encode()).hexdigest()
            block_id = _make_block_id(rel_path)
            risk_class, must_keep = _classify(rel_path)
            raw_tokens = _estimate_tokens(content)
            block_file = blocks_dir / f"{block_id}.txt"

            # Incremental: check if content changed
            old = old_blocks.get(block_id)
            if old and old.get("content_hash") == content_hash and block_file.exists():
                # Unchanged — keep existing metadata
                new_blocks[block_id] = old
                stats["indexed"] += 1
                continue

            # Write block content file
            try:
                block_file.write_text(content, encoding="utf-8")
            except OSError:
                stats["errors"] += 1
                continue

            new_blocks[block_id] = {
                "block_id": block_id,
                "source_path": rel_path,
                "content_hash": content_hash,
                "risk_class": risk_class,
                "must_keep": must_keep,
                "compression": "none" if must_keep else "eligible",
                "raw_tokens": raw_tokens,
                "raw_size": size,
                "version": 1,
                "indexed_at": datetime.now(timezone.utc).isoformat(),
                "source_type": "filesystem",
                "source_id": rel_path,
                "source_version": content_hash,
            }
            stats["indexed"] += 1
            stats["updated"] += 1

            if verbose:
                print(f"  ✓ {rel_path}")
            if on_progress:
                on_progress(rel_path)

    # Prune blocks for files that no longer exist
    for bid in list(new_blocks.keys()):
        src = vault / new_blocks[bid]["source_path"]
        if not src.exists():
            del new_blocks[bid]
            block_file = blocks_dir / f"{bid}.txt"
            if block_file.exists():
                block_file.unlink()

    # Write index.json
    index_data = {
        "version": "1.0",
        "meta": {
            "source_dir": str(vault),
            "indexed_at": datetime.now(timezone.utc).isoformat(),
            "stats": stats,
        },
        "blocks": new_blocks,
    }
    index_file.write_text(json.dumps(index_data, indent=2), encoding="utf-8")

    if verbose:
        print(f"\n✅ Index written: {index_file}")
        print(f"   Blocks: {len(new_blocks)}")

    return IndexRegistry(vault, new_blocks)
