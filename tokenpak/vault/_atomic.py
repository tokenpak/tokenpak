"""Atomic file-write helper for vault index artefacts.

Used to publish ``index.json``, ``blocks/*.txt`` and related read-back state
so concurrent readers (e.g. ``VaultIndex._load`` in the proxy process)
never observe a half-written file. POSIX ``os.replace`` is atomic when the
source and destination share a filesystem; placing the tmp file in the
target's parent directory guarantees that.

Callers include ``VaultHealth.repair``, ``vault/blocks.py`` (block/slice
store flush), ``vault/indexer.py`` (stats snapshot),
``vault/sources/claude_transcript.py`` (block + index publish),
``vault/retrieval/vector_local.py`` (embedding index artefacts),
``vault/retrieval/vault_index.py`` (BM25 cache), and
``compression/core.py`` (block + index publish).
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Union


def _atomic_write(
    target: Union[str, Path],
    content: Union[str, bytes, bytearray],
    *,
    fsync: bool = True,
) -> None:
    """Write ``content`` to ``target`` atomically.

    Strategy: write to a same-directory tmp file (``<basename>.tmp.<pid>.<8-hex>``
    to avoid cross-writer collision), optionally ``fsync``, then ``os.replace``
    to swap in. Readers see EITHER the old file OR the new file — never a
    partial one.

    Args:
        target: Final destination path.
        content: ``str`` or ``bytes``/``bytearray`` payload.
        fsync: When True (default), flush + ``os.fsync`` the tmp file before
            the swap. Correctness-first default; the same-directory tmp +
            ``os.replace`` strategy is what makes the swap atomic, fsync
            additionally ensures durability across power loss.

    Raises:
        OSError: Propagated from open/write/fsync/replace. Caller is
            expected to wrap as needed (``VaultHealth.repair`` already does).
    """
    target_path = Path(target)
    tmp = target_path.parent / f"{target_path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex[:8]}"

    is_binary = isinstance(content, (bytes, bytearray))
    mode = "wb" if is_binary else "w"
    encoding = None if is_binary else "utf-8"

    f = open(tmp, mode, encoding=encoding)
    try:
        f.write(content)
        f.flush()
        if fsync:
            os.fsync(f.fileno())
    finally:
        f.close()
    os.replace(tmp, target_path)
