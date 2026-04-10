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

from tokenpak.security.dlp import DLPScanner, DLPMatch, DLPBlockError

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


__all__ = ["DLPScanner", "DLPMatch", "DLPBlockError", "secure_write_config"]
