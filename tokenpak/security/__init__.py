# SPDX-License-Identifier: Apache-2.0
"""
tokenpak.security
=================

Security subpackage for TokenPak.
Exports the DLP scanner and supporting types, plus secure config-file utilities.

Free-tier subset of the I4 Security/PII/DLP architecture component:
gitleaks-pattern secret scanner (warn/redact/block modes).
Full PII/DLP remains Enterprise (I4).
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from tokenpak.security.dlp import DLPBlockError, DLPMatch, DLPScanner

# File mode for sensitive config files: owner read/write only.
_CONFIG_FILE_MODE = 0o600


def secure_write_config(path: Path, data: Dict[str, Any]) -> None:
    """
    Write *data* as pretty-printed JSON to *path* with mode 600.

    Uses a write-then-rename pattern so the file is never partially written.
    Parent directory must already exist (caller should call ``mkdir`` first).
    """
    path = Path(path)
    parent = path.parent
    fd, tmp_path = tempfile.mkstemp(dir=parent, prefix=".tokenpak-tmp-")
    try:
        os.chmod(tmp_path, _CONFIG_FILE_MODE)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
            fh.write("\n")
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


import re as _re

_MODEL_NAME_RE = _re.compile(r"^[A-Za-z0-9_\-\./]{1,256}$")
_MODEL_PATH_TRAVERSAL_RE = _re.compile(r"\.\.")

_INJECTION_RE = _re.compile(
    r"(\.\./|\.\.\\|;|\||&&|\$\(|`|<script|javascript:)",
    _re.IGNORECASE,
)

_REDACT_PATTERNS = [
    (_re.compile(r"(sk-[A-Za-z0-9]{10,})"), "[REDACTED-SK]"),
    (_re.compile(r"(Bearer\s+)[A-Za-z0-9\-_\.]+", _re.IGNORECASE), r"\1[REDACTED]"),
    (_re.compile(r"(X-TokenPak-Key:\s*)\S+", _re.IGNORECASE), r"\1[REDACTED]"),
    (_re.compile(r"(Authorization:\s*Bearer\s+)\S+", _re.IGNORECASE), r"\1[REDACTED]"),
    (_re.compile(r"(\"api_key\"\s*:\s*\")[^\"]+", _re.IGNORECASE), r"\1[REDACTED]"),
    (_re.compile(r"(api[_-]?key[=:]\s*)\S+", _re.IGNORECASE), r"\1[REDACTED]"),
]


def redact_pii(text: str) -> str:
    """Remove known credential and PII patterns from *text*."""
    for pattern, replacement in _REDACT_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def sanitize_cli_arg(value: str, name: str = "argument") -> str:
    """Reject CLI string inputs that contain obvious injection patterns."""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if _INJECTION_RE.search(value):
        raise ValueError(f"Invalid {name}: contains disallowed characters or sequences.")
    return value


def safe_temp_file(
    suffix: str = "",
    prefix: str = ".tokenpak-tmp-",
    dir: "Path | None" = None,
) -> "tuple[int, str]":
    """Create a temporary file with mode 600 and return (fd, path)."""
    import tempfile as _tempfile
    fd, path = _tempfile.mkstemp(suffix=suffix, prefix=prefix, dir=dir)
    os.chmod(path, _CONFIG_FILE_MODE)
    return fd, path


def sanitize_model_name(model: str) -> str:
    """Validate and return *model* if it matches the allowed character set."""
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


def ensure_config_permissions(path: Path) -> bool:
    """
    Ensure *path* has mode 600 (owner read/write only).

    Returns True if the path exists (permissions corrected if needed).
    Returns False if the path does not exist.
    """
    path = Path(path)
    if not path.exists():
        return False
    current = path.stat().st_mode & 0o777
    if current != _CONFIG_FILE_MODE:
        path.chmod(_CONFIG_FILE_MODE)
    return True


__all__ = ['DLPScanner', 'DLPMatch', 'DLPBlockError', 'secure_write_config', 'sanitize_model_name', 'ensure_config_permissions', 'redact_pii', 'safe_temp_file', 'sanitize_cli_arg', 'dlp']
