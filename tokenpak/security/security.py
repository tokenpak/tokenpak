# SPDX-License-Identifier: Apache-2.0
"""
tokenpak.security
=================

Centralized security utilities for TokenPak.

Provides:
  - secure_write_config: atomic config-file write with 600 permissions
  - sanitize_model_name: reject model names containing shell/path-injection chars
  - sanitize_cli_arg: reject obviously malicious CLI string inputs
  - redact_pii: scrub known secret patterns from arbitrary strings

These functions are intentionally dependency-free (stdlib only) so they can be
imported early in the stack without triggering heavy framework imports.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# File mode for sensitive config files: owner read/write only.
_CONFIG_FILE_MODE = 0o600

# Allowed model-name pattern: letters, digits, hyphens, dots, underscores, slashes
# (e.g. "gpt-4o", "claude-sonnet-4-6", "google/gemini-2-flash")
_MODEL_NAME_RE = re.compile(r"^[A-Za-z0-9_\-\./]{1,256}$")

# Path traversal sequences explicitly blocked inside valid character set
_MODEL_PATH_TRAVERSAL_RE = re.compile(r"\.\.")

# Patterns that indicate injection attempts in free-form string inputs
_INJECTION_RE = re.compile(
    r"(\.\./|\.\.\\|;|\||&&|\$\(|`|<script|javascript:)",
    re.IGNORECASE,
)

# Patterns to scrub from arbitrary strings (PII / credentials)
_REDACT_PATTERNS = [
    (re.compile(r"(sk-[A-Za-z0-9]{10,})"), "[REDACTED-SK]"),
    (re.compile(r"(Bearer\s+)[A-Za-z0-9\-_\.]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(X-TokenPak-Key:\s*)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(Authorization:\s*Bearer\s+)\S+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(\"api_key\"\s*:\s*\")[^\"]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(api[_-]?key[=:]\s*)\S+", re.IGNORECASE), r"\1[REDACTED]"),
]


# ---------------------------------------------------------------------------
# Secure config file writer
# ---------------------------------------------------------------------------


def secure_write_config(path: Path, data: Dict[str, Any]) -> None:
    """
    Write *data* as pretty-printed JSON to *path* with mode 600.

    Uses a write-then-rename pattern so the file is never partially written.
    Parent directory must already exist (caller should call ``mkdir`` first).

    Parameters
    ----------
    path : Path
        Destination config file path.
    data : dict
        JSON-serialisable dictionary to write.

    Raises
    ------
    OSError
        If the parent directory does not exist or write fails.
    """
    path = Path(path)
    parent = path.parent

    # Write to a temp file in the same directory, then atomically rename.
    fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".tokenpak-tmp-")
    try:
        os.chmod(tmp_path, _CONFIG_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        # Clean up temp file on failure; don't leak sensitive data
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def ensure_config_permissions(path: Path) -> bool:
    """
    Ensure *path* has mode 600 (owner read/write only).

    Returns True if permissions were already correct or were successfully fixed.
    Returns False if the path does not exist.
    """
    path = Path(path)
    if not path.exists():
        return False
    current = path.stat().st_mode & 0o777
    if current != _CONFIG_FILE_MODE:
        path.chmod(_CONFIG_FILE_MODE)
    return True


# ---------------------------------------------------------------------------
# Input sanitizers
# ---------------------------------------------------------------------------


def sanitize_model_name(model: str) -> str:
    """
    Validate and return *model* if it matches the allowed character set.

    Allowed: ``[A-Za-z0-9_\\-\\./]{1,256}``

    Raises
    ------
    ValueError
        If the model name contains disallowed characters or is too long.
    """
    if not isinstance(model, str):
        raise ValueError("model must be a string")
    if not _MODEL_NAME_RE.match(model):
        raise ValueError(
            f"Invalid model name {model!r}. "
            "Only letters, digits, hyphens, dots, underscores, and slashes are allowed."
        )
    if _MODEL_PATH_TRAVERSAL_RE.search(model):
        raise ValueError(
            f"Invalid model name {model!r}. Path traversal sequences ('..') are not allowed."
        )
    return model


def sanitize_cli_arg(value: str, name: str = "argument") -> str:
    """
    Reject CLI string inputs that contain obvious injection patterns.

    Parameters
    ----------
    value : str
        The raw CLI argument value.
    name : str
        Human-readable name for error messages.

    Raises
    ------
    ValueError
        If *value* contains shell metacharacters or path-traversal sequences.
    """
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if _INJECTION_RE.search(value):
        raise ValueError(f"Invalid {name}: contains disallowed characters or sequences.")
    return value


# ---------------------------------------------------------------------------
# PII scrubbing (for non-logger paths, e.g. error messages, DB fields)
# ---------------------------------------------------------------------------


def redact_pii(text: str) -> str:
    """
    Remove known credential and PII patterns from *text*.

    Safe to call on log messages, error strings, or any text before
    it is written to disk or sent externally.

    Parameters
    ----------
    text : str
        Arbitrary string that may contain sensitive data.

    Returns
    -------
    str
        A copy of *text* with credential values replaced by ``[REDACTED]``.
    """
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Temp-file cleanup helper
# ---------------------------------------------------------------------------


def safe_temp_file(
    suffix: str = "",
    prefix: str = ".tokenpak-tmp-",
    dir: Optional[Path] = None,
) -> tuple[int, str]:
    """
    Create a temporary file with mode 600 and return ``(fd, path)``.

    The caller is responsible for closing *fd* and unlinking *path* when done.
    This ensures sensitive temp data is never world-readable.
    """
    fd, path = tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=dir)
    os.chmod(path, _CONFIG_FILE_MODE)
    return fd, path
