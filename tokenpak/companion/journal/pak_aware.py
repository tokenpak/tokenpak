# SPDX-License-Identifier: Apache-2.0
"""Pak-aware journal extension (Std 32 §1.3 row 4, §4.4, Phase 1).

Per Std 32 §4.4 the companion journal continues auto-capturing every
session unchanged — local-only, no upload. Promotion of a journal entry to
a MultiPak Interaction Pak is the **opt-in** step performed by the Pro
daemon. This module ships the OSS-side surface for that opt-in:

- Mark a journal entry as a promotion candidate (write side, opt-in).
- Query promotion candidates (read side, used by the Pro daemon).
- Build a stub :class:`Pak` (subtype INTERACTION) from a journal entry —
  PROPOSED status, derived authority. Daemon revises status + authority
  on actual promotion.

This module does **not** modify the existing :class:`JournalStore` API
or schema. All state lives in the existing ``entries.metadata_json``
TEXT column as additive keys (``is_promotion_candidate``,
``promoted_pak_id``).

Privacy: the marker is pure metadata; entry content stays local. License
egress (per Std 25 §4.4) never sees journal data.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

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

# Marker keys stored under entries.metadata_json. Constants rather than
# magic strings — daemon-side code reads these too and we want a single
# source of truth (per `feedback_always_dynamic.md`: enumerations get
# discovered, but stable identifiers in a metadata schema are fine).
KEY_IS_PROMOTION_CANDIDATE = "is_promotion_candidate"
KEY_PROMOTED_PAK_ID = "promoted_pak_id"

# Platform identifier for journal-derived Paks. Distinct from the
# vault adapter's "tokenpak-vault" so recall ranking can distinguish.
_PAK_PLATFORM = "tokenpak-companion-journal"

# Journal entry_type → PakAuthority. Per Std 32 §5.2 ranking model:
# user_approved > file_source > tool_result > llm_generated. Journal
# entries don't yet carry user-approval signal so we route generously to
# tool_result for milestones (concrete events) and llm_generated for the
# rest. Daemon-side promotion may upgrade to user_approved.
_AUTHORITY_BY_ENTRY_TYPE = {
    "milestone": PakAuthority.TOOL_RESULT,
    "user": PakAuthority.TOOL_RESULT,
    "auto": PakAuthority.LLM_GENERATED,
    "cost": PakAuthority.LLM_GENERATED,
    "capsule": PakAuthority.LLM_GENERATED,
    "companion_savings": PakAuthority.TOOL_RESULT,
}


@dataclass(frozen=True)
class JournalEntryRow:
    """Read-side view of an entries row that includes the row id.

    Distinct from :class:`tokenpak.companion.journal.store.JournalEntry`,
    which intentionally hides the SQL id from its public contract. The
    row id is needed here because it's the stable handle for marking
    promotion candidacy. Frozen so consumers can't mutate metadata
    in-place — write paths go through :func:`mark_promotion_candidate`.
    """

    entry_id: int
    session_id: str
    timestamp: float
    entry_type: str
    content: str
    metadata: dict[str, Any]


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open a connection against the journal DB. Caller closes."""
    return sqlite3.connect(str(db_path))


