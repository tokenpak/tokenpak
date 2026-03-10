"""
TokenPak — Vendor/Minified Classifier
=====================================

Identifies vendor, minified, and bundled code to prevent garbage indexing.

Heuristics:
- Path patterns: .obsidian/plugins/*, node_modules/*, dist/*, build/*
- File extensions: .min.js, .bundle.css, etc.
- Content signals: long lines, high punctuation, low diversity
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class ClassificationResult:
    """Result of vendor/minified classification."""
    is_vendor: bool
    reason: str  # Why it was classified
    confidence: float  # 0.0-1.0


VENDOR_PATH_PATTERNS = [
    r'\.obsidian[/\\]plugins',
    r'node_modules[/\\]',
    r'[/\\]dist[/\\]',
    r'[/\\]build[/\\]',
    r'[/\\]\.venv[/\\]',
    r'[/\\]venv[/\\]',
    r'\.min\.(js|css|html)',
    r'\.bundle\.(js|css)',
    r'[/\\]vendor[/\\]',
    r'[/\\]third[_-]party',
]

VENDOR_EXTENSIONS = {
    '.bundle.js', '.bundle.css',
    '.min.js', '.min.css', '.min.html',
    '.umd.js',
}


def _has_vendor_path(path: str) -> bool:
    """Check if path matches vendor patterns."""
    for pattern in VENDOR_PATH_PATTERNS:
        if re.search(pattern, path, re.IGNORECASE):
            return True
    return False


def _has_vendor_extension(path: str) -> bool:
    """Check if file has vendor extension."""
    path_lower = path.lower()
    for ext in VENDOR_EXTENSIONS:
        if path_lower.endswith(ext):
            return True
    return False


def _is_minified_content(content: str) -> bool:
    """Heuristic check if content looks minified."""
    if not content or len(content) < 100:
        return False
    
    lines = content.split('\n')
    
    # Check average line length
    avg_line_length = sum(len(line) for line in lines) / max(1, len(lines))
    if avg_line_length > 200:  # Minified files have very long lines
        return True
    
    # Check punctuation density
    punctuation_count = sum(1 for c in content if c in '{}[]();:,=')
    punct_ratio = punctuation_count / len(content)
    if punct_ratio > 0.15:  # Minified has high punctuation
        return True
    
    # Check for typical minified markers
    if re.search(r'var\s+\w+\s*=\s*function', content):  # minified var declarations
        return False  # Might be normal minified
    
    return False


def classify_vendor_minified(
    path: str,
    content: Optional[str] = None,
) -> ClassificationResult:
    """Classify if content is vendor/minified/noise.
    
    Args:
        path: File path
        content: Optional file content for heuristic checks
        
    Returns:
        ClassificationResult with is_vendor bool and reason
    """
    # Strong signals: path patterns
    if _has_vendor_path(path):
        return ClassificationResult(
            is_vendor=True,
            reason=f"Path matches vendor pattern",
            confidence=0.95
        )
    
    if _has_vendor_extension(path):
        return ClassificationResult(
            is_vendor=True,
            reason=f"Extension indicates minified/bundled",
            confidence=0.90
        )
    
    # Weak signal: content heuristics (only if no path signal)
    if content and _is_minified_content(content):
        return ClassificationResult(
            is_vendor=True,
            reason="Content appears minified (long lines, high punctuation)",
            confidence=0.6
        )
    
    # Default: not vendor
    return ClassificationResult(
        is_vendor=False,
        reason="Normal source code",
        confidence=0.99
    )


def should_include_in_index(path: str, content: Optional[str] = None) -> bool:
    """Quick check: should this file be indexed?"""
    result = classify_vendor_minified(path, content)
    # Require high confidence for exclusion (avoid false positives)
    return not (result.is_vendor and result.confidence >= 0.80)


def create_metadata_only_block(path: str, content: str, reason: str) -> dict:
    """Create metadata-only block for vendor files.
    
    Args:
        path: File path
        content: File content (for size/hash)
        reason: Classification reason
        
    Returns:
        Minimal block with metadata only
    """
    import hashlib
    
    size = len(content)
    content_hash = hashlib.sha256(content.encode()).hexdigest()[:16]
    
    return {
        "source_path": path,
        "size_bytes": size,
        "content_hash": content_hash,
        "classification": "vendor",
        "exclude_reason": reason,
        # No content field = metadata-only
        "content": f"[VENDOR] {path} ({size} bytes, hash={content_hash})",
    }
