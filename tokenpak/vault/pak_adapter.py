# SPDX-License-Identifier: Apache-2.0
"""Vault → Pak adapter (Std 32 §1.3 row 3, §2.1, Phase 1).

Wraps the existing :class:`tokenpak.vault.retrieval.vault_index.VaultIndex`
to produce :class:`tokenpak.tip.pak.Pak` instances with subtype
:attr:`PakSubtype.VAULT`.

This is a **read-only** adapter — no writes to the vault, no daemon
contact, no encryption (Vault Paks are derived from user-controlled
project sources whose source-of-truth is the file on disk; per Std 32 §2.1
authority is ``file_source`` and retention is ``source_lifetime``).

Hard rule (Std 32 §7.1, §7.3): nothing in this module touches the
license-validation egress path; Pak content stays local. Privacy contract
tests in ``tests/tip/test_multipak_contracts.py`` assert this structurally.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from tokenpak.tip.pak import (
    Pak,
    PakAuthority,
    PakConfidence,
    PakRetentionPolicy,
    PakScope,
    PakSource,
    PakSourceType,
    PakStatus,
    PakSubtype,
    default_retention_for,
)

# Platform identifier on every Vault-Pak ``source.platform`` field.
# Constant rather than parameter — Vault Paks always come from the local
# vault subsystem; alternative platforms get their own adapters.
_PAK_PLATFORM = "tokenpak-vault"

# Map vault block ``file_type`` strings to the canonical PakSourceType enum.
# Per ``feedback_always_dynamic.md``: consult this table rather than hardcoding
# the mapping at call sites. ``"data"`` falls back to FILE; future vault file
# types added here in lockstep with ``vault.indexer``.
_FILE_TYPE_TO_SOURCE_TYPE = {
    "code": PakSourceType.CODE,
    "text": PakSourceType.FILE,
    "data": PakSourceType.FILE,
}


def _file_type_to_source_type(file_type: Optional[str]) -> PakSourceType:
    """Return the canonical PakSourceType for a vault ``file_type``.

    Unknown or missing values fall back to FILE — receivers must tolerate
    unrecognized source types per Std 31 §2 capability-codes rule.
    """
    if not file_type:
        return PakSourceType.FILE
    return _FILE_TYPE_TO_SOURCE_TYPE.get(file_type.lower(), PakSourceType.FILE)


def _infer_project(source_path: str) -> Optional[str]:
    """Best-effort project scope inference from a source path.

    Returns the first path segment past the user's home directory — for
    paths under ``/home/<user>/`` or ``/Users/<user>/`` this is the project
    name; for other paths the first content-bearing segment.

    Returns None when no segment qualifies — recall scoring treats None as
    "unscoped" rather than over-claiming a project. This is conservative
    by design: false positives in project_scope hard-filter on Std 32 §5.2.
    """
    if not source_path:
        return None
    parts = [p for p in Path(source_path).parts if p and p not in ("/", "\\", ".")]
    # Detect ``home/<user>/`` or ``Users/<user>/`` prefix (Linux/macOS) and
    # skip both the home anchor and the username segment. Linux's ``/home``
    # and macOS's ``/Users`` are filesystem conventions, not project scopes.
    if parts and parts[0].lower() in ("home", "users") and len(parts) >= 3:
        return parts[2]
    # Bare ``/home/foo`` or path not under a home root — return the first
    # non-anchor segment.
    if parts and parts[0].lower() in ("home", "users"):
        return parts[1] if len(parts) >= 2 else None
    return parts[0] if parts else None


def _iso8601_now() -> str:
    """ISO-8601 timestamp in UTC. Used as ``source.created_at`` fallback when
    block mtime is unavailable."""
    return datetime.now(timezone.utc).isoformat()


def _block_created_at(block_dict: dict) -> str:
    """Best-effort created-at derivation from the block's content file mtime.

    The vault's block dict carries ``_content_file`` (path to the on-disk
    content blob). When accessible, its mtime captures the indexing time
    (close enough to source-modification time for a Vault Pak's purposes).
    Falls back to "now" if the file is missing or unreadable.
    """
    cf = block_dict.get("_content_file")
    if cf:
        try:
            mtime = Path(cf).stat().st_mtime
            return datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat()
        except OSError:
            pass
    return _iso8601_now()


def _content_hash_from_block_id(block_id: str) -> str:
    """Vault block IDs are formatted as ``<path>#<content-hash[:8]>`` per
    ``vault.indexer``. Extract the hash suffix; fall back to the full block_id
    when no ``#`` is present (defensive, shouldn't happen in practice).

    Note: this is a SHA-256 prefix (8 hex chars), not the full digest. For
    a full-fidelity ``source.source_hash``, callers with access to the
    BlockRecord (write side) should pass ``content_hash=`` explicitly to
    :func:`vault_block_to_pak`. For retrieval-side adapters this prefix is
    sufficient as a stable identifier per the deterministic-ID guarantee.
    """
    if "#" in block_id:
        return block_id.split("#", 1)[1]
    return block_id


def _short_title(source_path: str, block_id: str) -> str:
    """Derive a human-readable title from the source path.

    Falls back to block_id when source_path is empty. The title is purely
    presentational — recall ranking does not consult it.
    """
    if source_path:
        return Path(source_path).name or source_path
    return block_id


def vault_block_to_pak(
    block_dict: dict,
    *,
    score: Optional[float] = None,
    summary: Optional[str] = None,
    content_hash: Optional[str] = None,
    confidence: Optional[PakConfidence] = None,
) -> Pak:
    """Convert a vault block dict (from :meth:`VaultIndex.search`) to a Pak.

    Required keys in ``block_dict`` (per
    ``tokenpak.vault.retrieval.vault_index.py:337-345``):
    - ``block_id``: str
    - ``source_path``: str (file path; falls back to block_id)
    - ``raw_tokens``: int (used in summary when ``summary`` not supplied)
    - ``_content_file``: str (internal — used for mtime-based created_at)

    Optional kwargs:
    - ``score``: BM25 score from ``VaultIndex.search``. Currently unused;
      reserved for confidence derivation in a future revision when the
      score-to-confidence mapping is calibrated.
    - ``summary``: human-readable summary; defaults to a token-count line.
    - ``content_hash``: full SHA-256 hex; defaults to the 8-char prefix
      embedded in block_id.
    - ``confidence``: explicit confidence override; defaults to MEDIUM.

    The returned Pak is frozen, JSON-serializable (via ``.to_dict()``), and
    structurally disjoint from license-payload field prefixes per Std 32 §7.1.
    """
    block_id = block_dict["block_id"]
    source_path = block_dict.get("source_path") or block_id
    raw_tokens = int(block_dict.get("raw_tokens") or 0)
    risk_class = block_dict.get("risk_class") or ""

    pak_id = f"vault:{block_id}"
    title = _short_title(source_path, block_id)

    if summary is None:
        # Default summary: indicates source + size. Real summaries come
        # from the daemon's compaction engine (Phase 5); Phase 1 ships a
        # diagnostic stub.
        summary = (
            f"Vault Pak from {source_path} ({raw_tokens} raw tokens, "
            f"risk_class={risk_class or 'unset'})"
        )

    source = PakSource(
        platform=_PAK_PLATFORM,
        source_type=_file_type_to_source_type(block_dict.get("file_type")),
        created_at=_block_created_at(block_dict),
        source_hash=content_hash or _content_hash_from_block_id(block_id),
    )

    scope = PakScope(project=_infer_project(source_path))

    return Pak(
        pak_id=pak_id,
        pak_type=PakSubtype.VAULT,
        title=title,
        summary=summary,
        scope=scope,
        source=source,
        status=PakStatus.PROPOSED,
        authority=PakAuthority.FILE_SOURCE,
        confidence=confidence or PakConfidence.MEDIUM,
        retention=PakRetentionPolicy(ttl=default_retention_for(PakSubtype.VAULT)),
    )


def search_as_paks(
    query: str,
    *,
    top_k: int = 5,
    min_score: float = 2.0,
    vault_index=None,
) -> list[Pak]:
    """Search the vault and return results as Vault Paks.

    Returned list is in the same order as :meth:`VaultIndex.search` —
    descending BM25 score, deterministic on ties. Empty list when no
    blocks meet ``min_score`` or no vault index is available.

    ``vault_index`` defaults to the module-level singleton accessor in
    :mod:`tokenpak.vault.search`; pass an explicit instance for tests or
    when consuming a non-default index.

    Per Std 32 §1.3 row 3 this is the read-only Phase 1 surface — no
    writes, no daemon contact, no Pak-store I/O. The Pro daemon's recall
    resolver (Phase 2) consumes these Paks alongside Interaction and
    Decision Paks for ranking.
    """
    if vault_index is None:
        # Lazy import to avoid pulling the vault subsystem into the module
        # graph for callers that only need the per-block conversion helper.
        try:
            from tokenpak.vault.search import get_vault_index  # type: ignore[import-not-found]
        except ImportError:
            return []
        try:
            vault_index = get_vault_index()
        except Exception:
            # Vault unavailable (config error, missing index, etc.) — empty
            # result is the correct UX per Std 32 §5.3 ("no relevant Paks →
            # level 0, empty list").
            return []

    if vault_index is None:
        return []

    try:
        results = vault_index.search(query, top_k=top_k, min_score=min_score)
    except Exception:
        return []

    return [vault_block_to_pak(block, score=score) for block, score in results]


__all__ = [
    "search_as_paks",
    "vault_block_to_pak",
]