def mark_promotion_candidate(
    db_path: Path,
    entry_id: int,
    *,
    on: bool = True,
    promoted_pak_id: Optional[str] = None,
) -> bool:
    """Toggle the promotion-candidate marker on a journal entry.

    Returns True when the row was found and updated; False when no entry
    matches ``entry_id``. Idempotent — calling with ``on=True`` repeatedly
    leaves the row in the same state.

    When ``on`` is False, ``promoted_pak_id`` is ignored and the
    promoted-pak link is cleared as well (the entry is no longer a
    candidate, so any preceding promotion link is stale). When ``on`` is
    True and ``promoted_pak_id`` is provided, the link is recorded for
    daemon-side use — Phase 1 OSS code never sets this; it's the
    Pro-daemon write path.
    """
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT metadata_json FROM entries WHERE id = ?", (entry_id,)
        ).fetchone()
        if row is None:
            return False
        try:
            meta = json.loads(row[0] or "{}")
        except (json.JSONDecodeError, TypeError):
            meta = {}
        if on:
            meta[KEY_IS_PROMOTION_CANDIDATE] = True
            if promoted_pak_id is not None:
                meta[KEY_PROMOTED_PAK_ID] = promoted_pak_id
        else:
            meta[KEY_IS_PROMOTION_CANDIDATE] = False
            meta.pop(KEY_PROMOTED_PAK_ID, None)
        conn.execute(
            "UPDATE entries SET metadata_json = ? WHERE id = ?",
            (json.dumps(meta, default=str), entry_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_promotion_candidates(
    db_path: Path,
    *,
    session_id: Optional[str] = None,
    limit: int = 100,
) -> list[JournalEntryRow]:
    """List journal entries marked as promotion candidates.

    Filters by session when ``session_id`` is supplied; otherwise returns
    candidates across all sessions, newest first. Results capped at
    ``limit`` (default 100, matching the existing journal API convention).

    Used by the Pro daemon (Phase 2+) to enumerate entries it should
    consider for Interaction Pak promotion. Phase 1 OSS code only queries
    this surface for diagnostic/status output (``tokenpak pak status``);
    no automatic promotion happens in OSS.
    """
    conn = _connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        # SQLite has no JSON1 guarantee across distros, so we filter via
        # LIKE on the raw metadata_json text. The marker is a stable
        # JSON fragment (boolean true), so this is reliable.
        like_pattern = '%"is_promotion_candidate": true%'
        if session_id is None:
            rows = conn.execute(
                "SELECT id, session_id, timestamp, entry_type, content, metadata_json "
                "FROM entries WHERE metadata_json LIKE ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (like_pattern, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, session_id, timestamp, entry_type, content, metadata_json "
                "FROM entries WHERE session_id = ? AND metadata_json LIKE ? "
                "ORDER BY timestamp DESC LIMIT ?",
                (session_id, like_pattern, limit),
            ).fetchall()
    finally:
        conn.close()
    return [
        JournalEntryRow(
            entry_id=r["id"],
            session_id=r["session_id"],
            timestamp=r["timestamp"],
            entry_type=r["entry_type"],
            content=r["content"],
            metadata=_parse_metadata(r["metadata_json"]),
        )
        for r in rows
    ]


def count_promotion_candidates(
    db_path: Path, *, session_id: Optional[str] = None
) -> int:
    """Return the number of promotion-candidate entries.

    Cheap status-line query — used by ``tokenpak pak status``. No row
    materialization. Filters by session when supplied.
    """
    conn = _connect(db_path)
    try:
        like_pattern = '%"is_promotion_candidate": true%'
        if session_id is None:
            row = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE metadata_json LIKE ?",
                (like_pattern,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE session_id = ? AND metadata_json LIKE ?",
                (session_id, like_pattern),
            ).fetchone()
    finally:
        conn.close()
    return int(row[0]) if row else 0


def _parse_metadata(raw: Any) -> dict[str, Any]:
    """Parse the metadata_json column. Returns an empty dict on any error."""
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _authority_for_entry_type(entry_type: str) -> PakAuthority:
    """Return the canonical PakAuthority for a journal entry_type.

    Unknown entry types fall back to LLM_GENERATED (lowest authority).
    Per ``feedback_always_dynamic.md`` consumers must consult this
    function rather than hardcoding the mapping.
    """
    return _AUTHORITY_BY_ENTRY_TYPE.get(entry_type, PakAuthority.LLM_GENERATED)


def journal_entry_to_pak_stub(entry: JournalEntryRow) -> Pak:
    """Build an Interaction Pak stub from a journal entry.

    The result is a **stub** — status PROPOSED, no anchors, no
    relationships. The Pro daemon revises status to ACCEPTED on
    promotion and may add anchors hydrated from the original session
    state. Phase 1 OSS code uses this for diagnostic output (``tokenpak
    pak inspect``) and for daemon ingest preview.

    Per Std 32 §2.2 — Interaction Paks default to 180-day retention.
    """
    pak_id = f"journal:{entry.session_id}:{entry.entry_id}"
    title = f"Journal entry [{entry.entry_type}] @ {_iso_from_ts(entry.timestamp)}"
    summary = (
        entry.content[:240] + "…"
        if len(entry.content) > 240
        else entry.content
    ) or f"Empty journal entry (entry_type={entry.entry_type})"

    source = PakSource(
        platform=_PAK_PLATFORM,
        source_type=PakSourceType.LLM_RESPONSE,
        created_at=_iso_from_ts(entry.timestamp),
        # No content hash for journal entries — recall ranking uses
        # session_id + entry_id as the stable identifier. Empty string
        # is the documented "absent" value per the schema (validators
        # accept empty source_hash for ephemeral sources).
        source_hash="",
    )

    return Pak(
        pak_id=pak_id,
        pak_type=PakSubtype.INTERACTION,
        title=title,
        summary=summary,
        scope=PakScope(),  # journal entries are unscoped at OSS layer
        source=source,
        status=PakStatus.PROPOSED,
        authority=_authority_for_entry_type(entry.entry_type),
        confidence=PakConfidence.LOW,  # ungraded; daemon may upgrade
        retention=PakRetentionPolicy(
            ttl=default_retention_for(PakSubtype.INTERACTION)
        ),
    )


def _iso_from_ts(ts: float) -> str:
    """Format a unix timestamp as an ISO-8601 UTC string."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


__all__ = [
    "KEY_IS_PROMOTION_CANDIDATE",
    "KEY_PROMOTED_PAK_ID",
    "JournalEntryRow",
    "count_promotion_candidates",
    "journal_entry_to_pak_stub",
    "list_promotion_candidates",
    "mark_promotion_candidate",
]
